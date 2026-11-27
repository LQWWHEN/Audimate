"""Authenticated room transport shared by desktop controls and audio threads."""
from contextlib import closing, contextmanager
import http.client
import time
import json
import os
from pathlib import Path
import queue
import secrets
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


@contextmanager
def timed(metrics, phase, field):
    if metrics is None:
        yield
        return
    start = time.monotonic()
    try:
        yield
    except Exception as exc:
        metrics.update(phase=phase, timed_out=int(isinstance(exc, TimeoutError)),elapsed_ms=(time.monotonic()-start)*1000)
        raise
    finally:
        metrics[field] = (time.monotonic()-start)*1000


class API:
    def __init__(self, address):
        url = urlsplit(address.strip().rstrip("/"))
        if (not url.hostname or url.username or url.password or url.path or url.query or url.fragment or
                not (url.scheme == "https" or url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError("请输入 HTTPS 服务地址；本机隧道可使用 http://127.0.0.1")
        self.url = url
        self.cookie = ""
        self.logging = "debug"
        self.timing = False
        self.telemetry = queue.Queue(8)
        self.pending_fault = None
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
        if self.logging == "off" or self.logging == "errors" and (event in ("publish", "play") or self.timing and event == "sync"):
            return
        sample = dict(stream=stream, event=event, transport=transport, **metrics)
        if not self.timing:
            sample.pop("detail", None)
        fault = "detail" in sample and event in ("error", "disconnect", "overload", "gap")
        if fault:
            sample["report_id"] = secrets.token_hex(8)
        with self.report_lock:
            if self.telemetry.full():
                if not fault:
                    return
                self.pending_fault = sample
            else:
                self.telemetry.put_nowait(sample)
            if not self.reporter or not self.reporter.is_alive():
                self.reporter = threading.Thread(target=self._send_reports, daemon=True)
                self.reporter.start()

    def _send_reports(self):
        while True:
            with self.report_lock:
                if self.pending_fault is not None:
                    sample, self.pending_fault = self.pending_fault, None
                elif self.telemetry.empty():
                    self.reporter = None
                    return
                else:
                    sample = self.telemetry.get_nowait()
            if (not self.cookie or self.logging == "off" or self.logging == "errors" and
                    sample["event"] in ("publish", "play", "sync")):
                continue
            if not self.timing:
                sample.pop("detail", None)
                sample.pop("report_id", None)
            detailed = "detail" in sample
            logger = logging.getLogger("audimate.desktop.dev" if detailed else "audimate.desktop")
            try:
                if not logger.handlers:
                    directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Audimate"
                    directory.mkdir(parents=True, exist_ok=True)
                    filename = "transport-dev.log" if detailed else "transport.log"
                    handler = RotatingFileHandler(directory/filename, maxBytes=262144, backupCount=2, encoding="utf-8")
                    logger.propagate = False
                    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                    logger.addHandler(handler)
                    logger.setLevel(logging.INFO)
                logger.info("%s", dict(sample, stream=sample["stream"][:8]))
            except OSError:
                pass
            cookie = self.cookie
            for attempt in range(2 if "report_id" in sample else 1):
                if self.cookie != cookie or self.logging == "off" or (detailed and not self.timing):
                    break
                try:
                    self.request("diagnostics", sample)
                    break
                except (RoomError, ssl.SSLCertVerificationError, ValueError):
                    break
                except (OSError, http.client.HTTPException):
                    if "report_id" in sample and attempt == 0:
                        time.sleep(.3)

    def connection(self):
        args = (self.url.hostname, self.url.port)
        if self.url.scheme == "https":
            return http.client.HTTPSConnection(*args, timeout=6, context=self.tls)
        return http.client.HTTPConnection(*args, timeout=6)

    def request(self, path, data=None, *, metadata=False, timing=None):
        with closing(self.connection()) as conn:
            return self.request_on(conn, path, data, metadata=metadata, timing=timing)

    def request_on(self, conn, path, data=None, *, metadata=False, timing=None):
        raw = isinstance(data, bytes)
        headers = {"User-Agent": "Audimate-Desktop/1"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if data is not None:
            headers["Content-Type"] = "application/octet-stream" if raw else "application/json"
        payload = data if raw or data is None else json.dumps(data).encode()
        if timing is not None:
            timing["reused"] = int(conn.sock is not None)
            if conn.sock is None:
                with timed(timing, "connect", "connect_ms"):
                    conn.connect()
        with timed(timing, "send", "send_ms"):
            conn.request("GET" if data is None else "POST", "/api/" + path, payload, headers)
        with timed(timing, "headers", "headers_ms"):
            response = conn.getresponse()
        with response as resp:
            if timing is not None:
                timing["http_status"] = resp.status
            with timed(timing, "body", "body_ms"):
                blob = resp.read()
            if resp.status >= 400:
                if timing is not None:
                    timing["phase"] = "headers"
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


class AudioUpload(threading.Thread):
    def __init__(self, api, stream, host=None):
        super().__init__(daemon=True)
        self.host = host or {}
        self.api, self.stream = api, stream
        self.cancel = threading.Event()
        self.rate = self.host.get("rate", 48000)
        self.frame_bytes = self.host.get("bits", 16) // 8 * 2
        self.block_ms = self.host.get("audio_block_ms", 200)
        self.packet_bytes = round(self.rate * self.block_ms / 1000) * self.frame_bytes
        self.buffer = bytearray()
        self.packets = queue.Queue(max(1, 400 // self.block_ms))
        self.dropped = 0
        self.notice = ""
        self.error = ""
        self.source_seq = 0
        self.pack_start = 0

    def add(self, block):
        pcm = bytes(block.pcm)
        if not self.buffer and self.api.timing:
            self.pack_start = time.monotonic()
        self.buffer.extend(pcm)
        if len(self.buffer) < self.packet_bytes:
            return
        if self.packets.full():
            try:
                self.packets.get_nowait()
                self.dropped += 1
                self.notice = "上行受限，已丢弃旧音频。可手动降低共享音质。"
            except queue.Empty:
                pass
        self.source_seq += 1
        queued = time.monotonic()
        detail = None
        if self.api.timing and self.pack_start:
            detail = dict(source_seq=self.source_seq, packet_bytes=len(self.buffer), queue_depth=self.packets.qsize(),
                          pack_ms=(queued-self.pack_start)*1000)
            if self.api.clock_error is not None:
                detail["source_clock_error_ms"] = self.api.clock_error
            if hasattr(block, "capture_read_ms"):
                detail["capture_read_ms"] = block.capture_read_ms
        self.pack_start = 0
        duration_ms = len(self.buffer) * 1000 / (self.rate * self.frame_bytes)
        self.packets.put_nowait((bytes(self.buffer), time.time()*1000-duration_ms, queued, detail))
        self.buffer.clear()

    def run(self):
        clock_timing = {} if self.api.timing else None
        try:
            with timed(clock_timing, "clock", "clock_sync_ms"):
                self.api.calibrate()
            with closing(self.api.connection()) as conn:
                self.send_packets(conn)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if clock_timing and clock_timing.get("phase") == "clock":
                self.api.report(self.stream,"error","pcm",{"detail":clock_timing})
            self.error = str(exc)

    def send_packets(self, conn):
        reported = 0
        reconnecting = False
        while not self.cancel.is_set():
            try:
                pcm, captured, queued, detail = self.packets.get(timeout=0.2)
            except queue.Empty:
                continue
            if self.cancel.is_set():
                return
            start = time.monotonic()
            path = f"audio?stream={self.stream}&captured={captured+self.api.offset:.2f}"
            if self.host.get("compression") == "flac":
                from server.flac import encode
                pcm = encode(pcm, self.host["rate"], self.host["bits"])
                path += "&compression=flac"
                if detail is not None:
                    detail.update(encode_ms=(time.monotonic()-start)*1000, packet_bytes=len(pcm))
            if self.api.timing and detail:
                path += f"&packet={detail['source_seq']}&sent={round(time.time()*1000+self.api.offset)}"
                detail["queue_ms"] = (start-queued)*1000
            else:
                detail = None
            try:
                ack = self.api.request_on(conn, path, pcm, timing=detail)
                if detail and "server_seq" in ack:
                    detail["server_seq"] = ack["server_seq"]
            except (RoomError, ssl.SSLCertVerificationError):
                if detail:
                    detail.setdefault("phase", "upload")
                    self.api.report(self.stream, "error", "pcm", {"detail": detail})
                raise
            except (OSError, http.client.HTTPException):
                conn.close()
                # The server may already have appended this packet. Reconnect on the next one.
                metrics = {"unconfirmed": 1}
                if detail:
                    detail.update(unconfirmed=1, upload_ms=(time.monotonic()-start)*1000)
                    metrics["detail"] = detail
                self.api.report(self.stream, "error", "pcm", metrics)
                if reconnecting:
                    raise
                reconnecting = True
                self.notice = "音频包未收到上传确认，正在重新连接。"
                continue
            reconnecting = False
            sampled = detail["source_seq"] % 10 == 1 if detail else time.monotonic()-reported >= 2
            upload_ms = (time.monotonic()-start)*1000
            if sampled or (detail and (upload_ms > self.block_ms or self.dropped)):
                metrics = dict(upload_ms=upload_ms, discarded=self.dropped)
                if detail:
                    metrics["detail"] = detail
                self.api.report(self.stream, "overload" if self.dropped else "publish", "pcm",
                                metrics)
                self.dropped = 0
                reported = time.monotonic()


class AudioListener(threading.Thread):
    def __init__(self, api, host):
        super().__init__(daemon=True)
        self.api, self.host = api, host
        self.cancel = threading.Event()
        self.error = self.notice = ""
        self.packets = queue.Queue(max(1, 800 // host.get("audio_block_ms", 200)))
        self.lag = None
        self.sync_sample = None

    def play(self):
        rate, bits = (48000, 16) if self.host.get("codec") == "opus" else (self.host["rate"], self.host["bits"])
        detail = {} if self.api.timing else None
        try:
            from .playback import Output
            with Output(rate, bits) as output:
                output.observe = getattr(self, "observe", None)
                reported, underruns = 0, 0
                first = True
                while not self.cancel.is_set():
                    try:
                        pcm, captured, behind, fetched, detail = self.packets.get(timeout=.2)
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
                    self.sync_sample = (time.monotonic(), self.lag)
                    if detail is not None:
                        detail["play_queue_ms"] = (time.monotonic()-fetched)*1000
                    with timed(detail, "output", "device_ms"):
                        underflow = bool(output.write(pcm))
                    underruns += underflow
                    if detail and "source_seq" in detail:
                        sampled = detail["source_seq"] % 10 == 1
                    else:
                        sampled = time.monotonic()-reported >= 2
                    if sampled or (detail and underflow):
                        metrics = dict(buffer_ms=buffered, timeline_ms=timeline, underruns=underruns)
                        if detail:
                            metrics["detail"] = detail
                        if age is not None:
                            metrics.update(playout_ms=max(0,self.lag),clock_error_ms=self.api.clock_error)
                        self.api.report(self.host["stream"], "gap" if detail and underflow else "play", "pcm", metrics)
                        reported = time.monotonic()
        except Exception as exc:
            if detail is not None and self.api.timing:
                detail.setdefault("phase", "output")
                self.api.report(self.host["stream"], "error", "pcm", {"detail":detail})
            self.error = str(exc)
            self.cancel.set()

    def run(self):
        playback = threading.Thread(target=self.play, daemon=True)
        seq = -1
        empty_reads = 0
        poll_start = None
        detail = {} if self.api.timing else None
        try:
            with timed(detail, "clock", "clock_sync_ms"):
                self.api.calibrate()
            playback.start()
            while not self.cancel.is_set():
                if self.packets.full():
                    self.cancel.wait(.025)
                    continue
                path = f"audio?stream={self.host['stream']}&after={seq}&sync={int(seq<0)}"
                compressed = self.host.get("compression") == "flac"
                if compressed:
                    path += "&compression=flac"
                detail = {} if self.api.timing else None
                start = time.monotonic()
                result = self.api.request(path + ("&trace=1" if detail is not None else ""), metadata=True, timing=detail)
                if result:
                    new_seq, pcm, headers = result
                    fetched = time.monotonic()
                    if detail is not None:
                        detail.update(json.loads(headers.get("X-Audimate-Timing", "{}")), server_seq=new_seq,
                                      fetch_ms=(fetched-start)*1000, empty_reads=empty_reads,
                                      poll_ms=(start-poll_start)*1000 if poll_start is not None else 0)
                        if "X-Audimate-Served-Ms" in headers:
                            delay = time.time()*1000+self.api.offset-float(headers["X-Audimate-Served-Ms"])
                            if 0 <= delay <= 60000:
                                detail["downlink_est_ms"] = delay
                    empty_reads, poll_start = 0, None
                    if compressed != (headers.get("Content-Type") == "audio/flac"):
                        raise ValueError("服务端返回的音频与主持公布的压缩格式不符")
                    if compressed:
                        from server.flac import decode
                        with timed(detail, "decode", "decode_ms"):
                            pcm = decode(pcm, self.host["rate"], self.host["bits"])
                        fetched = time.monotonic()
                    if headers.get("X-Reset") == "1":
                        self.notice = "已到缓存边界，正在重新同步"
                    seq = new_seq
                    self.position = seq
                    captured = headers.get("X-Captured-Ms")
                    self.packets.put_nowait((pcm, float(captured) if captured else None, float(headers["X-Behind-Ms"]), fetched, detail))
                else:
                    empty_reads += 1
                    if poll_start is None:
                        poll_start = start
                    self.cancel.wait(.04)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if self.api.timing and detail is not None:
                detail.setdefault("phase", "fetch")
                self.api.report(self.host["stream"], "error", "pcm", {"detail":detail})
            self.error = str(exc)
        finally:
            self.cancel.set()
            if playback.ident:
                playback.join()
