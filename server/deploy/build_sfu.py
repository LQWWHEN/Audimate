"""Build the pinned Audimate SFU using Go, git and the upstream module checksums."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--goos", choices=("windows","linux"), default="linux")
    args = parser.parse_args()
    patch = Path(__file__).with_name("livekit-native-stereo.patch").resolve()
    version = "1.13.11"
    with tempfile.TemporaryDirectory() as folder:
        archive = Path(folder)/"source.tgz"
        url = f"https://codeload.github.com/livekit/livekit/tar.gz/refs/tags/v{version}"
        with urlopen(url,timeout=60) as source, archive.open("wb") as dest:
            shutil.copyfileobj(source,dest)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != "c8d9b7ba9d66c8b5f314a438c0c2493815e7e70085e37706b888cab3c4ab8f8d":
            raise SystemExit("LiveKit source archive checksum mismatch")
        with tarfile.open(archive) as tar:
            tar.extractall(folder,filter="data")
        checkout = Path(folder)/("livekit-"+version)
        subprocess.run(["git","apply","--no-index",str(patch)],cwd=checkout,check=True)
        subprocess.run(["go","build","-trimpath","-o",str(args.output.resolve()),"./cmd/server"],
                       cwd=checkout,check=True,env=dict(os.environ,CGO_ENABLED="0",GOOS=args.goos,GOARCH="amd64"))
