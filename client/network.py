"""Authenticated room transport shared by desktop controls and audio threads."""
import http.client
import time
import json
import os
from pathlib import Path
import queue
import ssl
import threading
import logging
from logging.handlers import RotatingFileHandler
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
        self.logging = "debug"
        self.telemetry = queue.Queue(8)
        self.report_lock = threading.Lock()
        self.reporter = None
        self.offset = self.clock_error = None
        self.tls = ssl.create_default_context(cafile=os.environ.get("AUDIMATE_CA_FILE") or None)

    def calibrate(self):
        samples = []
        for _ in range(3):
            start = time.time()*1000
            stamp = self.request("clock")["unix_ms"]
            end = time.time()*1000
            samples.append(((end-start)/2, stamp-(start+end)/2))
        self.clock_error, self.offset = min(samples)

    def report(self, stream, event, transport, metrics):
        if self.logging == "off" or self.logging == "errors" and event in ("publish", "play"):
            return
        sample = dict(stream=stream, event=event, transport=transport, **metrics)
        with self.report_lock:
            if self.telemetry.full():
                return
            self.telemetry.put_nowait(sample)
            if not self.reporter or not self.reporter.is_alive():
                self.reporter = threading.Thread(target=self._send_reports, daemon=True)
                self.reporter.start()

    def _send_reports(self):
        logger = logging.getLogger("audimate.desktop")
        while True:
            with self.report_lock:
                if self.telemetry.empty():
                    self.reporter = None
                    return
                sample = self.telemetry.get_nowait()
            if self.logging == "off" or not self.cookie:
                continue
            try:
                if not logger.handlers:
                    directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Audimate"
                    directory.mkdir(parents=True, exist_ok=True)
                    handler = RotatingFileHandler(directory/"transport.log", maxBytes=262144, backupCount=2, encoding="utf-8")
                    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                    logger.addHandler(handler)
                    logger.setLevel(logging.INFO)
                logger.info("%s", dict(sample, stream=sample["stream"][:8]))
            except OSError:
                pass
            try:
                self.request("diagnostics", sample)
            except (ValueError, OSError, http.client.HTTPException):
                pass

    def request(self, path, data=None, *, metadata=False):
        raw = isinstance(data, bytes)
        headers = {"User-Agent": "Audimate-Desktop/1"}
        if self.cookie:
            headers["Cookie"] = self.cookie
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
                result = int(resp.getheader("X-Sequence")), blob
                return (*result, dict(resp.getheaders())) if metadata else result
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
        self.dropped = 0
        self.notice = ""
        self.error = ""

    def add(self, block):
        pcm = bytes(block.pcm)
        self.buffer.extend(pcm)
        if len(self.buffer) < len(pcm) * 20:
            return
        if self.packets.full():
            try:
                self.packets.get_nowait()
                self.dropped += 1
                self.notice = "上行受限，已丢弃旧音频。可手动降低共享音质。"
            except queue.Empty:
                pass
        self.packets.put_nowait((bytes(self.buffer), time.time()*1000-200))
        self.buffer.clear()

    def run(self):
        try:
            self.api.calibrate()
            reported = 0
            while not self.cancel.is_set():
                try:
                    pcm, captured = self.packets.get(timeout=0.2)
                except queue.Empty:
                    continue
                start = time.monotonic()
                self.api.request(f"audio?stream={self.stream}&captured={captured+self.api.offset:.2f}", pcm)
                if time.monotonic()-reported >= 2:
                    self.api.report(self.stream, "overload" if self.dropped else "publish", "pcm",
                                    dict(upload_ms=(time.monotonic()-start)*1000, discarded=self.dropped))
                    self.dropped = 0
                    reported = time.monotonic()
        except (OSError, ValueError, http.client.HTTPException) as exc:
            self.error = str(exc)


class AudioListener(threading.Thread):
    def __init__(self, api, host):
        super().__init__(daemon=True)
        self.api, self.host = api, host
        self.cancel = threading.Event()
        self.error = self.notice = ""
        self.packets = queue.Queue(4)
        self.lag = None

    def play(self):
        rate, bits = (48000, 16) if self.host.get("codec") == "opus" else (self.host["rate"], self.host["bits"])
        try:
            from .playback import Output
            with Output(rate, bits) as output:
                reported, underruns = 0, 0
                first = True
                while not self.cancel.is_set():
                    try:
                        pcm, captured, behind, fetched = self.packets.get(timeout=.2)
                    except queue.Empty:
                        continue
                    age = time.time()*1000 + self.api.offset - captured if captured is not None else None
                    if first:
                        self.cancel.wait(max(0, min(.6,(600-(age if age is not None else behind))/1000)))
                        first = False
                    if self.cancel.is_set():
                        break
                    buffered = self.packets.qsize()*len(pcm)*1000/(rate*bits/8*2)
                    timeline = behind + (time.monotonic()-fetched)*1000
                    self.lag = time.time()*1000+self.api.offset-captured if captured is not None else timeline
                    underruns += bool(output.write(pcm))
                    if time.monotonic()-reported >= 2:
                        metrics = dict(buffer_ms=buffered, timeline_ms=timeline, underruns=underruns)
                        if age is not None:
                            metrics.update(playout_ms=max(0,self.lag),clock_error_ms=self.api.clock_error)
                        self.api.report(self.host["stream"], "play", "pcm", metrics)
                        reported = time.monotonic()
        except Exception as exc:
            self.error = str(exc)
            self.cancel.set()

    def run(self):
        playback = threading.Thread(target=self.play, daemon=True)
        seq = -1
        try:
            self.api.calibrate()
            playback.start()
            while not self.cancel.is_set():
                if self.packets.full():
                    self.cancel.wait(.025)
                    continue
                result = self.api.request(f"audio?stream={self.host['stream']}&after={seq}&sync={int(seq<0)}", metadata=True)
                if result:
                    new_seq, pcm, headers = result
                    if headers.get("X-Reset") == "1":
                        while not self.packets.empty():
                            try:
                                self.packets.get_nowait()
                            except queue.Empty:
                                break
                        self.notice = "已到缓存边界，正在重新同步"
                    seq = new_seq
                    captured = headers.get("X-Captured-Ms")
                    self.packets.put_nowait((pcm, float(captured) if captured else None, float(headers["X-Behind-Ms"]), time.monotonic()))
                else:
                    self.cancel.wait(.04)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            self.error = str(exc)
        finally:
            self.cancel.set()
            if playback.ident:
                playback.join()
