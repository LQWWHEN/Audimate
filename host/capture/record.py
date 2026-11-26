"""Record an Audimate process capture to WAV, or import Capture in the host."""
import argparse
import ctypes as ct
from dataclasses import dataclass
from functools import lru_cache
import math
import sys
import time
import wave
from pathlib import Path


CHANNELS = 2
BLOCKS_PER_SECOND = 100


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int
    bits_per_sample: int

    def __post_init__(self):
        if not isinstance(self.sample_rate, int) or not (
            8000 <= self.sample_rate <= 192000 and self.sample_rate % BLOCKS_PER_SECOND == 0
        ):
            raise ValueError("sample rate must be 8000..192000 Hz in multiples of 100 Hz")
        if not isinstance(self.bits_per_sample, int) or self.bits_per_sample not in (16, 24, 32):
            raise ValueError("bit depth must be 16, 24 or 32")

    @property
    def sample_bytes(self):
        return self.bits_per_sample // 8

    @property
    def frames_per_block(self):
        return self.sample_rate // BLOCKS_PER_SECOND


DEFAULT_FORMAT = AudioFormat(48000, 16)
QUALITY_PRESETS = {
    "standard": DEFAULT_FORMAT,
    "cd": AudioFormat(44100, 16),
    "studio": AudioFormat(96000, 24),
}


class NativeFormat(ct.Structure):
    _fields_ = [("sample_rate", ct.c_uint32), ("bits_per_sample", ct.c_uint32)]


class BlockInfo(ct.Structure):
    _fields_ = [
        ("sequence", ct.c_uint64),
        ("discontinuities", ct.c_uint32),
    ]


@lru_cache(maxsize=32)
def block_type(fmt):
    num_samples = fmt.frames_per_block * CHANNELS
    if fmt.bits_per_sample == 24:
        pcm_type = ct.c_ubyte * (fmt.sample_bytes * num_samples)
    else:
        sample_type = {16: ct.c_int16, 32: ct.c_int32}[fmt.bits_per_sample]
        pcm_type = sample_type * num_samples

    class PCMBlock(ct.Structure):
        _fields_ = [*BlockInfo._fields_, ("pcm", pcm_type)]
        audio_format = fmt

        def samples(self):
            """Interleaved signed sample values, including for packed 24-bit PCM."""
            if fmt.bits_per_sample == 24:
                packed = bytes(self.pcm)
                return [int.from_bytes(packed[i:i+3], "little", signed=True)
                        for i in range(0, len(packed), 3)]
            return self.pcm

    return PCMBlock


# Public ctypes layout used by callers of the original fixed-format C API.
Block = block_type(DEFAULT_FORMAT)


def windows_build():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            build = winreg.QueryValueEx(key, "CurrentBuildNumber")[0]
            revision = winreg.QueryValueEx(key, "UBR")[0]
            return f"{build}.{revision}"
    except OSError:
        return str(sys.getwindowsversion().build)


def check_result(result, *, opening=False):
    if result < 0:
        code = result & 0xFFFFFFFF
        message_id = code & 0xFFFF if code >> 16 == 0x8007 else result
        if code == 0x8007042B:
            detail = "The selected process exited. Select the player's current PID."
        else:
            detail = ct.FormatError(message_id).strip()
        context = ""
        if opening:
            context = f"Opening process loopback on Windows build {windows_build()}: "
        error = OSError(f"{context}HRESULT 0x{code:08X} {detail}")
        error.hresult = code
        raise error


class Capture:
    """One PID, one reader. Keep open/read/close on a consumer thread."""

    def __init__(self, dll, pid, audio_format=DEFAULT_FORMAT):
        if not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("PID must be a positive 32-bit integer")
        self.lib = ct.CDLL(str(Path(dll).resolve()))
        self._block_type = block_type(audio_format)
        self.lib.audimate_open_format.argtypes = [ct.c_uint32, ct.POINTER(NativeFormat), ct.POINTER(ct.c_void_p)]
        self.lib.audimate_open_format.restype = ct.c_int32
        self.lib.audimate_read_pcm.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint32,
                                               ct.POINTER(BlockInfo), ct.c_uint32]
        self.lib.audimate_read_pcm.restype = ct.c_int32
        self.lib.audimate_stop.argtypes = [ct.c_void_p]
        self.lib.audimate_stop.restype = None
        self.lib.audimate_close.argtypes = [ct.c_void_p]
        self.lib.audimate_close.restype = None
        self.handle = ct.c_void_p()
        fmt = NativeFormat(audio_format.sample_rate, audio_format.bits_per_sample)
        check_result(self.lib.audimate_open_format(pid, ct.byref(fmt), ct.byref(self.handle)), opening=True)

    @property
    def audio_format(self):
        return self._block_type.audio_format

    def read(self, timeout_ms=1000):
        """Return the next 10 ms block, or None when the timeout expires."""
        if not self.handle:
            raise ValueError("capture is closed")
        if not 0 <= timeout_ms <= 0xFFFFFFFF:
            raise ValueError("timeout_ms is outside the uint32 range")
        block = self._block_type()
        info = BlockInfo()
        result = self.lib.audimate_read_pcm(self.handle, block.pcm, ct.sizeof(block.pcm),
                                            ct.byref(info), timeout_ms)
        check_result(result)
        if result != 0:
            return None
        block.sequence = info.sequence
        block.discontinuities = info.discontinuities
        return block

    def stop(self):
        """Wake a reader and discard queued blocks before switching sources."""
        self.lib.audimate_stop(self.handle)

    def close(self):
        if self.handle:
            self.lib.audimate_close(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def dbfs(value, bits_per_sample=DEFAULT_FORMAT.bits_per_sample):
    full_scale = 1 << (bits_per_sample - 1)
    return f"{20 * math.log10(value / full_scale):.1f}" if value else "-inf"


def record(args, audio_format=DEFAULT_FORMAT):
    # Classic RIFF stores chunk sizes in 32 bits.
    bytes_per_second = audio_format.sample_rate * CHANNELS * audio_format.sample_bytes
    if not 1 <= args.seconds <= (2**32 - 45) // bytes_per_second:
        raise ValueError("duration is outside the WAV size limit")
    expected = dropped = peak_l = peak_r = 0
    with Capture(args.dll, args.pid, audio_format) as capture, args.output.open("xb") as destination:
        with wave.open(destination, "wb") as wav:
            wav.setparams((CHANNELS, audio_format.sample_bytes, audio_format.sample_rate, 0, "NONE", "not compressed"))
            print(f"Recording PID {args.pid} and children at {audio_format.sample_rate} Hz, "
                  f"{audio_format.bits_per_sample}-bit -> {args.output}", flush=True)
            last_packet = time.monotonic()
            for written in range(1, args.seconds * BLOCKS_PER_SECOND + 1):
                block = capture.read()
                while block is None:
                    if time.monotonic() - last_packet >= 5:
                        raise TimeoutError("No audio packets for 5 seconds. Check the selected player and output device.")
                    block = capture.read()
                last_packet = time.monotonic()
                dropped += block.sequence - expected
                expected = block.sequence + 1
                wav.writeframesraw(bytes(block.pcm))
                samples = block.samples()
                peak_l = max(peak_l, max(map(abs, samples[::CHANNELS])))
                peak_r = max(peak_r, max(map(abs, samples[1::CHANNELS])))
                if written % BLOCKS_PER_SECOND == 0:
                    left = dbfs(peak_l, audio_format.bits_per_sample)
                    right = dbfs(peak_r, audio_format.bits_per_sample)
                    print(f"{written // BLOCKS_PER_SECOND}s  L/R {left}/{right} dBFS  "
                          f"dropped={dropped} discontinuities={block.discontinuities}", flush=True)
                    peak_l = peak_r = 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", required=True, type=Path)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--quality", choices=QUALITY_PRESETS, default="standard")
    parser.add_argument("--sample-rate", type=int, help="override the preset sample rate in Hz")
    parser.add_argument("--bit-depth", choices=(16, 24, 32), type=int, help="override the preset PCM bit depth")
    args = parser.parse_args()
    try:
        preset = QUALITY_PRESETS[args.quality]
        fmt = AudioFormat(preset.sample_rate if args.sample_rate is None else args.sample_rate,
                          preset.bits_per_sample if args.bit_depth is None else args.bit_depth)
        record(args, fmt)
    except (OSError, ValueError) as error:
        print(f"PID {args.pid}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
