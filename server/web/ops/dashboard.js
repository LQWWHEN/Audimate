"use strict";

const byId = id => document.getElementById(id);
const clock = seconds => new Date(seconds * 1000).toLocaleTimeString("zh-CN", {hour12: false});
let latest = null;
let range = 300;
let timer;
let generation = 0;
let controller;

function displayBytes(n) {
  for (const [scale, unit] of [[2**30, "GiB"], [2**20, "MiB"], [1024, "KiB"]]) {
    if (n >= scale) return (n / scale).toFixed(1) + " " + unit;
  }
  return n + " B";
}

function rate(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(2) + " Mbps";
  if (n >= 1000) return (n / 1000).toFixed(1) + " kbps";
  return Math.round(n) + " bps";
}

function duration(s) {
  if (s < 60) return Math.floor(s) + " 秒";
  if (s < 3600) return Math.floor(s / 60) + " 分 " + Math.floor(s % 60) + " 秒";
  return Math.floor(s / 3600) + " 小时 " + Math.floor((s % 3600) / 60) + " 分";
}

function label(id, text, tone) {
  const element = byId(id);
  element.textContent = text;
  element.dataset.tone = tone;
}

function node(tag, value) {
  const element = document.createElement(tag);
  element.textContent = value;
  return element;
}

async function api(action, body, signal) {
  const options = {cache: "no-store", signal};
  if (body) Object.assign(options, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
  });
  const response = await fetch("/ops/api/" + action, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(data.error || "服务返回 HTTP " + response.status);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function showLogin(message) {
  ++generation;
  clearTimeout(timer);
  controller?.abort();
  latest = null;
  byId("dashboard").hidden = true;
  document.querySelector("nav").hidden = document.querySelector(".operator").hidden = true;
  byId("auth").hidden = false;
  byId("loading").hidden = true;
  byId("error").textContent = message;
  byId("password").value = "";
}

function plot() {
  if (!latest) return;
  const canvas = byId("chart");
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width || !height) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const points = latest.history.slice(-range / 5);
  const peak = Math.max(0, ...points.map(p => Math.max(p.rx_bps, p.tx_bps)));
  const maxMbps = peak ? Math.ceil(peak / 1e5) / 10 : 1;
  const left = 42, right = width - 8, top = 12, bottom = height - 23;
  const startTime = latest.time - range;
  const x = timestamp => left + (timestamp - startTime) * (right - left) / range;
  ctx.font = "11px system-ui";
  ctx.fillStyle = "#566c60";
  for (let i = 0; i <= 4; i++) {
    const y = bottom - (bottom - top) * i / 4;
    ctx.fillText(Number((maxMbps * i / 4).toFixed(2)).toString(), 1, y + 3);
    ctx.strokeStyle = "#e0e7e0";
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
  }
  for (const [timestamp, align] of [[startTime, "left"], [startTime + range / 2, "center"], [latest.time, "right"]]) {
    ctx.textAlign = align;
    ctx.fillText(clock(timestamp).slice(0, 5), x(timestamp), height - 3);
  }
  for (const [field, color, dashes] of [["rx_bps", "#146552", []], ["tx_bps", "#4266b1", [5, 3]]]) {
    ctx.beginPath();
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.setLineDash(dashes);
    points.forEach((p, i) => {
      const y = bottom - (p[field] / 1e6 / maxMbps) * (bottom - top);
      if (i) ctx.lineTo(x(p.time), y);
      else ctx.moveTo(x(p.time), y);
    });
    ctx.stroke();
  }
  canvas.setAttribute("aria-label", "最近 " + range / 60 + " 分钟，音频峰值 " + rate(peak));
  byId("emptyPlot").hidden = peak > 0;
}

function events() {
  if (!latest) return;
  const choice = byId("filter").value;
  const rows = latest.events.filter(e => choice === "全部" || e.kind === choice).map(e => {
    const row = document.createElement("li");
    const kind = node("span", e.kind);
    kind.className = "tag";
    kind.dataset.kind = e.kind;
    row.append(node("time", clock(e.time)), kind, node("span", e.message + (e.count > 1 ? " ×" + e.count : "")));
    return row;
  });
  byId("log").replaceChildren(...rows);
  byId("emptyEvents").hidden = rows.length > 0;
}

function render(s) {
  latest = s;
  byId("auth").hidden = byId("loading").hidden = true;
  byId("dashboard").hidden = document.querySelector("nav").hidden = document.querySelector(".operator").hidden = false;
  byId("operator").textContent = s.operator;
  byId("warning").hidden = true;
  label("live", "实时连接", "ok");
  byId("asof").textContent = "更新于 " + clock(s.time) + "　·　工作进程已运行 " + duration(s.uptime_s);
  byId("online").textContent = s.members.length + " / " + s.capacity;
  byId("capacity").textContent = s.pending + " 个待处理主持申请";
  byId("pulling").textContent = s.pulling;
  for (const field of ["rx", "tx"]) {
    byId(field + "Rate").textContent = rate(s[field + "_bps"]);
    byId(field + "Total").textContent = "近 10 秒均速 · 累计 " + displayBytes(s[field]);
  }
  const host = s.host;
  const flowing = host && host.last_audio_s !== null;
  const stalled = flowing ? host.last_audio_s > 3 : host && host.age_s > 3;
  label("streamState", !host ? "空席" : stalled ? "上行无新包" : flowing ? "传输中" : "等待首包",
        !host ? "" : stalled ? "warn" : "ok");
  byId("source").textContent = host ? host.source : "等待主持上台";
  byId("host").textContent = host ? "主持 · " + host.name : "";
  const details = [
    ["音频格式", host ? host.rate / 1000 + " kHz / " + host.bits + "-bit / 双声道" : "—"],
    ["最近上行", flowing ? host.last_audio_s + " 秒前" : "—"],
    ["缓冲块数", s.buffer_blocks + " / " + s.buffer_limit],
    ["当前流序号", host ? host.sequence : "—"]
  ];
  byId("streamInfo").replaceChildren(...details.map(([key, value]) => {
    const row = document.createElement("div");
    row.append(node("dt", key), node("dd", value));
    return row;
  }));
  byId("members").replaceChildren(...s.members.map(member => {
    const row = document.createElement("tr");
    let state = member.host ? "主持" : member.pulling ? "拉流中" : "在线";
    if (member.pending) state += " · 申请中";
    for (const value of [member.name, member.client, state, duration(member.joined_s), member.seen_s + " 秒前", displayBytes(member.rx), displayBytes(member.tx)]) {
      row.append(node("td", value));
    }
    return row;
  }));
  byId("emptyMembers").hidden = s.members.length > 0;
  byId("diagnostics").textContent = "累计音频块 · 收 " + s.uploads + " / 发 " + s.downloads +
    "　空读 " + s.empty_reads + "　游标跳过块数 " + s.cursor_gaps + "　房间接口错误 " + s.failures;
  events();
  plot();
}

async function refresh() {
  const version = ++generation;
  clearTimeout(timer);
  controller?.abort();
  const active = controller = new AbortController();
  const timeout = setTimeout(() => active.abort(), 8000);
  try {
    const s = await api("overview", undefined, active.signal);
    if (version === generation) render(s);
  } catch (e) {
    if (version !== generation) return;
    if (e.status === 401 || e.status === 403) {
      showLogin(e.status === 403 ? e.message : latest ? "会话已结束，请重新登录" : "");
      return;
    }
    if (latest) {
      label("live", "连接中断", "warn");
      byId("warning").textContent = "无法获取新数据，显示上次快照。正在重试。";
      byId("warning").hidden = false;
    } else {
      byId("loading").hidden = false;
      byId("loading").textContent = "暂时无法获取监控数据，正在重试…";
    }
  } finally {
    clearTimeout(timeout);
    if (version === generation) timer = setTimeout(refresh, 2000);
  }
}

byId("login").onsubmit = async event => {
  event.preventDefault();
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    await api("login", {name: byId("name").value.trim(), password: byId("password").value});
    byId("password").value = "";
    byId("error").textContent = "";
    await refresh();
  } catch (e) {
    byId("error").textContent = e.message;
  } finally {
    button.disabled = false;
  }
};
byId("logout").onclick = async () => {
  try {
    await api("logout", {});
    showLogin("");
  } catch (e) {
    if (e.status === 401) showLogin("");
    else {
      byId("warning").textContent = "退出失败，请重试。";
      byId("warning").hidden = false;
    }
  }
};
byId("refresh").onclick = refresh;
byId("filter").onchange = events;
for (const button of byId("range").querySelectorAll("button")) {
  button.onclick = () => {
    range = Number(button.dataset.range);
    for (const b of byId("range").querySelectorAll("button")) b.setAttribute("aria-pressed", b === button);
    plot();
  };
}
window.addEventListener("resize", plot);
refresh();
