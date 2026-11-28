"""Build a self-contained Windows x64 test executable from a clean packaging venv."""
import argparse
from importlib.metadata import distribution
from pathlib import Path
import platform
import ssl
import struct
import subprocess
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "windows-package"
DIST = ROOT / "build" / "distribution"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path, default=ROOT/"build"/"Release"/"audimate_capture.dll")
    parser.add_argument("--server", help="Default public HTTPS address for this test build")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().upper() not in ("AMD64", "X86_64") or struct.calcsize("P") != 8:
        parser.error("Build with Windows x64 Python")
    dll = args.dll.resolve()
    if not dll.is_file() or dll.name.lower() != "audimate_capture.dll":
        parser.error("Build the x64 audimate_capture.dll first or specify --dll")
    BUILD.mkdir(parents=True, exist_ok=True)
    launcher = BUILD/"launch_player.py"
    launcher.write_text("from client.__main__ import main\n\nif __name__ == '__main__':\n    raise SystemExit(main())\n", encoding="utf8")
    icon_path = ROOT/"client"/"audimate.ico"

    vendor = ROOT/"server"/"web"/"vendor"
    data = [(ROOT/"client"/"qml", "client/qml"),
            (icon_path, "client"),
            (ROOT/"client"/"fonts", "client/fonts"),
            (ROOT/"client"/"licenses"/"qt", "licenses/Qt"),
            (vendor/"flac-1.5.0.wasm", "server/web/vendor"),
            (vendor/"flac-LICENSE.txt", "licenses/flac"),
            (vendor/"flac-wasi-NOTICES.txt", "licenses/flac"),
            (Path(sys.base_prefix)/"LICENSE.txt", "licenses/python")]
    # Some Python distributions ship a public CA distrust list next to ssl.py.
    # The frozen stdlib resolves this path relative to the extraction root.
    distrust_list = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
    if distrust_list.is_file():
        data.append((distrust_list, "."))
    if args.server:
        address = urlsplit(args.server)
        if (address.scheme != "https" or not address.hostname or address.username is not None
                or address.password is not None or address.path not in ("", "/") or address.query or address.fragment):
            parser.error("--server requires a public HTTPS origin")
        endpoint = BUILD/"server.local.txt"
        endpoint.write_text(args.server.rstrip("/")+"\n", encoding="utf8")
        data.append((endpoint, "client"))

    for name in ("PySide6-Essentials", "shiboken6", "numpy", "Pillow", "sounddevice", "livekit",
                 "wasmtime", "protobuf", "cffi", "aiofiles", "importlib_resources"):
        dist = distribution(name)
        for entry in dist.files or ():
            if any(p.lower() == "licenses" for p in entry.parts) or entry.name.lower().startswith(("license", "copying", "notice")):
                data.append((Path(dist.locate_file(entry)), str(Path("licenses")/name/entry.parent)))

    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
               "--name", "Audimate-Windows-x64", "--icon", str(icon_path), "--noupx",
               "--paths", str(ROOT), "--specpath", str(BUILD), "--workpath", str(BUILD/"analysis"),
               "--distpath", str(DIST), "--add-binary", f"{dll};build/Release",
               "--hidden-import", "livekit.rtc.resources", "--hidden-import", "google._upb._message",
               "--collect-binaries", "livekit.rtc", "--collect-binaries", "wasmtime"]
    for source, target in data:
        command.extend(["--add-data", f"{source};{target}"])
    subprocess.run([*command, str(launcher)], check=True, cwd=ROOT)
    print(DIST/"Audimate-Windows-x64.exe")


if __name__ == "__main__":
    main()
