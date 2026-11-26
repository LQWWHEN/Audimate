"""Authenticated room transport shared by desktop controls and audio threads."""
import http.client
import io
import json
import os
from pathlib import Path
import queue
import ssl
import threading
import wave
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

def default_server():
    if os.environ.get("AUDIMATE_SERVER"):
        return os.environ["AUDIMATE_SERVER"]
    local = Path(__file__).with_name("server.local.txt")
    return local.read_text(encoding="utf-8").strip() if local.is_file() else "http://127.0.0.1:17881"


class RoomError(OSError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f"HTTP {status} · {message}")


class API:
    def __init__(self, address):
        url = urlsplit(address.strip().rstrip("/"))
        if (not url.hostname or url.username or url.password or url.path or url.query or url.fragment or
                not (url.scheme == "https" or url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError("请输入 HTTPS 服务地址；本机隧道可使用 http://127.0.0.1")
        self.url = url
        self.cookie = ""
        self.tls = ssl.create_default_context(cafile=os.environ.get("AUDIMATE_CA_FILE") or None)

    def request(self, path, data=None):
        raw = isinstance(data, bytes)
        headers = {"Cookie": self.cookie} if self.cookie else {}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream" if raw else "application/json"
        payload = data if raw or data is None else json.dumps(data).encode()
        args = (self.url.hostname, self.url.port)
        conn = (http.client.HTTPSConnection(*args, timeout=6, context=self.tls) if self.url.scheme == "https"
                else http.client.HTTPConnection(*args, timeout=6))
        try:
            conn.request("GET" if data is None else "POST", "/api/" + path, payload, headers)
            resp = conn.getresponse()
            blob = resp.read()
            if resp.status >= 400:
                try:
                    message = json.loads(blob)["error"]
                except (KeyError, ValueError, UnicodeError):
                    message = resp.reason
                raise RoomError(resp.status, message)
            if resp.getheader("Set-Cookie"):
                cookie = SimpleCookie(resp.getheader("Set-Cookie"))
                self.cookie = "audimate=" + cookie["audimate"].value if cookie["audimate"].value else ""
            if resp.status == 204:
                return None
            if resp.getheader("X-Sequence"):
                return int(resp.getheader("X-Sequence")), blob
            return json.loads(blob)
        finally:
            conn.close()


class AudioUpload(threading.Thread):
    def __init__(self, api, stream):
        super().__init__(daemon=True)
        self.api, self.stream = api, stream
        self.cancel = threading.Event()
        self.buffer = bytearray()
        self.packets = queue.Queue(2)
        self.error = ""

    def add(self, block):
        pcm = bytes(block.pcm)
        self.buffer.extend(pcm)
        if len(self.buffer) < len(pcm) * 20:
            return
        if self.packets.full():
            try:
                self.packets.get_nowait()
            except queue.Empty:
                pass
        self.packets.put_nowait(bytes(self.buffer))
        self.buffer.clear()

    def run(self):
        try:
            while not self.cancel.is_set():
                try:
                    pcm = self.packets.get(timeout=0.2)
                except queue.Empty:
                    continue
                self.api.request("audio?stream=" + self.stream, pcm)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            self.error = str(exc)


class AudioListener(threading.Thread):
    def __init__(self, api, host):
        super().__init__(daemon=True)
        self.api, self.host = api, host
        self.cancel = threading.Event()
        self.error = ""
        self.packets = queue.Queue(3)

    def play(self):
        import winsound
        try:
            while not self.cancel.is_set():
                try:
                    pcm = self.packets.get(timeout=0.2)
                except queue.Empty:
                    continue
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wav:
                    wav.setparams((2, self.host["bits"] // 8, self.host["rate"], 0, "NONE", ""))
                    wav.writeframes(pcm)
                winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        except (RuntimeError, OSError) as exc:
            self.error = str(exc)
            self.cancel.set()

    def run(self):
        playback = threading.Thread(target=self.play, daemon=True)
        playback.start()
        seq = -1
        try:
            while not self.cancel.is_set():
                if self.packets.full():
                    self.cancel.wait(0.04)
                    continue
                result = self.api.request(f"audio?stream={self.host['stream']}&after={seq}")
                if result:
                    seq, pcm = result
                    self.packets.put_nowait(pcm)
                else:
                    self.cancel.wait(0.06)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            self.error = str(exc)
        finally:
            self.cancel.set()
            playback.join()
