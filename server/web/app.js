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
let listenStream = "",
  requesting = false,
  sending = false,
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
    if (response.status === 401 && name) signedOut();
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
  poll(epoch);
}
function stopListen() {
  listenStream = "";
  if (listenContext) listenContext.close().catch(() => {});
  listenContext = null;
  $("listen").textContent = "收听";
}
function clearInput() {
  stageGeneration++;
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
    const state = await api("room");
    if (epoch === version && before === changes && !requesting) render(state);
  } catch (e) {
    if (epoch === version) note(e.message);
  }
  if (epoch === version) timer = setTimeout(() => poll(version), 1100);
}
function render(state) {
  room = state;
  const h = room.host;
  if (listenStream && listenStream !== h?.stream) stopListen();
  $("members").textContent = room.members + " 人在线";
  $("source").textContent = h ? h.source : "等待主持上台";
  $("hostInfo").textContent = h
    ? h.name + " · " + h.rate / 1000 + " kHz · " + h.bits + "-bit"
    : "";
  $("listen").disabled = !h || room.hosting;
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
    stopListen();
    publish(h.stream, h.bits).catch((e) => {
      note(e.message);
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
      bits,
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
  node.port.onmessage = async (event) => {
    if (generation !== stageGeneration || sending) return;
    sending = true;
    try {
      const r = await fetch("/api/audio?stream=" + stream, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: event.data,
      });
      if (!r.ok) throw Error("音频上传已结束");
    } catch (e) {
      if (generation === stageGeneration) {
        note(e.message);
        await leave();
      }
    } finally {
      sending = false;
    }
  };
}
$("listen").onclick = async () => {
  if (listenContext) return stopListen();
  const h = room?.host;
  if (!h) return;
  const ctx = new AudioContext();
  listenContext = ctx;
  listenStream = h.stream;
  $("listen").textContent = "停止收听";
  note("");
  let after = -1,
    next = 0;
  try {
    await ctx.resume();
    while (listenContext === ctx) {
      if (next > ctx.currentTime + 0.65) {
        await sleep(60);
        continue;
      }
      const r = await fetch(
        "/api/audio?stream=" + h.stream + "&after=" + after,
        { cache: "no-store" },
      );
      if (r.status === 204) {
        await sleep(65);
        continue;
      }
      if (!r.ok) throw Error("当前音源已结束");
      const view = new DataView(await r.arrayBuffer());
      if (listenContext !== ctx) break;
      after = Number(r.headers.get("X-Sequence"));
      const width = h.bits / 8;
      const buf = ctx.createBuffer(2, view.byteLength / (width * 2), h.rate);
      for (let channel = 0; channel < 2; channel++) {
        const target = buf.getChannelData(channel);
        for (let i = 0; i < target.length; i++) {
          const j = (2 * i + channel) * width;
          const v =
            width === 2
              ? view.getInt16(j, true)
              : width === 4
                ? view.getInt32(j, true)
                : view.getUint16(j, true) | (view.getInt8(j + 2) << 16);
          target[i] = v / 2 ** (h.bits - 1);
        }
      }
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      next = Math.max(ctx.currentTime + 0.025, next);
      src.start(next);
      next += buf.duration;
    }
  } catch (e) {
    if (listenContext === ctx) {
      stopListen();
      note(e.message);
    }
  }
};
api("me")
  .then((user) => enter(user.name))
  .catch(() => {});
