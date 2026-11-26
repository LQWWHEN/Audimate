// Run with Node, Playwright and a Chromium installation. Uses a temporary DB.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { mkdtemp, rm, writeFile } = require("node:fs/promises");
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
  await page.evaluate(()=>{
    const audio=document.querySelector("#liveAudio");
    audio.addEventListener("playing",()=>{
      const source=listenContext.createMediaStreamSource(audio.srcObject), split=listenContext.createChannelSplitter(2);
      const left=listenContext.createAnalyser(),right=listenContext.createAnalyser();
      source.connect(split);split.connect(left,0);split.connect(right,1);
      window.toneGraph={source,split,left,right};
    });
  });
  await page.click("#listen");
  await page.waitForFunction(() => {
    if(window.receivedDesktopTone)return true;
    if(!window.toneGraph)return false;
    const {left,right}=window.toneGraph,l=new Float32Array(left.fftSize),r=new Float32Array(right.fftSize);
    left.getFloatTimeDomainData(l);right.getFloatTimeDomainData(r);
    return l.every(x=>Math.abs(x)<.003) && r.some(x=>Math.abs(x)>.015);
  }, null, {
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
  const useRTC = process.argv.includes("--livekit");
  let sfu;
  if (useRTC && !url && !process.env.LIVEKIT_URL) {
    const key=randomBytes(10).toString("hex"), secret=randomBytes(32).toString("hex");
    const file=path.join(dir,"livekit.yaml");
    const lan=Object.values(os.networkInterfaces()).flat().find(x=>x.family==="IPv4"&&!x.internal)?.address || "127.0.0.1";
    await writeFile(file, "port: 18880\nbind_addresses: [127.0.0.1]\nrtc:\n  node_ip: "+lan+"\n  advertise_internal_ip: true\n  enable_loopback_candidate: true\n  tcp_port: 18881\n  udp_port: 18882\nkeys:\n  "+key+": "+secret+"\n");
    sfu=spawn(process.env.LIVEKIT_BIN||"livekit-server",["--config",file],{windowsHide:true,stdio:"ignore"});
    Object.assign(process.env,{LIVEKIT_API_KEY:key,LIVEKIT_API_SECRET:secret,LIVEKIT_URL:"ws://127.0.0.1:18880",LIVEKIT_INTERNAL_URL:"http://127.0.0.1:18880"});
  }
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
  let browser, bridgeErrors="";
  service?.stderr.on("data",chunk=>{
    for(const line of chunk.toString().split("\n")) if (/Exception|Error:|bridge failed|RuntimeError|Traceback|rtc_audio.py|media.py|ffi_client.py/i.test(line))
      bridgeErrors+=(line.split("?")[0].replace(/[a-zA-Z0-9_-]{35,}/g,"[redacted]")+"\n").slice(0,240);
  });
  try {
    if (service) {
      const [line] = await once(service.stdout, "data");
      url = line.toString().trim();
    }
    browser = await chromium.launch({
      executablePath: process.env.CHROME_BIN,
      args: ["--mute-audio","--allow-loopback-in-peer-connection"],
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
    if (useRTC) {
      // Observe decoded signal at the Web Audio output, independently of SDK events.
      await listener.evaluate(()=>{
        const audio=document.querySelector("#liveAudio");
        audio.addEventListener("playing", ()=>{
          const source=listenContext.createMediaStreamSource(audio.srcObject);
          const analyser=listenContext.createAnalyser();
          source.connect(analyser); window.decodedAnalyser=analyser; window.decodedSource=source;
        });
      });
      await listener.click("#listen");
    } else {
      await host.selectOption("#codec","pcm");
      await listener.selectOption("#receive","pcm");
    }
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
    if (useRTC) {
      const report=listener.waitForResponse(async response=>{
        if(!response.url().endsWith("/api/diagnostics")||response.status()!==200)return false;
        const data=response.request().postDataJSON();return data.transport==="rtc" && data.event==="play" && data.bytes>0;
      },{timeout:30000});
      report.catch(()=>{});
      const hasTone=()=>{
        const a=window.decodedAnalyser;if(!a)return false;
        const v=new Float32Array(a.fftSize);a.getFloatTimeDomainData(v);
        return v.some(x=>Math.abs(x)>.001) && document.querySelector("#actual").textContent.includes("LiveKit");
      };
      try { await listener.waitForFunction(hasTone,null,{timeout:35000}); }
      catch(e) {
        const detail=await listener.evaluate(async()=>({stats:playback?.track?await rtcMetrics(playback.track):null, muted:$("liveAudio").muted, paused:$("liveAudio").paused, ctx:listenContext?.state,live:playback?.rtc?.room.state,
          remotes:playback?.rtc?.room.remoteParticipants.size, analyser:!!window.decodedAnalyser, notice:$("notice").textContent}));
        const sender=await host.evaluate(async()=>({stats:hostRTC?.room.localParticipant.audioTrackPublications.size?await rtcMetrics([...hostRTC.room.localParticipant.audioTrackPublications.values()][0].track):null, live:hostRTC?.room.state,published:hostRTC?.room.localParticipant.trackPublications.size,
          host:room?.hosting,notice:$("notice").textContent}));
        throw Error("RTC playback "+JSON.stringify({detail,sender,errors}));
      }
      await report;
      if (process.env.AUDIMATE_TEST_URL) await listener.evaluate(async()=>{
        const grant=await api("media",{stream:room.host.stream,role:"listen"});
        window.testOldGrant=grant.token;
        const result=await fetch("/rtc/validate?access_token="+encodeURIComponent(grant.token));
        if (!result.ok) throw Error("当前媒体令牌验证失败 "+result.status);
      });
      await host.click("#stage");
      await listener.waitForFunction(()=>document.querySelector("#actual").textContent.includes("等待下一位"));
      if (process.env.AUDIMATE_TEST_URL) assert.equal(await listener.evaluate(async()=>{
        const r=await fetch("/rtc/validate?access_token="+encodeURIComponent(window.testOldGrant));
        delete window.testOldGrant;
        return r.status;
      }),403,"下台后旧令牌必须失效");
      await host.selectOption("#codec","pcm");
      await listener.evaluate(()=>window.decodedAnalyser=null);
      await host.click("#stage");
      try {await listener.waitForFunction(hasTone,null,{timeout:25000});} catch {
        const state=await listener.evaluate(()=>({actual:$("actual").textContent,notice:$("notice").textContent,remote:playback?.rtc?.room.remoteParticipants.size,track:!!playback?.track}));
        throw Error("PCM bridge "+JSON.stringify(state)+" "+bridgeErrors.slice(-1600));
      }
      await listener.selectOption("#receive","pcm");
      await listener.waitForFunction(()=>window.samplesReceived>0,null,{timeout:10000});
    } else {
      await listener.click("#listen");
      await listener.waitForFunction(() => window.samplesReceived > 0, null, {timeout:15000});
    }
    assert.match(await listener.textContent("#hostInfo"), /96 kHz · 24-bit/);
    if (process.env.AUDIMATE_SCREENSHOTS) {
      await host.screenshot({path:path.join(process.env.AUDIMATE_SCREENSHOTS,"room.png"),fullPage:true});
      await listener.screenshot({path:path.join(process.env.AUDIMATE_SCREENSHOTS,"listener-mobile.png"),fullPage:true});
    }
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
      useRTC ? "PASS: audible native Opus, shared PCM-to-Opus, lossless reception, RTC telemetry and persistent subscription across hosts" :
        "PASS: browser register/login/logout, tab audio upload, live playback, host reject/accept/handoff, step-down, mobile width",
    );
  } finally {
    if (sfu) sfu.kill();
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
