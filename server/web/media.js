"use strict";
LivekitClient.setLogLevel("error");
const prefsKey = "audimate.listen.v1";
const autoSyncDelayMs = 1200;
let prefs = {auto: true, interval: 5, receive: "opus"};
try { Object.assign(prefs, JSON.parse(localStorage.getItem(prefsKey) || "{}")); } catch {}
function savePrefs() {
  try { localStorage.setItem(prefsKey, JSON.stringify(prefs)); } catch {}
}

let clockOffset = null, clockError = null;
async function calibrate() {
  const samples = [];
  for (let i = 0; i < 3; i++) {
    const start = Date.now(), stamp = await api("clock"), end = Date.now();
    samples.push({offset: stamp.unix_ms - (end + start) / 2, error: (end-start)/2});
  }
  const best = samples.sort((a,b) => a.error-b.error)[0];
  clockOffset = best.offset;
  clockError = best.error;
}

function diagnostics(host, event, transport, metrics = {}) {
  const level = room?.config?.logging;
  if (!level || level === "off" || level === "errors" && ["publish","play"].includes(event)) return;
  const sample = {stream: host.stream, event, transport, ...metrics};
  console.debug("Audimate", {...sample,stream:host.stream.slice(0,8)});
  api("diagnostics",sample).catch(() => {});
}

async function connectRTC(host, role, session) {
  const grant = await api("media", {stream: host.stream, role, clock_offset_ms:clockOffset, clock_error_ms:clockError});
  if (session.closed) return;
  await session.room.connect(grant.url, grant.token, {autoSubscribe: role !== "publish", maxRetries: 1});
  if (session.closed) await session.room.disconnect();
}

function rtcSession() {
  return {room: new LivekitClient.Room({disconnectOnPageLeave: true}), closed: false,
    close() { this.closed = true; clearInterval(this.timer); return this.room.disconnect().catch(() => {}); }};
}

async function rtcMetrics(track) {
  const stats = await track.getRTCStatsReport();
  const values = {};
  for (const row of stats?.values() || []) {
    if (row.type === "candidate-pair" && row.state === "succeeded" && row.currentRoundTripTime != null)
      values.rtt_ms = row.currentRoundTripTime * 1000;
    if (row.type === "inbound-rtp") {
      values.bytes = row.bytesReceived;
      values.sender_time_ms = row.estimatedPlayoutTimestamp;
      values.lost = row.packetsLost;
      if (row.jitter != null) values.jitter_ms = row.jitter * 1000;
      if (row.jitterBufferEmittedCount > 0) {
        const last = track.lastBufferStats || {delay: 0, count: 0};
        const count = row.jitterBufferEmittedCount - last.count;
        if (count > 0) values.buffer_ms = (row.jitterBufferDelay-last.delay)*1000/count;
        track.lastBufferStats = {delay: row.jitterBufferDelay, count: row.jitterBufferEmittedCount};
      }
    }
    if (row.type === "outbound-rtp") values.bytes = row.bytesSent;
    if (row.type === "remote-inbound-rtp" && row.roundTripTime != null) values.rtt_ms = row.roundTripTime*1000;
  }
  return values;
}

class Listening {
  constructor(ctx, host, transport) {
    this.ctx = ctx; this.host = host; this.transport = transport;
    this.gain = ctx.createGain(); this.gain.connect(ctx.destination);
    this.lastCheck = performance.now();
    this.reported = 0; this.underruns = 0;
    this.queued = new Set();
  }
  close() {
    this.closed = true;
    if (this.track) this.track.detach($("liveAudio"));
    this.rtc?.close();
    this.gain.disconnect();
    for (const src of this.queued) src.stop();
    this.queued.clear();
  }
  observe(metrics) {
    const now = performance.now(), lag = metrics.playout_ms ?? metrics.rtcp_lag_ms ?? metrics.timeline_ms ?? metrics.buffer_ms;
    this.lag = lag;
    if (metrics.playout_ms != null) $("latency").textContent = "预计播放延迟 " + Math.round(metrics.playout_ms) + " ms";
    else if (metrics.rtcp_lag_ms!=null) $("latency").textContent="RTCP 时差估计 "+Math.round(metrics.rtcp_lag_ms)+" ms";
    else if (metrics.timeline_ms!=null) $("latency").textContent="距缓存实时位置 "+Math.round(metrics.timeline_ms)+" ms";
    else $("latency").textContent = metrics.buffer_ms == null ? "等待延迟样本" : "接收缓冲 " + Math.round(metrics.buffer_ms) + " ms";
    if (lag > 1000) $("latency").textContent += " · 延迟偏高，可手动同步";
    if (now-this.reported >= 2000) {
      diagnostics(this.host, "play", this.transport, metrics);
      this.reported = now;
    }
    if (lag >= room.config.history_s * 1000 || prefs.auto && lag > autoSyncDelayMs && now-this.lastCheck >= prefs.interval*1000) {
      this.lastCheck = now;
      return true;
    }
    return false;
  }
  async runRTC() {
    const sess = this.rtc = rtcSession();
    let track;
    sess.room.on(LivekitClient.RoomEvent.TrackSubscribed, t => {
      if (this.closed || t.kind !== "audio") return;
      track = t;
      const receiver = t.receiver;
      if (receiver && "jitterBufferTarget" in receiver) receiver.jitterBufferTarget = 600;
      this.track = t;
      t.attach($("liveAudio"));
      $("liveAudio").play().catch(()=>note("浏览器暂停了音轨，请重新点击收听。"));
    });
    await connectRTC(this.host, "listen", sess);
    const start = performance.now();
    while (!this.closed) {
      if (track) {
        const metrics = await rtcMetrics(track);
        if (this.closed) return;
        const source = room?.host;
        if (source?.codec==="opus" && Number.isFinite(source.clock_offset_ms) && Number.isFinite(clockOffset)) {
          const raw = metrics.sender_time_ms;
          // RTCP sender reports use NTP. Some implementations already expose Unix milliseconds.
          const sent = raw > 3e12 ? raw-2208988800000 : raw;
          const lag=Date.now()+clockOffset-(sent+source.clock_offset_ms);
          if (Number.isFinite(lag) && lag>=0 && lag<60000) {
            metrics.rtcp_lag_ms=lag;
            metrics.clock_error_ms=clockError+source.clock_error_ms;
          }
        }
        delete metrics.sender_time_ms;
        if (this.observe(metrics)) { refreshPlayback(true); return; }
      } else if (performance.now()-start > 10000) throw Error("等待 LiveKit 音轨超时");
      if (sess.room.state === "disconnected") throw Error("LiveKit 连接中断");
      await sleep(1000);
    }
  }
  async runPCM() {
    const h = this.host, ctx = this.ctx;
    const rate = h.codec === "opus" ? 48000 : h.rate;
    const bits = h.codec === "opus" ? 16 : h.bits;
    let after = -1, next = 0;
    while (!this.closed) {
      if (next > ctx.currentTime + 0.72) { await sleep(30); continue; }
      const response = await fetch("/api/audio?stream="+h.stream+"&after="+after+(after < 0 ? "&sync=1" : ""), {cache:"no-store"});
      if (this.closed) return;
      if (response.status === 204) { await sleep(40); continue; }
      if (!response.ok) throw Error("音源已结束或连接中断");
      const view = new DataView(await response.arrayBuffer());
      if (this.closed) return;
      const seq = Number(response.headers.get("X-Sequence"));
      const gap = after >= 0 && seq !== after + 1;
      if (gap) diagnostics(h, "gap", "pcm", {discarded: Math.max(0,seq-after-1)});
      after = seq;
      const width = bits/8, buffer = ctx.createBuffer(2, view.byteLength/(width*2),rate);
      for (let channel=0; channel<2; channel++) {
        const samples = buffer.getChannelData(channel);
        for (let i=0; i<samples.length; i++) {
          const j=(i*2+channel)*width;
          const value=width===2 ? view.getInt16(j,true) : width===4 ? view.getInt32(j,true) :
            view.getUint16(j,true) | (view.getInt8(j+2)<<16);
          samples[i]=value / 2**(bits-1);
        }
      }
      const captured = response.headers.get("X-Captured-Ms");
      const age = captured && clockOffset != null ? Date.now()+clockOffset-Number(captured) : null;
      const mediaAge = Number(response.headers.get("X-Behind-Ms"));
      if (response.headers.get("X-Reset") === "1") {
        for (const src of this.queued) src.stop();
        this.queued.clear(); next=0;
      }
      if (next < ctx.currentTime) {
        if (next) this.underruns++;
        next=ctx.currentTime+Math.max(0.025,(600-(age ?? mediaAge))/1000);
      }
      const metrics = {buffer_ms: (next-ctx.currentTime)*1000, underruns:this.underruns};
      metrics.timeline_ms=mediaAge+metrics.buffer_ms;
      if (age != null) Object.assign(metrics,{playout_ms:Math.max(0,age+metrics.buffer_ms), clock_error_ms:clockError});
      if (this.observe(metrics) && this.queued.size) { refreshPlayback(true); return; }
      const source=ctx.createBufferSource(); source.buffer=buffer; source.connect(this.gain);
      this.queued.add(source); source.onended=()=>this.queued.delete(source);
      source.start(next); next+=buffer.duration;
    }
  }
}
