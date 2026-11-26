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
  if (!Number.isFinite(n)) return "—";
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
  timingView?.then(view=>{ if (!latest) view.stopPie(); }).catch(()=>{});
  byId("dashboard").hidden = true;
  document.querySelector("nav").hidden = document.querySelector(".operator").hidden = true;
  byId("auth").hidden = false;
  byId("loading").hidden = true;
  byId("error").textContent = message;
  byId("password").value = "";
}

const trafficSeries = [
  {codec:"pcm", direction:"rx", key:"rx_bps", label:"PCM 入站", color:"#146552"},
  {codec:"pcm", direction:"tx", key:"tx_bps", label:"PCM 出站", color:"#397b98"},
  {codec:"opus", direction:"rx", key:"opus_rx_bps", label:"Opus 入站", color:"#855095"},
  {codec:"opus", direction:"tx", key:"opus_tx_bps", label:"Opus 出站", color:"#a35d16"},
];
byId("trafficDirection").onchange = byId("trafficCodec").onchange = plot;
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
  const direction = byId("trafficDirection").value, codec = byId("trafficCodec").value;
  const series = trafficSeries.filter(s => (!direction || s.direction === direction) && (!codec || s.codec === codec));
  const values = series.flatMap(s => points.map(p => p[s.key]).filter(Number.isFinite));
  const peak = Math.max(0, ...values);
  byId("trafficLegend").replaceChildren(...series.map(s => {
    const label = node("span", s.label+" "+rate(points.at(-1)?.[s.key]));
    const line = document.createElement("i");
    line.className = "lineKey "+s.direction;
    line.style.borderColor = s.color;
    label.prepend(line);
    return label;
  }));
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
    ctx.fillText(range===60 ? clock(timestamp) : clock(timestamp).slice(0, 5), x(timestamp), height - 3);
  }
  for (const s of series) {
    ctx.beginPath();
    ctx.lineWidth = 2;
    ctx.strokeStyle = s.color;
    ctx.setLineDash(s.direction === "tx" ? [5,3] : []);
    let connected = false;
    for (const p of points) {
      if (!Number.isFinite(p[s.key])) { connected = false; continue; }
      const y = bottom - (p[s.key] / 1e6 / maxMbps) * (bottom - top);
      if (connected) ctx.lineTo(x(p.time), y);
      else ctx.moveTo(x(p.time), y);
      connected = true;
    }
    ctx.stroke();
  }
  canvas.setAttribute("aria-label", "最近 " + range / 60 + " 分钟，音频峰值 " + rate(peak));
  byId("emptyPlot").hidden = peak > 0;
  byId("emptyPlot").textContent = values.length ? "所选时段暂无音频传输" : "等待所选流量的采样";
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
    const audio = s.audio_traffic;
    byId(field + "Rate").textContent = rate(audio[field + "_bps"]);
    byId(field + "Total").textContent = audio[field] == null ? "Opus 指标缺测 · PCM 已计 "+displayBytes(s[field]) :
      "近 10 秒均速 · 累计 " + displayBytes(audio[field]);
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
    ["缓冲块数", s.buffer_blocks + " 块 · 上限 " + s.history_s + " 秒"],
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
  renderControl(s);
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
let previousConfig = "";
let timingView;
const ms = value => Number.isFinite(value) ? Math.round(value) + " ms" : "—";
function renderControl(s) {
  const {maintenance,restarting,active,pending,audit} = s.service;
  byId("serviceState").textContent = restarting ? "正在重启媒体服务" : maintenance ? "维护中" : "已开放";
  byId("activeConfig").textContent = "生效缓存 " + active.history_s + " 秒 · 校准档位 " + active.intervals.join(" / ") + " 秒 · 成员离线超时 " + active.lease_s + " 秒";
  const signature=JSON.stringify([pending,active.logging]);
  if (signature!==previousConfig) {
    byId("history").value=pending.history_s; byId("intervals").value=pending.intervals.join(", ");
    byId("lease").value=pending.lease_s;
    byId("level").value=active.logging; previousConfig=signature;
  }
  for(const el of byId("config").elements) el.disabled=!maintenance;
  byId("maintenance").disabled=maintenance;
  byId("restart").disabled=!maintenance || restarting;
  byId("open").disabled=!maintenance || restarting || JSON.stringify(active)!==JSON.stringify(pending);
  byId("audit").replaceChildren(...audit.map(a=>node("li",clock(a.time)+"　"+a.actor+"　"+a.action)));
  const latencies=s.samples.filter(x=>Number.isFinite(x.playout_ms)).map(x=>x.playout_ms).sort((a,b)=>a-b);
  byId("p95").textContent="预计播放延迟 P95　"+ms(latencies[Math.ceil(latencies.length*.95)-1])+"　("+latencies.length+" 个样本)";
  const detailNames={timeline_ms:"缓存位置 ms",rtcp_lag_ms:"RTCP 时差估计 ms",clock_error_ms:"对时半往返 ms",jitter_ms:"抖动 ms",upload_ms:"上传 ms",discarded:"丢弃",lost:"丢包",underruns:"欠载",bytes:"RTP 累计字节"};
  byId("samples").replaceChildren(...s.samples.slice(0,60).map(x=>{
    const row=document.createElement("tr");
    const details=Object.entries(detailNames).filter(([k])=>x[k]!=null).map(([k,v])=>v+" "+x[k]).join(" · ");
    for (const v of [clock(x.time),x.name+" / "+x.client+" / "+x.session,x.transport+" / "+x.event,ms(x.playout_ms),ms(x.rtt_ms),ms(x.buffer_ms),details||"—"]) row.append(node("td",v));
    return row;
  }));
  byId("noSamples").hidden=!!s.samples.length;
  if (s.timing) {
    timingView ??= import("/ops/timing-dev.js");
    timingView.then(view => { if (latest === s) view.render(s); }).catch(() => { timingView = null; });
  } else {
    byId("devTiming")?.remove();
    timingView?.then(view=>{ if (latest===s) view.stopPie(); }).catch(()=>{});
  }
}
async function manage(action,data={}) {
  try {render(await api("service/"+action,data)); byId("opResult").textContent="操作已完成";}
  catch(e){byId("opResult").textContent=e.message;}
}
byId("config").onsubmit = e => {
  e.preventDefault();
  manage("configure", {
    history_s: Number(byId("history").value),
    intervals: byId("intervals").value.split(/[,，]/).map(Number),
    lease_s: Number(byId("lease").value),
  });
};
byId("saveLevel").onclick=()=>manage("logging",{logging:byId("level").value});
byId("maintenance").onclick=()=>{if(confirm("进入维护将结束所有普通用户会话。继续？"))manage("maintenance");};
byId("restart").onclick=()=>{if(confirm("现在重启并应用暂存配置？"))manage("restart");};
byId("open").onclick=()=>manage("open");
byId("findAccount").onsubmit=async e=>{
  e.preventDefault();
  try {
    const rows=await api("accounts?name="+encodeURIComponent(byId("query").value));
    byId("accounts").replaceChildren(...rows.map(r=>new Option(r.name+(r.disabled?"（已停用）":""),r.name)));
    byId("accountResult").textContent=rows.length+" 条结果，最多显示 100 条";
  }catch(err){byId("accountResult").textContent=err.message;}
};
byId("accountOp").onchange=()=>{byId("newSecret").hidden=byId("accountOp").value!=="password";};
byId("accountAction").onsubmit=async e=>{
  e.preventDefault();
  const name=byId("accounts").value,action=byId("accountOp").value;
  if(!name||!confirm("对 "+name+" 执行所选操作？"))return;
  try {render(await api("accounts",{name,action,password:byId("newPassword").value}));byId("accountResult").textContent="已更新 "+name;}
  catch(err){byId("accountResult").textContent=err.message;}
  finally{byId("newPassword").value="";}
};
window.addEventListener("resize", plot);
refresh();
