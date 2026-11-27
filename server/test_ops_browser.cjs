// With AUDIMATE_TEST_URL, read two credential lines from stdin. Otherwise
// start an isolated service and provision a temporary operator locally.
const assert = require("node:assert/strict");
const { execFileSync, spawn } = require("node:child_process");
const { once } = require("node:events");
const { createServer } = require("node:http");
const { mkdtemp, rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

async function run() {
  const folder = await mkdtemp(path.join(tmpdir(), "audimate-monitor-"));
  const python = process.env.AUDIMATE_PYTHON || "python";
  let url = process.env.AUDIMATE_TEST_URL, service, browser, host, listener, stream, exporter;
  let rtpRx=9000, rtpTx=15000;
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
      exporter=createServer((_,res)=>res.end('livekit_packet_bytes{direction="incoming"} '+rtpRx+'\n'+
        'livekit_packet_bytes{direction="outgoing"} '+rtpTx+'\n'));
      exporter.listen(0,"127.0.0.1");
      await once(exporter,"listening");
      service = spawn(python, ["-m", "server.dev", "--port", "0", "--db", db], {windowsHide: true,
        env:{...process.env,LIVEKIT_STATS_URL:"http://127.0.0.1:"+exporter.address().port+"/metrics"}});
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
      await post(host, media+(service ? "&packet="+(i+1) : ""), pcm);
      const data = await listener.get(url + media + "&after=" + i);
      assert.equal(data.status(), 200);
      assert.ok(pcm.equals(await data.body()));
    }
    if (service) {
      rtpRx+=8000; rtpTx+=16000;
      await post(listener,"/api/diagnostics",{stream,event:"play",transport:"pcm",playout_ms:642.5,clock_error_ms:8.2,buffer_ms:200,
        detail:{source_seq:1,server_seq:1,decode_ms:4.25,ingest_ms:5,uplink_est_ms:35,residence_ms:240,
          downlink_est_ms:25,play_queue_ms:200}});
      await page.waitForFunction(()=>document.querySelector("#p95").textContent.includes("643 ms"));
      await page.waitForFunction(()=>document.querySelector("#devTiming .stageRows")?.textContent.includes("PCM 解码"));
      await page.fill("#devTiming input", "1");
      assert.ok((await page.locator(".packetRows").textContent()).includes("PCM 解码"));
      await page.fill("#devTiming input", "999");
      await page.waitForSelector(".emptyTrace:visible");
      await page.fill("#devTiming input", "");
      await page.waitForFunction(()=>document.querySelector(".pieStatus")?.textContent.includes("1 个有效包"));
      await page.selectOption("#pieView","peer");
      assert.ok(await page.isVisible("#piePeer"));
      await page.selectOption("#pieView","slowest");
      assert.ok((await page.textContent(".pieStatus")).includes(name));
      await page.selectOption("#pieView","path");
      await page.selectOption("#piePath","rtc");
      assert.ok((await page.textContent(".pieStatus")).includes("样本不足"));
      await page.selectOption("#piePath","pcm");
      await page.selectOption("#pieWindow","60");
      await page.waitForFunction(()=>latest.audio_traffic.rx_bps!=null && latest.audio_traffic.rx===123200,
        null,{timeout:18000}).catch(async error=>{
          const s=await snapshot();
          throw Error(error.message+" "+JSON.stringify({audio:s.audio_traffic,meter:s.sfu,history:s.history.slice(-2)}));
        });
      assert.equal((await snapshot()).audio_traffic.tx,131200);
      await page.selectOption("#trafficCodec","opus");
      await page.selectOption("#trafficDirection","tx");
      assert.match(await page.textContent("#trafficLegend"),/^Opus 出站/);
      assert.equal(await page.locator("#trafficLegend span").count(),1);
      assert.match(await page.textContent("#rxTotal"),/120.3 KiB/);
      await page.selectOption("#trafficCodec","");
      await page.selectOption("#trafficDirection","");
      assert.equal(await page.locator("#trafficLegend span").count(),4);
      await listener.get(url+"/api/room?listen="+stream);
      await host.get(url+"/api/room");
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
      await page.selectOption("#filter", "识别");
      assert.ok(await page.isVisible("#emptyEvents"));
      if (process.env.AUDIMATE_RECOGNITION_WAV) {
        const wav = process.env.AUDIMATE_RECOGNITION_WAV;
        const extract = "import sys,wave; f=wave.open(sys.argv[1]); " +
          "assert (f.getnchannels(), f.getsampwidth()) == (2, 2); sys.stdout.buffer.write(f.readframes(5*f.getframerate()))";
        const pcm = execFileSync(python, ["-c", extract, wav], {maxBuffer: 4*1024*1024, windowsHide: true});
        const rate = pcm.length / (5*4);
        const stage = await post(host, "/api/stage/request", {source:"测试样本", rate, bits:16, recognize:true});
        stream = (await stage.json()).host.stream;
        await post(host, "/api/stage/track", {stream, track:{title:"I Will Survive", artist:"Gloria Gaynor"}});
        for (let pos=0; pos<pcm.length; pos+=rate*4/5) {
          await post(host, "/api/audio?stream="+stream, pcm.subarray(pos, pos+rate*4/5));
        }
        await page.waitForFunction(()=>document.querySelector("#log").textContent.includes("Quick Fetch"));
        const result = await page.textContent("#log");
        assert.match(result, /I Will Survive - Gloria Gaynor/);
        assert.match(result, /已确认 · Shazam ID 482734446 · Love Tracks · 1978/);
        assert.match(result, /200/);
        console.log(result);
        await page.selectOption("#filter", "异常");
        assert.ok(!(await page.textContent("#log")).includes("Shazam ID"));
        await post(host, "/api/stage/leave", {stream});
        stream = null;
      }
      page.on("dialog",dialog=>dialog.accept());
      assert.equal(await page.inputValue("#lease"), "20");
      assert.ok(await page.isEnabled("#lease"));
      await page.selectOption("#level","errors");
      await page.click("#saveLevel");
      await page.waitForFunction(()=>latest.service.active.logging==="errors");
      await page.fill("#history","3"); await page.fill("#intervals","5, 15");
      await page.fill("#lease","8");
      await page.fill("#recognitionInterval","9");
      await page.selectOption("#recognitionFuzzy","off");
      await page.fill("#titleMin","0.95");
      await page.fill("#artistMin","0.89");
      await page.click("#config button");
      await page.waitForFunction(()=>latest.service.pending.history_s===3 && latest.service.pending.lease_s===8);
      assert.equal((await snapshot()).service.active.history_s,3);
      assert.equal((await snapshot()).lease_s,8);
      assert.equal((await snapshot()).service.active.recognition_interval_s,9);
      const matching=(await snapshot()).service.active;
      assert.deepEqual([matching.recognition_fuzzy,matching.recognition_title_min,matching.recognition_artist_min],[false,0.95,0.89]);
      await page.click("#maintenance");
      await page.waitForFunction(()=>latest.service.maintenance);
      assert.equal((await host.get(url+"/api/room")).status(),401);
      await page.click("#restart");
      await page.waitForFunction(()=>latest.service.active.history_s===3 && latest.lease_s===8);
      await page.click("#open");
      await page.waitForFunction(()=>!latest.service.maintenance);
      assert.ok((await page.textContent("#activeConfig")).includes("成员离线超时 8 秒"));
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
    if (exporter) await new Promise(resolve=>exporter.close(resolve));
    await rm(folder, {recursive: true, force: true});
  }
}
run().catch(error => { console.error(error.stack); process.exitCode = 1; });
