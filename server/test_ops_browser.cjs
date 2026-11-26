// With AUDIMATE_TEST_URL, read two credential lines from stdin. Otherwise
// start an isolated service and provision a temporary operator locally.
const assert = require("node:assert/strict");
const { execFileSync, spawn } = require("node:child_process");
const { once } = require("node:events");
const { mkdtemp, rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

async function run() {
  const folder = await mkdtemp(path.join(tmpdir(), "audimate-monitor-"));
  const python = process.env.AUDIMATE_PYTHON || "python";
  let url = process.env.AUDIMATE_TEST_URL, service, browser, host, listener, stream;
  async function post(client, route, data) {
    const response = await client.post(url + route, {data});
    assert.equal(response.status(), 200, route + " 返回 " + response.status());
    return response;
  }
  try {
    let credentials;
    if (url) {
      assert.equal(new URL(url).protocol, "https:");
      const input = [];
      for await (const chunk of process.stdin) input.push(chunk);
      credentials = Buffer.concat(input).toString("utf8");
    } else {
      const db = path.join(folder, "test.sqlite3");
      service = spawn(python, ["-m", "server.dev", "--port", "0", "--db", db], {windowsHide: true});
      const [line] = await once(service.stdout, "data");
      url = line.toString().trim();
      credentials = execFileSync(python, ["-m", "server.ops_admin", "--db", db, "create", "ops_test"],
                                 {encoding: "utf8", windowsHide: true});
    }
    url = url.replace(/\/$/, "");
    const name = /^Username: (.+)$/m.exec(credentials)?.[1].trim();
    const password = /^Password: (.+)$/m.exec(credentials)?.[1].trim();
    assert.ok(name && password, "需要运维测试账号");
    browser = await chromium.launch({executablePath: process.env.CHROME_BIN});
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", err => errors.push(err.message));
    const snapshot = async () => (await page.request.get(url + "/ops/api/overview")).json();
    await page.goto(url + "/ops/");
    await page.waitForSelector("#auth:visible");
    assert.equal((await page.request.get(url + "/ops/api/overview")).status(), 401);
    await page.fill("#name", name);
    await page.fill("#password", password);
    await page.click("#login button");
    await page.waitForSelector("#dashboard:visible");
    const before = await snapshot();
    assert.equal(before.members.length, 0, "需要空闲测试房间，已停止流量测试");

    // Two independent room sessions and a third, read-only operator session.
    host = (await browser.newContext({userAgent: "Audimate-Desktop/1"})).request;
    listener = (await browser.newContext()).request;
    for (const peer of [host, listener]) await post(peer, "/api/login", {name, password});
    const response = await post(host, "/api/stage/request", {source: "运维联调音源", rate: 48000, bits: 16});
    const state = await response.json();
    assert.ok(state.hosting);
    stream = state.host.stream;
    const media = "/api/audio?stream=" + stream;
    const pcm = Buffer.alloc(38400);
    for (let i = 0; i < pcm.length / 4; i++) pcm.writeInt16LE(Math.round(900 * Math.sin(i / 12)), 4 * i + 2);
    for (let i = 0; i < 3; i++) {
      await post(host, media, pcm);
      const data = await listener.get(url + media + "&after=" + i);
      assert.equal(data.status(), 200);
      assert.ok(pcm.equals(await data.body()));
    }
    if (service) {
      await post(listener,"/api/diagnostics",{stream,event:"play",transport:"pcm",playout_ms:642.5,clock_error_ms:8.2,buffer_ms:200});
      await page.waitForFunction(()=>document.querySelector("#p95").textContent.includes("643 ms"));
    }
    const current = await snapshot();
    assert.equal(current.members.length, 2);
    assert.equal(current.pulling, 1);
    assert.equal(current.host.sequence, 3);
    assert.equal(current.rx - before.rx, 115200);
    assert.equal(current.tx - before.tx, 115200);
    assert.equal(current.members.find(m => m.host).client, "Windows 客户端");
    await page.waitForFunction(() => document.querySelector("#source").textContent === "运维联调音源");
    assert.equal(await page.locator("#members tr").count(), 2);
    await page.click('[data-range="60"]');
    assert.equal(await page.locator('[data-range="60"]').getAttribute("aria-pressed"), "true");
    await page.selectOption("#filter", "主持");
    assert.ok((await page.locator("#log").textContent()).includes("上台"));
    if (process.env.AUDIMATE_SCREENSHOTS) await page.screenshot({path:path.join(process.env.AUDIMATE_SCREENSHOTS,"operations.png"),fullPage:true});
    await page.setViewportSize({width: 390, height: 760});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await post(host, "/api/stage/leave", {stream});
    stream = null;
    await page.waitForFunction(() => document.querySelector("#streamState").textContent === "空席");
    if (service) {
      page.on("dialog",dialog=>dialog.accept());
      await page.selectOption("#level","errors");
      await page.click("#saveLevel");
      await page.waitForFunction(()=>latest.service.active.logging==="errors");
      await page.click("#maintenance");
      await page.waitForFunction(()=>latest.service.maintenance);
      assert.equal((await host.get(url+"/api/room")).status(),401);
      await page.fill("#history","3"); await page.fill("#intervals","5, 15");
      await page.click("#config button");
      await page.waitForFunction(()=>latest.service.pending.history_s===3);
      assert.equal((await snapshot()).service.active.history_s,5);
      await page.click("#restart");
      await page.waitForFunction(()=>latest.service.active.history_s===3);
      await page.click("#open");
      await page.waitForFunction(()=>!latest.service.maintenance);
      await page.fill("#query",name); await page.click("#findAccount button");
      await page.waitForFunction(()=>document.querySelector("#accounts").options.length>0);
    }
    await page.click("#logout");
    await page.waitForSelector("#auth:visible");
    assert.equal((await page.request.get(url + "/ops/api/overview")).status(), 401);
    assert.deepEqual(errors, []);
    console.log("PASS: ops login, live member table, 115200 bytes in + out, chart range, event filter, mobile layout, stage release and logout");
  } finally {
    if (stream) await post(host, "/api/stage/leave", {stream}).catch(() => {});
    for (const peer of [host, listener]) if (peer) await peer.post(url + "/api/logout", {data: {}}).catch(() => {});
    if (browser) await browser.close();
    if (service) {
      const stopped = once(service, "exit");
      service.kill();
      await stopped;
    }
    await rm(folder, {recursive: true, force: true});
  }
}
run().catch(error => { console.error(error.message); process.exitCode = 1; });
