// Run with Node, Playwright and a Chromium installation. Uses a temporary DB.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { mkdtemp, rm, writeFile } = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { once } = require("node:events");
const { randomBytes } = require("node:crypto");
const { startWindowPlayers, verifyWindowAudio } = require("./test_window_capture.cjs");

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
  if (process.env.AUDIMATE_PEER_TRACK)
    await page.waitForFunction(text => document.querySelector("#source").textContent === text, process.env.AUDIMATE_PEER_TRACK);
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
          ...(process.argv.includes("--release") ? ["--release"] : []),
        ],
        { windowsHide: true },
      );
  let browser, windowPlayers, bridgeErrors="";
  service?.stderr.on("data",chunk=>{
    for(const line of chunk.toString().split("\n")) if (/Exception|Error:|bridge failed|RuntimeError|Traceback|rtc_audio.py|media.py|ffi_client.py/i.test(line))
      bridgeErrors+=(line.split("?")[0].replace(/[a-zA-Z0-9_-]{35,}/g,"[redacted]")+"\n").slice(0,240);
  });
  try {
    if (service) {
      const [line] = await once(service.stdout, "data");
      url = line.toString().trim();
    }
    if (process.argv.includes("--native-window")) windowPlayers = await startWindowPlayers(dir);
    browser = await chromium.launch({
      headless: !windowPlayers,
      executablePath: process.env.CHROME_BIN,
      args: ["--mute-audio","--allow-loopback-in-peer-connection", ...(windowPlayers?.args || [])],
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
    const samples = [];
    for (const page of [host, listener])
      page.on("pageerror", (e) => errors.push(e.message));
    for (const page of [host, listener]) page.on("request", request => {
      if (new URL(request.url()).pathname === "/api/diagnostics") samples.push(request.postDataJSON());
    });
    const suffix = randomBytes(6).toString("hex");
    const password = randomBytes(24).toString("hex");
    await account(host, url, "webQA_" + suffix, password, true);
    await host.click("#logout");
    await host.waitForSelector("#auth:visible");
    await account(host, url, "webQA_" + suffix, password, false);
    await account(listener, url, "listenerQA_" + suffix, password, true);
    await listener.waitForFunction(() => room?.config);
    const syncChecks = await listener.evaluate(() => {
      const original = {auto: prefs.auto, interval: prefs.interval, history: room.config.history_s};
      const check = new Listening(new OfflineAudioContext(2, 128, 48000), {}, "pcm");
      const sample = lag => check.observe({playout_ms: lag});
      check.reported = performance.now();
      try {
        prefs.auto = true; prefs.interval = 5; room.config.history_s = 5;
        check.lastCheck -= 10000;
        const results = [sample(1135), $("latency").textContent.includes("延迟偏高"), sample(1200),
                         sample(1201), sample(1201)];
        prefs.auto = false; check.lastCheck -= 10000;
        results.push(sample(1201));
        room.config.history_s = 1;
        results.push(sample(1000));
        return results;
      } finally {
        prefs.auto = original.auto; prefs.interval = original.interval; room.config.history_s = original.history;
        check.close();
      }
    });
    assert.deepEqual(syncChecks, [false, true, false, true, false, false, true]);
    const originalPicker = windowPlayers && await host.evaluateHandle(() => navigator.mediaDevices.getDisplayMedia);
    // Simulate chooser outcomes, then use the native picker for the host when requested.
    for (const page of [host, listener])
      await page.evaluate(() => {
        window.selection = {surface: "browser", label: "Tab audio"};
        navigator.mediaDevices.getDisplayMedia = async (options) => {
          window.captureOptions = options;
          const {surface, label} = window.selection;
          const canvas = document.createElement("canvas");
          canvas.width = canvas.height = 8;
          const video = canvas.captureStream().getVideoTracks()[0];
          video.getSettings = () => ({ displaySurface: surface });
          const stream = window.lastSelection = new MediaStream([video]);
          if (!label) return stream;
          const ctx = new AudioContext(),
            osc = ctx.createOscillator(),
            dest = ctx.createMediaStreamDestination();
          osc.frequency.value = 700;
          osc.connect(dest);
          osc.start();
          await ctx.resume();
          const audio = dest.stream.getAudioTracks()[0];
          Object.defineProperty(audio, "label", {value: label});
          stream.addTrack(audio);
          return stream;
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
    let requests = 0;
    host.on("request", r => { if (new URL(r.url()).pathname === "/api/stage/request") requests++; });
    for (const [surface, label, message] of [
      ["window", "", "窗口没有音轨"], ["browser", "", "标签页没有音轨"],
      ["window", "System Audio", "整个系统的声音"], ["browser", "System Audio", "整个系统的声音"],
      ["window", "Unknown", "无法确认音轨"],
      ["monitor", "System Audio", "请选择应用窗口"], ["", "Application Audio", "请选择应用窗口"],
    ]) {
      await host.evaluate(s => window.selection = s, {surface, label});
      await host.click("#stage");
      await host.waitForFunction(m => document.querySelector("#notice").textContent.includes(m) &&
        window.lastSelection.getTracks().every(t => t.readyState === "ended"), message);
      assert.equal(requests, 0);
    }
    assert.deepEqual(await host.evaluate(() => [
      captureOptions.audio, captureOptions.audioSelection, captureOptions.windowAudio,
      captureOptions.systemAudio, captureOptions.monitorTypeSurfaces
    ]), [{echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: {ideal: 2}},
      "preferred", "window", "exclude", "exclude"]);
    if (windowPlayers) {
      await host.evaluate(picker => navigator.mediaDevices.getDisplayMedia = picker, originalPicker);
      await host.bringToFront();
    } else {
      await host.evaluate(() => window.selection = {surface: "window", label: "Application Audio"});
    }
    await host.click("#stage");
    await host.waitForFunction(
      () => document.querySelector("#stage").textContent === "下台",
    );
    assert.ok(await host.isVisible("#stopSourceHelp"));
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
      if (windowPlayers) await verifyWindowAudio(listener, "opus");
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
      if (service) {
        const development = !process.argv.includes("--release");
        const flag = await host.evaluate(() => room.config.dev_timing === true);
        assert.equal(flag, development);
        const timeout = Date.now()+8000;
        while (!samples.some(s=>s.event==="publish") || !samples.some(s=>s.event==="play")) {
          assert.ok(Date.now()<timeout,"缺少浏览器主持或播放诊断样本");
          await new Promise(resolve=>setTimeout(resolve,100));
        }
        if (development) {
          assert.ok(samples.some(s=>s.event==="publish" && s.detail?.queue_ms>=0 && s.detail.server_seq>0));
          assert.ok(samples.some(s=>s.event==="play" && s.detail?.decode_ms>=0 && s.detail.source_seq>0));
        } else {
          assert.ok(samples.every(s=>!("detail" in s)));
        }
      }
    }
    assert.match(await listener.textContent("#hostInfo"), /96 kHz · 24-bit/);
    assert.equal(await listener.textContent("#source"), "应用窗口");
    if (windowPlayers) {
      await windowPlayers.setMinimized(true);
      await listener.evaluate(() => window.samplesReceived = 0);
      await listener.waitForFunction(() => window.samplesReceived >= 10);
    }
    // The server has removed the host, but its HTTP acknowledgement is still in flight.
    // A room poll must not offer a new picker that the old leave response can tear down.
    let releaseLeave;
    const holdLeave = new Promise(resolve => { releaseLeave = resolve; });
    let serverLeft;
    const left = new Promise(resolve => { serverLeft = resolve; });
    await host.route("**/api/stage/leave", async route => {
      const response = await route.fetch();
      serverLeft();
      await holdLeave;
      await route.fulfill({response});
    }, {times: 1});
    const previousStream = await host.evaluate(() => {
      window.oldCaptureTracks = input.getTracks();
      if (input.getVideoTracks()[0].readyState !== "ended" || input.getAudioTracks()[0].readyState !== "live")
        throw Error("窗口主持应只保留应用音轨");
      return room.host.stream;
    });
    if (windowPlayers) await host.click("#stage");
    else await host.evaluate(() => {
      const audio = input.getAudioTracks()[0];
      audio.stop();
      audio.dispatchEvent(new Event("ended")); // Browser's Stop Sharing notification.
    });
    try {
      await left;
      await host.waitForResponse(r => new URL(r.url()).pathname === "/api/room" && r.ok());
      assert.equal(await host.locator("#stage").isDisabled(), true, "下台未完成时不能重开选择器");
      assert.ok(await host.evaluate(() => oldCaptureTracks.every(t => t.readyState === "ended")));
    } finally {
      releaseLeave();
    }
    await host.waitForFunction(() => !room.hosting && !document.querySelector("#stage").disabled);
    assert.ok(await host.isVisible("#chooseSourceHelp"));
    await host.click("#stage");
    if (windowPlayers) {
      // A minimized window can be absent from the native picker. Keep it open
      // while restoring the same process and allow Chrome to update its list.
      await host.waitForResponse(r => new URL(r.url()).pathname === "/api/room" && r.ok());
      console.log("Native picker before restore:", await host.evaluate(() => room.hosting ? "selected minimized window" : "awaiting selection"));
      await windowPlayers.setMinimized(false);
    }
    await host.waitForFunction(previous => room.hosting && room.host.stream !== previous, previousStream);
    await listener.waitForFunction(id => playback?.host.stream === id,
      await host.evaluate(() => room.host.stream));
    await listener.evaluate(() => window.samplesReceived = 0);
    await listener.waitForFunction(() => window.samplesReceived > 0);
    if (windowPlayers) await verifyWindowAudio(listener, "pcm", windowPlayers);
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
    assert.equal(await host.textContent("#source"), "浏览器标签页");
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
        "PASS: chooser constraints, rejected sources released, window audio upload and playback, tab handoff, mobile width",
    );
  } finally {
    if (sfu) sfu.kill();
    if (browser) await browser.close();
    if (windowPlayers) await windowPlayers.close();
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
