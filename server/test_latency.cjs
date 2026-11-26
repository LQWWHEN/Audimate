const assert = require("node:assert/strict");

async function run() {
  const {summarizeLatency} = await import("./web/ops/latency-dev.js");
  const d={source_seq:1,server_seq:3, capture_age_ms:300,uplink_est_ms:100,ingest_ms:20,
    residence_ms:240, downlink_est_ms:10, decode_ms:10, play_queue_ms:40, serve_ms:12,fetch_ms:290,device_ms:200};
  const host={role:"host",event:"publish",time:60,transport:"pcm",detail:{server_seq:3,source_seq:1,pack_ms:190,queue_ms:10}};
  const a={role:"listener",peer:"a",time:90,event:"play",transport:"pcm",playout_ms:800,clock_error_ms:2,detail:d};
  const b={...a,peer:"b",playout_ms:1200};
  const a2={...a,time:91,detail:{...d,server_seq:4,source_seq:2}};
  const rows=[a2,b,a,{...a,event:"gap"},host];
  const calc=(mode,path="pcm",id="")=>summarizeLatency(rows,mode,path,id,65);
  const room=calc("path");
  assert.equal(room.count,3); assert.equal(room.peers,2); assert.equal(room.total,1000);
  assert.equal(room.parts.residence,240); assert.equal(room.parts.ingest,20);
  assert.equal(room.parts.uplink,80); assert.equal(room.parts.play,40);
  assert.equal(room.parts.capture,142.5); // Equal listener weights; a2 has no matching publisher.
  assert.equal(Object.values(room.parts).reduce((a,b)=>a+b),1000);
  const one = summarizeLatency([a,host],"peer","pcm","a",65);
  assert.equal(one.parts.unknown,200);
  assert.equal(one.parts.residence/one.total,.3);
  assert.equal(calc("slowest").chosen.peer,"b");
  assert.equal(summarizeLatency(rows,"slowest","pcm","",65,new Set(["a"])).chosen.peer,"a");
  assert.equal(summarizeLatency(rows,"slowest","pcm","",65,new Set()).count,0);
  assert.equal(calc("peer","pcm","a").count,2);
  assert.equal(calc("path","rtc").count,0);
  assert.equal(summarizeLatency(rows,"path","pcm","",90).count,1);
  assert.equal(summarizeLatency([{...a,playout_ms:12}],"path","pcm","").inconsistent,1);
  assert.equal(summarizeLatency([{...a,clock_error_ms:null}],"path","pcm","").count,0);
  const rtc={...b,time:92,transport:"rtc",playout_ms:undefined,rtcp_lag_ms:400,rtt_ms:60};
  assert.equal(summarizeLatency([rtc,...rows],"slowest","pcm","").chosen.peer,"a");
  const missing=summarizeLatency([rtc],"path","rtc","");
  assert.equal(missing.count,0); assert.equal(missing.auxiliary.rtt_ms,60);
  const passive={...b,detail:{...b.detail,get ingest_ms(){throw Error("unselected listener was decomposed");}}};
  assert.equal(summarizeLatency([a,passive],"peer","pcm","a").count,1);
  assert.equal(summarizeLatency([{...a,detail:{...d,skipped_playback:1}}],"path","pcm","").count,0);
  console.log("PASS: exclusive stages, residual, packet join, equal listener weights, views and incomplete traces");
}
run().catch(error=>{console.error(error);process.exitCode=1;});
