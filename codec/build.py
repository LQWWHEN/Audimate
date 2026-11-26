"""Build with Zig 0.13.0 and an extracted Xiph libFLAC 1.5.0 source tarball."""
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
src = Path(sys.argv[2]).resolve()
lib = src / "src/libFLAC"
sources = ["bitmath", "bitreader", "bitwriter", "cpu", "crc", "fixed", "float", "format",
           "lpc", "md5", "memory", "metadata_object", "stream_decoder", "stream_encoder",
           "stream_encoder_framing", "window"]
subprocess.run([sys.argv[1], "cc", "-target", "wasm32-wasi", "-mexec-model=reactor", "-O2",
                "-DFLAC__NO_ASM", "-DFLAC__HAS_OGG=0", "-DFLAC__NO_DLL", '-DPACKAGE_VERSION="1.5.0"',
                "-DHAVE_LROUND", "-DHAVE_FSEEKO", "-I"+str(src/"include"), "-I"+str(lib/"include"),
                "-Wl,--strip-all", "-Wl,--max-memory=0x2000000",
                *["-Wl,--export="+f for f in ("packet_input","packet_output","encode_packet","decode_packet")],
                str(here/"packet.c"), *[str(lib/(name+".c")) for name in sources],
                "-o", str(here.parent/"server/web/vendor/flac-1.5.0.wasm")], check=True)
