"use strict";
const $ = (id) => document.getElementById(id);
const note = (text) => {
  $("notice").textContent = text;
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let mode = "login",
  name = "",
  room = null,
  epoch = 0,
  timer,
  input,
  captureContext,
  publisher,
  listenContext;
let requesting = false,
  stageGeneration = 0,
  changes = 0;

async function api(path, data) {
  const response = await fetch(
    "/api/" + path,
    data === undefined
      ? { cache: "no-store" }
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        },
  );
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && name) {signedOut(); note(result.error || "请重新登录");}
    throw Error(result.error || "连接失败");
  }
  return result;
}
function choose(next) {
  mode = next;
  $("loginTab").setAttribute("aria-pressed", next === "login");
  $("registerTab").setAttribute("aria-pressed", next === "register");
  $("password").autocomplete =
    next === "login" ? "current-password" : "new-password";
  $("submit").textContent = next === "login" ? "登录" : "注册并进入";
  $("authTitle").textContent = next === "login" ? "登录音乐房间" : "创建账号";
  note("");
}
$("loginTab").onclick = () => choose("login");
$("registerTab").onclick = () => choose("register");
$("authform").onsubmit = async (event) => {
  event.preventDefault();
  $("submit").disabled = true;
  try {
    enter(
      (
        await api(mode, {
          name: $("name").value.trim(),
          password: $("password").value,
        })
      ).name,
    );
  } catch (e) {
    note(e.message);
  } finally {
    $("submit").disabled = false;
  }
};
function enter(user) {
  name = user;
  epoch++;
  $("username").textContent = user;
  $("password").value = "";
  $("auth").hidden = true;
  $("room").hidden = $("account").hidden = false;
  note("");
  firstRoom = true;
  clockOffset = null;
  calibrate().catch(() => {});
  poll(epoch);
}
let listening = false, playback, hostRTC, fallbackStream = "", firstRoom = true;
function clearPlayback() {
  playback?.close();
  playback = null;
}
function stopListen() {
  listening = false;
  clearPlayback();
  if (listenContext) listenContext.close().catch(() => {});
  listenContext = null;
  $("listen").textContent = "收听";
  $("latency").textContent = "";
}
function refreshPlayback(force=false) {
  const host = room?.host;
  const transport = room?.livekit && (host?.codec === "opus" || prefs.receive === "opus") &&
                    fallbackStream !== host?.stream ? "rtc" : "pcm";
  if (!force && playback && playback.host.stream === host?.stream && playback.transport === transport && !room.hosting) return;
  clearPlayback();
  $("sync").disabled = !listening || !host || room.hosting;
  if (!listening || !host || room.hosting) {
    $("actual").textContent = room?.hosting ? "正在主持" : listening ? "等待下一位主持" : "尚未收听";
    $("latency").textContent = "";
    return;
  }
  const task = playback = new Listening(listenContext, host, transport);
  $("actual").textContent = transport === "rtc" ? "LiveKit · Opus 立体声" : host.codec === "opus" ? "Opus 解码缓存" : "无损 PCM";
  (transport === "rtc" ? task.runRTC() : task.runPCM()).catch(e => {
    if (task.closed || playback!==task) return;
    diagnostics(host,"disconnect",transport);
    if (transport === "rtc") {
      fallbackStream = host.stream;
      note("已改从服务器缓存收听。原因：" + e.message);
      refreshPlayback(true);
    } else { clearPlayback(); note(e.message); }
  });
}
function clearInput() {
  stageGeneration++;
  hostRTC?.close(); hostRTC = null;
  if (input) input.getTracks().forEach((t) => t.stop());
  if (captureContext) captureContext.close().catch(() => {});
  input = captureContext = publisher = null;
}
function signedOut() {
  name = "";
  epoch++;
  clearTimeout(timer);
  stopListen();
  clearInput();
  room = null;
  $("room").hidden = $("account").hidden = true;
  $("auth").hidden = false;
}
$("logout").onclick = async () => {
  try {
    await api("logout", {});
    signedOut();
    note("已退出");
  } catch (e) {
    note(e.message);
  }
};
async function poll(version) {
  if (epoch !== version) return;
  const before = changes;
  try {
    const state = await api("room" + (playback?.track ? "?listen="+playback.host.stream : ""));
    if (epoch === version && before === changes && !requesting) render(state);
  } catch (e) {
    if (epoch === version) note(e.message);
  }
  if (epoch === version) timer = setTimeout(() => poll(version), 1100);
}
function render(state) {
  room = state;
  const h = room.host;
  if (firstRoom) {
    $("codec").value = room.livekit ? "opus" : "pcm";
    firstRoom = false;
  }
  for (const option of $("codec").options) option.disabled = !room.livekit && option.value !== "pcm";
  $("codec").disabled = room.hosting || room.pending || requesting;
  const intervals = state.config.intervals.slice();
  if (listening && !intervals.includes(prefs.interval)) intervals.push(prefs.interval);
  if (!intervals.includes(prefs.interval)) { prefs.interval = 5; savePrefs(); }
  if ($("interval").dataset.options !== intervals.join()) {
    $("interval").replaceChildren(...intervals.map(i=>new Option(i+" 秒", i)));
    $("interval").dataset.options = intervals.join();
  }
  $("interval").value = prefs.interval;
  $("auto").checked = prefs.auto;
  $("receive").value = prefs.receive;
  refreshPlayback();
  $("members").textContent = room.members + " 人在线";
  $("source").textContent = h ? h.source : "等待主持上台";
  $("hostInfo").textContent = h
    ? h.name + " · " + h.rate / 1000 + " kHz · " + h.bits + "-bit · " + (h.codec === "opus" ? "Opus "+h.bitrate/1000+" kbps" : "无损 PCM")
    : "";
  $("listen").disabled = room.hosting && !listening;
  $("stage").textContent = room.hosting
    ? "下台"
    : room.pending
      ? "已申请主持"
      : h
        ? "申请主持"
        : "主持 · 共享标签页";
  $("stage").disabled = room.pending || requesting;
  $("cancel").hidden = !room.pending;
  $("quality").disabled = room.hosting || room.pending || requesting;
  $("requests").replaceChildren();
  for (const r of room.requests) {
    const row = document.createElement("div");
    row.className = "request";
    const label = document.createElement("span");
    label.textContent = r.name + " 申请主持";
    row.append(label);
    for (const [action, title] of [
      ["accept", "接受"],
      ["reject", "拒绝"],
    ]) {
      const button = document.createElement("button");
      button.textContent = title;
      button.onclick = async () => {
        try {
          const updated = await api("stage/" + action, {
            id: r.id,
            stream: h.stream,
          });
          changes++;
          render(updated);
        } catch (e) {
          note(e.message);
        }
      };
      row.append(button);
    }
    $("requests").append(row);
  }
  if (!room.hosting && !room.pending && input && !requesting) {
    clearInput();
    note("本次主持或申请已结束");
  } else if (room.hosting && input && !publisher) {
    publish(h.stream, h.bits).catch((e) => {
      if (room?.host?.stream !== h.stream || !room.hosting || !input) return;
      note(e.message);
      diagnostics(h,"error","rtc");
      leave();
    });
  }
}
async function leave() {
  const stream = room?.host?.stream || "";
  clearInput();
  try {
    const updated = await api("stage/leave", { stream });
    changes++;
    render(updated);
  } catch (e) {
    note(e.message);
  }
}
$("cancel").onclick = leave;
$("stage").onclick = async () => {
  if (room?.hosting) return leave();
  requesting = true;
  $("stage").disabled = true;
  note("");
  const version = epoch;
  try {
    const [rate, bits] = $("quality").value.split("/").map(Number);
    if (!navigator.mediaDevices?.getDisplayMedia)
      throw Error("请通过 HTTPS 在桌面 Chrome 或 Edge 中共享标签页");
    const chosen = await navigator.mediaDevices.getDisplayMedia({
      video: { displaySurface: "browser" },
      audio: true,
      systemAudio: "exclude",
    });
    if (
      version !== epoch ||
      chosen.getVideoTracks()[0]?.getSettings().displaySurface !== "browser" ||
      !chosen.getAudioTracks().length
    ) {
      chosen.getTracks().forEach((t) => t.stop());
      throw Error("请选择浏览器标签页并勾选共享音频");
    }
    input = chosen;
    for (const track of chosen.getTracks())
      track.onended = () => {
        if (input === chosen) leave();
      };
    captureContext = new AudioContext({ sampleRate: rate });
    await captureContext.resume();
    const state = await api("stage/request", {
      source: "浏览器标签页",
      rate: captureContext.sampleRate,
      bits, codec: $("codec").value === "pcm" ? "pcm" : "opus",
      bitrate: ({opus64:64000, opus256:256000})[$("codec").value] || 128000,
    });
    changes++;
    requesting = false;
    render(state);
  } catch (e) {
    clearInput();
    note(e.message);
  } finally {
    requesting = false;
    if (room) render(room);
  }
};
async function publish(stream, bits) {
  const generation = stageGeneration,
    ctx = captureContext,
    media = input;
  publisher = true;
  if (room.host.codec === "opus") {
    const host = room.host, session = hostRTC = rtcSession();
    await connectRTC(host, "publish", session);
    if (session.closed) return;
    const destination=ctx.createMediaStreamDestination();
    ctx.createMediaStreamSource(new MediaStream(media.getAudioTracks())).connect(destination);
    const track = new LivekitClient.LocalAudioTrack(destination.stream.getAudioTracks()[0], undefined, true);
    await session.room.localParticipant.publishTrack(track, {name:"music", source:LivekitClient.Track.Source.Microphone,
      audioPreset:{maxBitrate:host.bitrate}, forceStereo:true, dtx:false, red:true});
    session.timer = setInterval(async () => {
      if (session.closed) return;
      try {
        const metrics = await rtcMetrics(track);
        diagnostics(host, "publish", "rtc", metrics);
        if (metrics.rtt_ms > 1000) note("主持上行往返延迟偏高，请检查网络或手动调整音质。");
      } catch {}
    }, 2000);
    return;
  }
  await ctx.audioWorklet.addModule("/audio-worker.js");
  if (generation !== stageGeneration) return;
  const node = new AudioWorkletNode(ctx, "stereo-packets", {
    processorOptions: { bits },
  });
  const mute = ctx.createGain();
  mute.gain.value = 0;
  ctx
    .createMediaStreamSource(new MediaStream(media.getAudioTracks()))
    .connect(node);
  node.connect(mute).connect(ctx.destination);
  const pending = [];
  let busy=false, dropped=0, reported=0;
  const host=room.host;
  async function drain() {
    busy=true;
    while (pending.length && generation===stageGeneration) {
      const {data,captured}=pending.shift(), start=performance.now();
      try {
        const r=await fetch("/api/audio?stream="+stream+(captured == null ? "" : "&captured="+Math.round(captured)),
          {method:"POST",headers:{"Content-Type":"application/octet-stream"},body:data});
        if (!r.ok) throw Error("音频上传已结束");
        if (performance.now()-reported>2000) {
          diagnostics(host,dropped ? "overload":"publish","pcm",{upload_ms:performance.now()-start,discarded:dropped});
          reported=performance.now(); dropped=0;
        }
      } catch(e) {
        if (generation===stageGeneration) {note(e.message); await leave();}
        break;
      }
    }
    busy=false;
  }
  node.port.onmessage = event => {
    if (generation!==stageGeneration) return;
    if (pending.length>=2) {pending.shift(); dropped++; note("上传赶不上采集，已跳过旧音频。可手动降低共享音质。");}
    const captured = clockOffset==null ? null : Date.now()+clockOffset+(event.data.startTime-ctx.currentTime)*1000;
    pending.push({data:event.data.pcm,captured});
    if (!busy) drain();
  };
}
$("listen").onclick = async () => {
  if (listening) {stopListen(); refreshPlayback(); return;}
  listenContext = new AudioContext();
  listening = true;
  await listenContext.resume();
  $("listen").textContent = "停止收听";
  fallbackStream="";
  refreshPlayback(true);
};
$("sync").onclick=()=>{
  if (!playback) return;
  if (playback.lag != null && playback.lag <= 850 && !fallbackStream) {note("已接近实时位置"); return;}
  diagnostics(playback.host,"sync",playback.transport);
  fallbackStream=""; refreshPlayback(true); note("正在同步到当前音源");
};
$("auto").onchange=()=>{prefs.auto=$("auto").checked; savePrefs();};
$("interval").onchange=()=>{prefs.interval=Number($("interval").value); savePrefs();};
$("receive").onchange=()=>{prefs.receive=$("receive").value; savePrefs(); fallbackStream=""; refreshPlayback(true);};
api("me")
  .then((user) => enter(user.name))
  .catch(() => {});
