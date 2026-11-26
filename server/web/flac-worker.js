// Each message is one complete stream. No codec state crosses packet boundaries.
let ready;
async function load() {
  const wasi = {};
  // libFLAC uses in-memory callbacks. WASI libc still imports file descriptors.
  for (const fd of ["close","fdstat_get","prestat_get","prestat_dir_name","read","seek","write"])
    wasi["fd_"+fd] = () => 8; // BADF
  wasi.proc_exit = () => { throw Error("FLAC 模块已退出"); };
  let api;
  wasi.random_get = (ptr, len) => {
    crypto.getRandomValues(new Uint8Array(api.memory.buffer, ptr, len));
    return 0;
  };
  const response = await fetch("/vendor/flac-1.5.0.wasm");
  if (!response.ok) throw Error("无法加载 FLAC 模块");
  const {instance} = await WebAssembly.instantiate(await response.arrayBuffer(), {wasi_snapshot_preview1:wasi});
  api=instance.exports;
  api._initialize();
  return api;
}
self.onmessage = async ({data:{id, op, buffer, rate, bits}}) => {
  try {
    const api = await (ready ??= load());
    if (!buffer.byteLength || buffer.byteLength > 776192) throw Error("FLAC 音频包大小无效");
    new Uint8Array(api.memory.buffer, api.packet_input(), buffer.byteLength).set(new Uint8Array(buffer));
    const size = api[op](buffer.byteLength, rate, bits);
    if (!size) throw Error("FLAC 音频包无效或与主持格式不符");
    const output = new Uint8Array(api.memory.buffer, api.packet_output(), size).slice().buffer;
    self.postMessage({id,output}, [output]);
  } catch (err) {
    ready = null;
    self.postMessage({id,error:err.message});
  }
};
