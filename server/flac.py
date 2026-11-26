"""Packet codec shared by the desktop transport and the room worker."""
from pathlib import Path
from threading import Lock
import atexit
from importlib.util import find_spec

CAPACITY = 768000 + 8192
MODULE = Path(__file__).with_name("web") / "vendor" / "flac-1.5.0.wasm"
AVAILABLE = MODULE.is_file() and find_spec("wasmtime") is not None
_lock = Lock()
_codec = None


class _Codec:
    def __init__(self):
        import wasmtime as w
        config = w.Config()
        config.consume_fuel = True
        engine = w.Engine(config)
        module = w.Module.from_file(engine, MODULE)
        self.store = w.Store(engine)
        self.store.set_wasi(w.WasiConfig())
        linker = w.Linker(engine)
        linker.define_wasi()
        self.store.set_fuel(100_000_000)
        self.exports = linker.instantiate(self.store, module).exports(self.store)
        self.exports["_initialize"](self.store)
        self.input = self.exports["packet_input"](self.store)
        self.output = self.exports["packet_output"](self.store)

    def run(self, op, data, rate, bits):
        self.store.set_fuel(500_000_000 if op == "encode_packet" else 100_000_000)
        memory = self.exports["memory"]
        memory.write(self.store, data, self.input)
        size = self.exports[op](self.store, len(data), rate, bits)
        if not size:
            raise ValueError("FLAC 音频包无效或与主持格式不符")
        return bytes(memory.read(self.store, self.output, self.output + size))


def _packet(op, data, rate, bits):
    global _codec
    if not 0 < len(data) <= CAPACITY:
        raise ValueError("FLAC 音频包大小无效")
    from wasmtime import Trap
    with _lock:
        if _codec is None:
            _codec = _Codec()
        try:
            return _codec.run(op, data, rate, bits)
        except Trap as exc:
            _codec = None
            raise ValueError("FLAC 音频包超出处理限制") from exc


def encode(data, rate, bits):
    return _packet("encode_packet", data, rate, bits)


def decode(data, rate, bits):
    return _packet("decode_packet", data, rate, bits)


@atexit.register
def _close():
    global _codec
    with _lock:
        _codec = None
