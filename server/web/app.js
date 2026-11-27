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
let requesting = false, leaving = false,
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
let syncStream, listenerSync;
function clearPlayback() {
  playback?.close();
  playback = null;
}
function stopListen() {
  listening = false;
  syncStream = null;
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
  if (syncStream !== host.stream) {
    listenerSync = new SyncPolicy(performance.now());
    syncStream = host.stream;
  }
  const task = playback = new Listening(listenContext, host, transport, listenerSync);
  $("actual").textContent = transport === "rtc" ? "LiveKit · Opus 立体声" : host.codec === "opus" ? "Opus 解码缓存" : pcmLabel(host);
  (transport === "rtc" ? task.runRTC() : task.runPCM()).catch(e => {
    if (task.closed || playback!==task) return;
    const failure = task.pendingTiming || task.rtc?.failure;
    diagnostics(host,"disconnect",transport,failure && detailedTiming()
      ? {detail:{phase:failure.phase,http_status:failure.http_status,elapsed_ms:performance.now()-failure.start}} : {});
    if (transport === "rtc") {
      fallbackStream = host.stream;
      note("已改从服务器缓存收听。原因：" + e.message);
      refreshPlayback(true);
    } else { clearPlayback(); note(e.message); }
  });
}
function clearInput() {
  stageGeneration++;
  for (const track of input?.getTracks() || []) {
    track.onended = null;
    track.stop();
  }
  const released = Promise.allSettled([hostRTC?.close(), captureContext?.close()]);
  input = captureContext = publisher = hostRTC = null;
  return released;
}
function signedOut() {
  name = "";
  epoch++;
  clearTimeout(timer);
  stopListen();
  clearInput();
  leaving = requesting = false;
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
    if (epoch === version && before === changes && !requesting && !leaving) render(state);
  } catch (e) {
    if (epoch === version) note(e.message);
  }
  if (epoch === version) timer = setTimeout(() => poll(version), 1100);
}
function pcmLabel(host) {
  return host.compression === "flac" ? "无损 PCM · FLAC 压缩" : "无损 PCM";
}
function render(state) {
  const previous = room;
  room = state;
  const h = room.host;
  if (firstRoom) {
    $("codec").value = room.livekit ? "opus" : "pcm";
    firstRoom = false;
  }
  for (const option of $("codec").options)
    option.disabled = option.value === "flac" ? !state.flac : option.value.startsWith("opus") && !room.livekit;
  $("codec").disabled = room.hosting || room.pending || requesting;
  const field = $("hostThreshold"), policy = state.sync_policy;
  field.max = (policy?.max_ms ?? 1800) / 1000;
  $("syncHint").textContent = "建议 ≥ " + ((policy?.suggested_ms ?? 1000) / 1000).toFixed(1) + " 秒";
  $("applyThreshold").hidden = !state.hosting;
  field.disabled = state.pending || requesting || leaving;
  if (state.hosting && (previous?.host?.stream !== h.stream ||
      previous?.sync_policy?.effective_ms !== policy?.effective_ms ||
      previous?.host?.sync_request_ms !== h.sync_request_ms)) {
    field.value = h.sync_request_ms == null ? "" : policy.effective_ms / 1000;
  } else if (previous?.hosting && !state.hosting) field.value = "";
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
    ? h.name + " · " + h.rate / 1000 + " kHz · " + h.bits + "-bit · " + (h.codec === "opus" ? "Opus "+h.bitrate/1000+" kbps" : pcmLabel(h))
    : "";
  $("listen").disabled = room.hosting && !listening;
  $("stage").textContent = leaving ? "正在下台…" : room.hosting
    ? "下台"
    : room.pending
      ? "已申请主持"
      : h
        ? "申请主持"
        : "主持 · 共享音频";
  $("stage").disabled = room.pending || requesting || leaving;
  $("chooseSourceHelp").hidden = room.hosting || room.pending;
  $("stopSourceHelp").hidden = !room.hosting;
  $("cancel").hidden = !room.pending;
  $("cancel").disabled = leaving;
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
      const failure = hostRTC?.failure;
      diagnostics(h,"error",h.codec === "opus" ? "rtc" : "pcm",failure && detailedTiming()
        ? {detail:{phase:failure.phase,elapsed_ms:performance.now()-failure.start}} : {});
      leave(room,"upload");
    });
  }
}
async function leave(state = room, reason) {
  if (leaving || !state) return;
  const version = epoch, stream = state.host?.stream || "";
  leaving = true;
  const released = clearInput();
  if (room) render(room);
  try {
    const updated = await api("stage/leave", { stream, ...(reason && detailedTiming() ? {reason} : {}) });
    if (version === epoch) room = updated;
  } catch (e) {
    if (version === epoch) note(e.message);
  } finally {
    await released;
    if (version === epoch) {
      leaving = false;
      changes++;
      if (room) render(room);
    }
  }
}
$("cancel").onclick = () => leave();
function sharedAudioSource(stream) {
  const surface = stream.getVideoTracks()[0]?.getSettings().displaySurface;
  if (surface !== "window" && surface !== "browser")
    throw Error("请选择应用窗口或浏览器标签页，避免共享整个屏幕的声音。");
  const tracks = stream.getAudioTracks();
  if (!tracks.length || tracks[0].readyState !== "live")
    throw Error(surface === "browser"
      ? "标签页没有音轨。请重新选择播放音乐的标签页，并勾选共享标签页音频。"
      : "窗口没有音轨。请重新选择并勾选共享应用音频；若没有此选项，可用 Windows 客户端按进程共享。");
  // Chromium can return system loopback for a window even with windowAudio: "window".
  // Its application loopback track has the specific, non-localized label "Application Audio".
  if (tracks.some(track => track.label === "System Audio"))
    throw Error("浏览器返回了整个系统的声音。请改选标签页音频，或用 Windows 客户端按进程共享。");
  if (tracks.length !== 1 || (surface === "window" && tracks[0].label !== "Application Audio"))
    throw Error("无法确认音轨仅来自所选音源。请改选标签页音频，或用 Windows 客户端按进程共享。");
  return surface === "window" ? "应用窗口" : "浏览器标签页";
}
function thresholdValue() {
  const field = $("hostThreshold");
  if (!field.reportValidity()) throw Error("请检查同步阈值");
  return field.value === "" ? null : Math.round(field.valueAsNumber * 1000);
}
$("applyThreshold").onclick = async () => {
  if (!room?.hosting) return;
  const stream = room.host.stream;
  try {
    const state = await api("stage/sync", {stream, sync_request_ms:thresholdValue()});
    if (room?.host?.stream === stream) render(state);
  } catch(e) { note(e.message); }
};
$("stage").onclick = async () => {
  if (requesting || leaving) return;
  if (room?.hosting) return leave();
  requesting = true;
  $("stage").disabled = true;
  note("");
  const version = epoch;
  try {
    const syncRequest = thresholdValue();
    const [rate, bits] = $("quality").value.split("/").map(Number);
    if (!navigator.mediaDevices?.getDisplayMedia)
      throw Error("请通过 HTTPS 在桌面 Chrome 或 Edge 中共享音频。");
    const chosen = await navigator.mediaDevices.getDisplayMedia({
      video: { displaySurface: "window" },
      // Speech processing can merge the channels and attenuate sustained music.
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: { ideal: 2 },
      },
      audioSelection: "preferred",
      windowAudio: "window",
      systemAudio: "exclude",
      monitorTypeSurfaces: "exclude",
      surfaceSwitching: "exclude",
    });
    if (version !== epoch) {
      chosen.getTracks().forEach((t) => t.stop());
      return;
    }
    input = chosen;
    const source = sharedAudioSource(chosen);
    // The window was needed to authorize its application audio. A minimized
    // window can lose its video surface while its process is still playing.
    for (const track of chosen.getTracks()) {
      if (track.kind === "video" && track.getSettings().displaySurface === "window") {
        track.stop();
        continue;
      }
      track.onended = () => {
        if (input !== chosen) return;
        if (requesting) clearInput();
        else leave();
      };
    }
    captureContext = new AudioContext({ sampleRate: rate });
    await captureContext.resume();
    if (input !== chosen) return;
    const state = await api("stage/request", {
      source,
      sync_request_ms: syncRequest,
      rate: captureContext.sampleRate,
      bits, codec: $("codec").value.startsWith("opus") ? "opus" : "pcm",
      compression: $("codec").value === "flac" ? "flac" : "none",
      bitrate: ({opus64:64000, opus256:256000})[$("codec").value] || 128000,
    });
    if (version !== epoch) return;
    if (input !== chosen) {
      await leave(state);
      return;
    }
    changes++;
    requesting = false;
    render(state);
  } catch (e) {
    if (version !== epoch) return;
    clearInput();
    note(e.name === "NotAllowedError" ? "未获得共享权限。要上台，请重新选择音源并允许共享。" : e.message);
  } finally {
    if (version === epoch) {
      requesting = false;
      if (room) render(room);
    }
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
    const publishStart = performance.now();
    if (detailedTiming()) session.failure = {phase:"publish_track",start:publishStart};
    await session.room.localParticipant.publishTrack(track, {name:"music", source:LivekitClient.Track.Source.Microphone,
      audioPreset:{maxBitrate:host.bitrate}, forceStereo:true, dtx:false, red:true});
    if (session.timing) session.timing.publish_track_ms = performance.now()-publishStart;
    session.failure = null;
    if (session.closed) { track.stop(); return; }
    session.timer = setInterval(async () => {
      if (session.closed) return;
      try {
        const metrics = await rtcMetrics(track);
        if (session.timing && detailedTiming()) metrics.detail = {...metrics.detail,...session.timing};
        diagnostics(host, "publish", "rtc", metrics);
        session.timing = null;
        if (metrics.rtt_ms > 1000) note("主持上行往返延迟偏高，请检查网络或手动调整音质。");
      } catch {}
    }, 2000);
    return;
  }
  await ctx.audioWorklet.addModule("/audio-worker.js");
  if (generation !== stageGeneration) return;
  const node = new AudioWorkletNode(ctx, "stereo-packets", {
    processorOptions: { bits, blockMs: room.host.audio_block_ms ?? 200 },
  });
  const mute = ctx.createGain();
  mute.gain.value = 0;
  ctx
    .createMediaStreamSource(new MediaStream(media.getAudioTracks()))
    .connect(node);
  node.connect(mute).connect(ctx.destination);
  const pending = [];
  const blockMs = room.host.audio_block_ms ?? 200;
  const maxPending = Math.max(1, Math.floor(400 / blockMs));
  let busy=false, dropped=0, reported=0, packetId=0;
  const host=room.host;
  async function drain() {
    busy=true;
    while (pending.length && generation===stageGeneration) {
      const {data,captured,queued,detail}=pending.shift(), start=performance.now();
      const trace = detailedTiming() && detail;
      if (trace) trace.queue_ms = start-queued;
      try {
        const compressed=host.compression==="flac";
        const wire=compressed ? await flacPacket("encode_packet", data, host.rate, host.bits) : data;
        if (generation!==stageGeneration) break;
        if (trace && compressed) Object.assign(trace,{encode_ms:performance.now()-start,packet_bytes:wire.byteLength});
        const sendStart=performance.now();
        const traceQuery = trace ? "&packet="+trace.source_seq+(clockOffset==null ? "" : "&sent="+Math.round(Date.now()+clockOffset)) : "";
        const r=await fetch("/api/audio?stream="+stream+(compressed ? "&compression=flac" : "")+(captured == null ? "" : "&captured="+Math.round(captured))+traceQuery,
          {method:"POST",headers:{"Content-Type":"application/octet-stream"},body:wire});
        if (trace) {trace.fetch_headers_ms=performance.now()-sendStart; trace.http_status=r.status;}
        if (!r.ok) throw Error("音频上传已结束");
        if (trace) {
          const bodyStart=performance.now(), ack=await r.json();
          trace.body_ms=performance.now()-bodyStart;
          if (Number.isSafeInteger(ack.server_seq)) trace.server_seq=ack.server_seq;
        }
        const upload_ms=performance.now()-start;
        if (trace ? trace.source_seq%10===1 || dropped || upload_ms>blockMs : performance.now()-reported>2000) {
          diagnostics(host,dropped ? "overload":"publish","pcm",{upload_ms,discarded:dropped,...(trace ? {detail:trace} : {})});
          reported=performance.now(); dropped=0;
        }
      } catch(e) {
        if (trace && generation===stageGeneration)
          diagnostics(host,"error","pcm",{detail:{...trace,phase:trace.http_status >= 400 ? "headers" : trace.http_status ? "body" : "upload",elapsed_ms:performance.now()-start}});
        if (generation===stageGeneration) {note(e.message); await leave(room,"upload");}
        break;
      }
    }
    busy=false;
  }
  node.port.onmessage = event => {
    if (generation!==stageGeneration) return;
    if (pending.length>=maxPending) {pending.shift(); dropped++; note("上传赶不上采集，已跳过旧音频。可手动降低共享音质。");}
    const captured = clockOffset==null ? null : Date.now()+clockOffset+(event.data.startTime-ctx.currentTime)*1000;
    const data = event.data.pcm, queued = performance.now();
    packetId++;
    const packMs = data.byteLength * 1000 / (2*(bits/8)*ctx.sampleRate);
    const detail = detailedTiming() ? {source_seq:packetId, packet_bytes:data.byteLength, queue_depth:pending.length,
      pack_ms:packMs, handoff_ms:Math.max(0,(ctx.currentTime-event.data.startTime)*1000-packMs),
      source_clock_error_ms:clockError} : null;
    pending.push({data,captured,queued,detail});
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
  diagnostics(playback.host,"sync",playback.transport,detailedTiming() ? {detail:{phase:"manual"}} : {});
  listenerSync = new SyncPolicy(performance.now());
  fallbackStream=""; refreshPlayback(true); note("正在同步到当前音源");
};
$("auto").onchange=()=>{prefs.auto=$("auto").checked; savePrefs();};
$("interval").onchange=()=>{prefs.interval=Number($("interval").value); savePrefs();};
$("receive").onchange=()=>{prefs.receive=$("receive").value; savePrefs(); fallbackStream=""; refreshPlayback(true);};
api("me")
  .then((user) => enter(user.name))
  .catch(() => {});
