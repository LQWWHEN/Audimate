// Run with Node, Playwright and a Chromium installation. Uses a temporary DB.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { mkdtemp, rm } = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { once } = require("node:events");
const { randomBytes } = require("node:crypto");

async function account(page, url, user, password, register) {
  await page.goto(url);
  if (register) await page.click("#registerTab");
  await page.fill("#name", user);
  await page.fill("#password", password);
  await page.click("#submit");
  await page.waitForSelector("#room:visible");
}

// Companion to the Windows GUI test. Confirm the selected right-only process
// reaches Web Audio using the same account first registered from the desktop.
async function listenToDesktop(page, url) {
  await account(
    page,
    url,
    process.env.AUDIMATE_PEER_NAME,
    process.env.AUDIMATE_PEER_PASSWORD,
    false,
  );
  await page.evaluate(() => {
    const start = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args) {
      const left = this.buffer.getChannelData(0);
      const right = this.buffer.getChannelData(1);
      if (
        this.buffer.sampleRate === 48000 &&
        right.some((x) => Math.abs(x) > 0.002) &&
        left.every((x) => Math.abs(x) < 0.00025)
      )
        window.receivedDesktopTone = true;
      return start.apply(this, args);
    };
  });
  await page.click("#listen");
  await page.waitForFunction(() => window.receivedDesktopTone, null, {
    timeout: 20000,
  });
  await page.click("#logout");
  await page.waitForSelector("#auth:visible");
  console.log(
    "PASS: desktop account login and right-channel desktop audio reached browser playback",
  );
}

async function run() {
  const dir = await mkdtemp(path.join(os.tmpdir(), "audimate-e2e-"));
  let url = process.env.AUDIMATE_TEST_URL;
  const service = url
    ? null
    : spawn(
        process.env.AUDIMATE_PYTHON || "python",
        [
          "-m",
          "server.dev",
          "--port",
          "0",
          "--db",
          path.join(dir, "accounts.db"),
        ],
        { windowsHide: true },
      );
  let browser;
  try {
    if (service) {
      const [line] = await once(service.stdout, "data");
      url = line.toString().trim();
    }
    browser = await chromium.launch({
      executablePath: process.env.CHROME_BIN,
      args: ["--mute-audio"],
    });
    const host = await browser.newPage();
    if (process.argv.includes("--desktop-listener")) {
      await listenToDesktop(host, url);
      return;
    }
    const listener = await browser.newPage({
      viewport: { width: 390, height: 760 },
    });
    const errors = [];
    for (const page of [host, listener])
      page.on("pageerror", (e) => errors.push(e.message));
    const suffix = randomBytes(6).toString("hex");
    const password = randomBytes(24).toString("hex");
    await account(host, url, "webQA_" + suffix, password, true);
    await host.click("#logout");
    await host.waitForSelector("#auth:visible");
    await account(host, url, "webQA_" + suffix, password, false);
    await account(listener, url, "listenerQA_" + suffix, password, true);
    // Only the browser chooser is replaced. The real Worklet, HTTP PCM upload,
    // second browser's read, PCM decoder and scheduled playback all run.
    for (const page of [host, listener])
      await page.evaluate(() => {
        navigator.mediaDevices.getDisplayMedia = async () => {
          const ctx = new AudioContext(),
            osc = ctx.createOscillator(),
            dest = ctx.createMediaStreamDestination();
          osc.frequency.value = 700;
          osc.connect(dest);
          osc.start();
          await ctx.resume();
          const canvas = document.createElement("canvas");
          canvas.width = canvas.height = 8;
          const video = canvas.captureStream().getVideoTracks()[0];
          video.getSettings = () => ({ displaySurface: "browser" });
          return new MediaStream([video, ...dest.stream.getAudioTracks()]);
        };
      });
    await host.selectOption("#quality", "96000/24");
    assert.equal(
      await host.evaluate(
        async () => (await (await fetch("/api/room")).json()).host,
      ),
      null,
      "测试需要空闲房间",
    );
    await host.click("#stage");
    await host.waitForFunction(
      () => document.querySelector("#stage").textContent === "下台",
    );
    await listener.waitForFunction(
      () => !document.querySelector("#listen").disabled,
    );
    await listener.evaluate(() => {
      const start = AudioBufferSourceNode.prototype.start;
      window.samplesReceived = 0;
      AudioBufferSourceNode.prototype.start = function (...args) {
        if (
          this.buffer?.sampleRate === 96000 &&
          this.buffer.length === 19200 &&
          this.buffer.getChannelData(0).some((x) => Math.abs(x) > 0.001)
        )
          window.samplesReceived++;
        return start.apply(this, args);
      };
    });
    await listener.click("#listen");
    await listener.waitForFunction(() => window.samplesReceived > 0, null, {
      timeout: 15000,
    });
    assert.match(await listener.textContent("#hostInfo"), /96 kHz · 24-bit/);
    await listener.click("#stage");
    await host.getByRole("button", { name: "拒绝" }).click();
    await listener.waitForFunction(
      () => document.querySelector("#stage").textContent === "申请主持",
    );
    await listener.selectOption("#quality", "44100/16");
    await listener.click("#stage");
    await host.getByRole("button", { name: "接受" }).click();
    await listener.waitForFunction(
      () => document.querySelector("#stage").textContent === "下台",
    );
    await host.waitForFunction(() =>
      document.querySelector("#hostInfo").textContent.includes("44.1 kHz"),
    );
    await listener.click("#stage");
    await listener.waitForFunction(
      () => document.querySelector("#source").textContent === "等待主持上台",
    );
    assert.equal(
      await listener.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    assert.deepEqual(errors, []);
    for (const page of [host, listener]) {
      await page.click("#logout");
      await page.waitForSelector("#auth:visible");
    }
    console.log(
      "PASS: browser register/login/logout, tab audio upload, live playback, host reject/accept/handoff, step-down, mobile width",
    );
  } finally {
    if (browser) await browser.close();
    if (service) {
      const exited = once(service, "exit");
      service.kill();
      await exited;
    }
    await rm(dir, { recursive: true, force: true });
  }
}
run().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
