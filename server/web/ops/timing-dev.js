// Served only by create_dev_app. All values supplied by clients are rendered as text.
const extras = {source_seq:"源包号",server_seq:"缓存包号",packet_bytes:"包字节",queue_depth:"待发包数",
  reused:"复用连接 (1/0)",empty_reads:"空拉取次数",unconfirmed:"结果未确认",timed_out:"超时",
  http_status:"HTTP 状态",source_clock_error_ms:"主持对时半往返 ms",concealed_samples:"累计掩蔽样本",phase:"阶段"};
const phases = {clock:"对时",connect:"建连",send:"写请求",headers:"等响应头",body:"读请求响应体",
  fetch:"拉取",capture:"采集",output:"写设备",sdk:"媒体 SDK",grant:"申请凭据",upload:"上传",
  decode:"解码",manual:"手动同步",auto:"自动同步",history:"历史上限",publish_track:"发布音轨",subscribe:"订阅"};
let snapshot;
function rows(table, values) {
  table.replaceChildren(...values.map(fields => {
    const tr=document.createElement("tr");
    for (const value of fields) {
      const cell=document.createElement("td");
      cell.textContent=value;
      tr.append(cell);
    }
    return tr;
  }));
}
function packets(panel) {
  const filter=panel.querySelector("input").value.trim();
  const {samples,timing} = snapshot;
  const visible=samples.filter(s=>s.session===timing.stream && s.detail &&
    (!filter || String(s.detail.source_seq)===filter || String(s.detail.server_seq)===filter));
  rows(panel.querySelector(".packetRows"),visible.slice(0,60).map(s=>{
    const d=s.detail;
    const values=Object.entries(d).filter(([key])=>key!=="server_seq" && key!=="source_seq").map(([key,v])=>
      (timing.labels[key] || extras[key] || key)+" "+(key==="phase" ? phases[v] || v : v)+(timing.labels[key] ? " ms" : ""));
    return [new Date(s.time*1000).toLocaleTimeString("zh-CN",{hour12:false}),s.name+" / "+s.client,
      s.transport+" / "+s.event,d.source_seq??"—",d.server_seq??"—",values.join(" · ")];
  }));
  panel.querySelector(".emptyTrace").hidden=visible.length>0;
}
export function render(s) {
  snapshot=s;
  let panel=document.getElementById("devTiming");
  if (!panel) {
    panel=document.createElement("section");
    panel.id="devTiming";
    panel.innerHTML='<h3>开发版链路计时</h3><p class="traceScope"></p>'+
      '<details><summary>分段耗时分布（毫秒）</summary><div class="scroll"><table><thead><tr><th>端 / 路径</th><th>阶段</th><th>样本数</th><th>P50</th><th>P95</th><th>最大</th></tr></thead><tbody class="stageRows"></tbody></table></div></details>'+
      '<label>按源包号或缓存包号查看 <input type="search" inputmode="numeric" placeholder="全部采样包"></label>'+
      '<div class="scroll"><table><thead><tr><th>服务端记录时间</th><th>端</th><th>路径 / 事件</th><th>源包号</th><th>缓存包号</th><th>分段明细</th></tr></thead><tbody class="packetRows"></tbody></table></div>'+
      '<p class="emptyTrace">暂无匹配的开发版样本</p>';
    panel.querySelector("input").oninput=()=>packets(panel);
    document.getElementById("telemetry").append(panel);
  }
  panel.querySelector(".traceScope").textContent="音源 "+(s.timing.stream||"—")+" · 最近 10 分钟内保留的采样。跨端时间为估算；各阶段可能重叠。";
  rows(panel.querySelector(".stageRows"),s.timing.rows.map(r=>[r.client+" / "+r.transport+" / "+r.direction,
    s.timing.labels[r.stage],r.n,r.p50,r.p95,r.peak]));
  packets(panel);
}
