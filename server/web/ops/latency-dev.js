// All times are milliseconds. Only the selected view is decomposed.
const stages = [
  ["capture", "采集与组包", "#286959"], ["queue", "主持待发队列", "#539982"],
  ["encode", "FLAC 编码", "#356584"],
  ["uplink", "上传至服务入口", "#426da1"], ["ingest", "服务端入库", "#675b94"],
  ["residence", "服务端缓存驻留", "#ab7938"], ["downlink", "响应传至听众", "#51899a"],
  ["decode", "听众解码", "#936479"], ["play", "等待播放", "#5e754b"],
  ["unknown", "未归因", "#a6b1b1"],
];
const measured = v => Number.isFinite(v) && v >= 0;
const amount = v => measured(v) ? v : 0;
const codecName = c => c === "rtc" || c === "opus" ? "Opus" : "PCM";
const peerName = r => r.name+" · "+r.client+" · "+r.peer.slice(-4);
const packetId = d => Number.isSafeInteger(d?.server_seq) && d.server_seq > 0;
function usable(r) {
  return r.transport === "pcm" && packetId(r.detail) && !r.detail.skipped_playback && measured(r.playout_ms) && r.playout_ms > 0 &&
    measured(r.clock_error_ms) && r.clock_error_ms < r.playout_ms;
}

function breakdown(r, sender) {
  const d = r.detail, age = d.capture_age_ms, up = d.uplink_est_ms;
  const parts = {capture:0, queue:0, uplink:0, ingest:amount(d.ingest_ms), residence:amount(d.residence_ms),
    downlink:amount(d.downlink_est_ms), decode:amount(d.decode_ms), play:amount(d.play_queue_ms)};
  if (measured(up) && measured(d.ingest_ms) && up >= d.ingest_ms)
    parts.uplink = up - d.ingest_ms;
  if (sender && Number.isSafeInteger(d.source_seq) && sender.source_seq === d.source_seq && measured(age) && measured(up)) {
    const capture = amount(sender.pack_ms) + amount(sender.handoff_ms);
    const queue = amount(sender.queue_ms);
    const encode = amount(sender.encode_ms);
    // Capture to store contains both the pre-send stages and the upload.
    if (capture + queue + encode <= age - up + 2) {
      parts.capture = capture;
      parts.queue = queue;
      parts.encode = encode;
    }
  }
  const sum = Object.values(parts).reduce((a,b)=>a+b,0);
  // The endpoint clocks round to milliseconds. Larger contradictions invalidate the sample.
  if (sum > r.playout_ms + 2) return null;
  if (sum > r.playout_ms) for (const key of Object.keys(parts)) parts[key] *= r.playout_ms/sum;
  parts.unknown = Math.max(0,r.playout_ms-sum);
  return parts;
}

export function summarizeLatency(samples, view, path, peer, since=-Infinity, onlinePeers) {
  // Samples are newest first. A reconnect using the same account has a distinct peer ID.
  const plays = samples.filter(r=>r.time>since && r.role==="listener" && r.peer && (r.event==="play" || r.event==="gap") &&
    (view!=="slowest" || !onlinePeers || onlinePeers.has(r.peer)));
  const latest = new Map();
  for (const r of plays) if (!latest.has(r.peer)) latest.set(r.peer,r);
  let selectedPeer = peer;
  if (view === "slowest") {
    let max = -1;
    const ranked = new Set();
    for (const r of plays) {
      if (ranked.has(r.peer) || latest.get(r.peer).transport!==r.transport || !usable(r)) continue;
      ranked.add(r.peer);
      if (r.playout_ms > max) { max=r.playout_ms; selectedPeer=r.peer; }
    }
    if (max < 0) selectedPeer = "";
  }
  const chosen = latest.get(selectedPeer);
  const selected = view==="path" ? plays.filter(r=>r.transport===path) :
    plays.filter(r=>r.peer===selectedPeer && r.transport===chosen?.transport);
  const senders = new Map();
  for (const r of samples) {
    if (r.role==="host" && r.transport==="pcm" && ["publish","overload"].includes(r.event) && packetId(r.detail))
      senders.set(r.detail.server_seq,r.detail);
  }
  const groups = new Map(), parts = {}, seen = new Set();
  let count=0, inconsistent=0;
  for (const r of selected) {
    if (!usable(r)) continue;
    const id=r.peer+"/"+r.detail.server_seq;
    if (seen.has(id)) continue;
    const split=breakdown(r,senders.get(r.detail.server_seq));
    if (!split) {inconsistent++; continue;}
    seen.add(id);
    if (!groups.has(r.peer)) groups.set(r.peer,{n:0,total:0,parts:{}});
    const g=groups.get(r.peer);
    g.n++; g.total+=r.playout_ms; count++;
    for (const [key,value] of Object.entries(split)) g.parts[key]=(g.parts[key]||0)+value;
  }
  let total=0;
  for (const g of groups.values()) {
    total+=g.total/g.n/groups.size;
    for (const [key,value] of Object.entries(g.parts)) parts[key]=(parts[key]||0)+value/g.n/groups.size;
  }
  const auxiliary = selected[0] || (view==="slowest" && !chosen ? plays[0] : null);
  return {total,parts,count,peers:groups.size,inconsistent,chosen:view==="path"?null:chosen,auxiliary};
}

let snapshot, timer, lastUpdate, lastRound, lastLevel;
function populate(select, entries, preferred) {
  const previous=select.value;
  if (select.length!==entries.length || entries.some(([v,t],i)=>select.options[i]?.value!==v || select.options[i]?.text!==t))
    select.replaceChildren(...entries.map(([v,t])=>new Option(t,v)));
  select.value=entries.some(([v])=>v===previous) ? previous : preferred || entries[0]?.[0] || "";
}

function paint(panel, result) {
  const canvas=panel.querySelector("canvas"), ctx=canvas.getContext("2d");
  const scale=window.devicePixelRatio || 1;
  canvas.width=canvas.height=Math.round(172*scale);
  ctx.scale(scale,scale);
  const rows=[];
  let start=-Math.PI/2;
  for (const [key,label,color] of stages) {
    const value=result.parts[key];
    if (!(value>0)) continue;
    const share=value/result.total, end=start+2*Math.PI*share;
    ctx.beginPath(); ctx.moveTo(86,86); ctx.arc(86,86,82,start,end); ctx.closePath();
    ctx.fillStyle=color; ctx.fill(); ctx.strokeStyle="white"; ctx.stroke(); start=end;
    const row=document.createElement("tr"), tag=document.createElement("i");
    tag.className="dot"; tag.style.backgroundColor=color;
    for (const text of [label,value.toFixed(1)+" ms",(share*100).toFixed(1)+"%"]) {
      const td=document.createElement("td"); td.textContent=text; row.append(td);
    }
    row.firstChild.prepend(tag); rows.push(row);
  }
  panel.querySelector("tbody").replaceChildren(...rows);
  panel.querySelector(".pieResult").hidden=!result.count;
  canvas.setAttribute("aria-label","预计播放延迟构成，各阶段耗时见表格");
}

function refreshPie() {
  const panel=document.getElementById("latencyPie");
  if (!panel || !snapshot) return;
  const {host,timing,service}=snapshot;
  const view=panel.querySelector("#pieView").value, seconds=Number(panel.querySelector("#pieWindow").value);
  const path=panel.querySelector("#piePath"), peer=panel.querySelector("#piePeer");
  const pathName=t=>(host?.compression==="flac" ? "FLAC" : host ? codecName(host.codec) : "—")+" → "+
    (host?.compression==="flac" && t==="pcm" ? "FLAC" : codecName(t));
  const now=snapshot.time+(performance.now()-lastUpdate)/1000;
  const samples=host && timing.round_id && service.active.logging==="debug"
    ? snapshot.samples.filter(s=>s.round_id===timing.round_id) : [];
  const listeners=new Map();
  for (const r of samples) if (r.time>now-seconds && r.peer && r.role==="listener" && ["play","gap"].includes(r.event) && !listeners.has(r.peer))
    listeners.set(r.peer,r);
  populate(path,["pcm","rtc"].map(t=>[t,pathName(t)]),host?.codec==="opus"?"rtc":"pcm");
  const entries=Array.from(listeners,([id,r])=>[id,peerName(r)]);
  if (view==="peer" && peer.value && !listeners.has(peer.value))
    entries.push([peer.value,peer.selectedOptions[0].text]);
  populate(peer,entries);
  path.parentElement.hidden=view!=="path"; peer.parentElement.hidden=view!=="peer";
  path.disabled=!host; peer.disabled=!host || !entries.length;
  const onlinePeers=view==="slowest" ? new Set(snapshot.members.map(m=>m.peer)) : null;
  const res=summarizeLatency(samples,view,path.value,peer.value,now-seconds,onlinePeers);
  paint(panel,res);
  let status;
  if (!host) status="房间空席，等待下一轮主持";
  else if (service.active.logging!=="debug") status="当前仅收集异常或已关闭诊断。完整采样需要启用「完整调试」。";
  else if (res.count) status="预计 "+res.total.toFixed(1)+" ms · "+res.peers+" 位听众 · "+res.count+" 个有效包";
  else status="端到端样本不足，等待带有对时和包号的播放样本。";
  if (res.chosen) status+=" · "+peerName(res.chosen)+" · "+pathName(res.chosen.transport);
  if (res.inconsistent) status+=" · "+res.inconsistent+" 条计时不一致";
  panel.querySelector(".pieStatus").textContent=status;
  const aux=res.auxiliary, stats=[];
  if (!res.count && aux) {
    for (const [key,label] of [["rtt_ms","RTT"],["jitter_ms","抖动"],["buffer_ms","接收缓冲"]])
      if (measured(aux[key])) stats.push(label+" "+aux[key].toFixed(1)+" ms");
  }
  panel.querySelector(".pieAux").textContent=stats.length ? "最近记录 · "+peerName(aux)+" · "+stats.join(" / ") : "";
}

export function stopPie() {
  clearInterval(timer); timer=null; snapshot=null; lastRound=lastLevel=undefined;
  document.getElementById("latencyPie")?.remove();
  document.getElementById("delayNav").hash="telemetry";
}

export function presentPie(s) {
  snapshot=s; lastUpdate=performance.now();
  let panel=document.getElementById("latencyPie");
  if (!panel) {
    panel=document.createElement("section"); panel.id="latencyPie"; panel.className="card";
    panel.innerHTML='<div class="card-head"><h2>播放延迟构成</h2><small>每 5 秒更新</small></div>'+
      '<div class="chartFilters"><label>视图 <select id="pieView"><option value="path">按路径汇总</option><option value="peer">指定听众</option><option value="slowest">当前最慢听众</option></select></label>'+
      '<label>路径 <select id="piePath"></select></label><label hidden>听众 <select id="piePeer"></select></label>'+
      '<label>时间窗口 <select id="pieWindow"><option value="10">最近 10 秒</option><option value="30" selected>最近 30 秒</option><option value="60">最近 60 秒</option></select></label></div>'+
      '<p class="pieStatus"></p><p class="pieAux"></p><div class="pieResult" hidden><canvas role="img"></canvas>'+
      '<table><thead><tr><th>环节</th><th>平均耗时</th><th>占比</th></tr></thead><tbody></tbody></table></div>';
    document.getElementById("telemetry").before(panel);
    document.getElementById("delayNav").hash="latencyPie";
    panel.onchange=refreshPie;
    timer=setInterval(refreshPie,5000);
  }
  if (s.timing.round_id!==lastRound || s.service.active.logging!==lastLevel) {
    panel.querySelector("#piePath").replaceChildren();
    panel.querySelector("#piePeer").replaceChildren();
    lastRound=s.timing.round_id; lastLevel=s.service.active.logging;
    refreshPie();
  }
}
