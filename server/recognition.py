"""Per-room recognition. Only immutable slices of the playback history leave the room lock."""
import asyncio
from importlib.util import find_spec
import io
import threading
import time
import unicodedata
from urllib.parse import urlsplit
import wave

WINDOW_S = 12
QUICK_S = 5
QUERY_TIMEOUT_S = 8
AVAILABLE = find_spec("shazamio") is not None


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def matches_title(track, title):
    return all(normalize(track[k]) == normalize(title[k]) for k in ("title", "artist"))


def cover_url(value):
    if isinstance(value, str) and len(value) <= 2048:
        url = urlsplit(value)
        if url.scheme == "https" and url.netloc.endswith((".mzstatic.com", ".shazam.com")) and "@" not in url.netloc:
            return value
    return ""


def track_info(answer):
    matches, track = answer.get("matches"), answer.get("track")
    if not matches or not track:
        return None
    def field(name):
        value = track.get(name)
        return value.strip()[:240] if isinstance(value, str) else ""
    result = dict(id=field("key"), title=field("title"), artist=field("subtitle"))
    if not all(result.values()):
        return None
    for section in track.get("sections") or ():
        if section.get("type") == "SONG":
            for item in section.get("metadata") or ():
                key = {"Album": "album", "Released": "released"}.get(item.get("title"))
                if key and isinstance(item.get("text"), str):
                    result[key] = item["text"][:240]
    result["cover"] = cover_url((track.get("images") or {}).get("coverart"))
    return result


class QueryTrace:
    """Per-query milestones from the SDK's HTTP client. Never retain request or response bodies."""

    def __init__(self):
        self.responses = []
        self.mark("准备音频")

    def mark(self, stage):
        self.stage = stage
        self.since = time.monotonic()

    def attach(self, config):
        async def request_start(session, ctx, params):
            self.responses.append(None)
            self.mark("准备连接")

        async def response_headers(session, ctx, params):
            self.responses[-1] = params.response.status
            self.mark("收到响应头，等待读取或重试")

        def milestone(stage):
            async def record(session, ctx, params):
                self.mark(stage)
            return record

        config.on_request_start.append(request_start)
        config.on_connection_create_start.append(milestone("建立 TCP/TLS 连接"))
        config.on_dns_resolvehost_start.append(milestone("解析 DNS"))
        config.on_dns_resolvehost_end.append(milestone("建立 TCP/TLS 连接"))
        config.on_request_headers_sent.append(milestone("发送请求体"))
        config.on_request_chunk_sent.append(milestone("等待上游响应头"))
        config.on_request_end.append(response_headers)
        self.mark("生成指纹并准备请求")

    def http_summary(self):
        if not self.responses:
            return "尚未发起 HTTP 请求"
        statuses = " → ".join(str(code) if code is not None else "未收到响应头" for code in self.responses)
        return f"HTTP 尝试 {len(self.responses)} 次（{statuses}）"


async def identify(wav, trace=None):
    from shazamio import Shazam
    async with Shazam(language="en-US") as service:
        if trace is not None:
            trace.attach(service.http_client.trace_config)
        answer = await asyncio.wait_for(service.recognize(wav), timeout=QUERY_TIMEOUT_S)
    return track_info(answer)


def encode_window(parts, rate, width, quick):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((2, width, rate, 0, "NONE", ""))
        for part in parts:
            wav.writeframesraw(part)
        if quick:
            wav.writeframesraw(bytes((WINDOW_S - QUICK_S) * rate * width * 2))
    return output.getvalue()


class Recognition:
    def __init__(self, first_frame, title):
        self.first_frame = first_frame
        self.title = title
        self.quick_pending = bool(title)
        self.last_end = None
        self.last_start = 0
        self.current = self.candidate = None
        self.notice = ""

    def view(self):
        return dict(current=self.current, candidate=self.candidate if not self.title else None, notice=self.notice)

    def receive(self, match, quick):
        self.notice = ""
        if self.title and match and not matches_title(match, self.title):
            match = None
        if quick:
            if match:
                self.current = match
            return
        if match and self.current and match["id"] == self.current["id"]:
            self.candidate = None
        elif match and self.candidate and match["id"] == self.candidate["id"]:
            self.current, self.candidate = match, None
        else:
            if self.candidate:
                self.current = None
            self.candidate = match

    def schedule(self, room, rate, width):
        # Called under the room lock, once per appended block. No PCM copies or IO here.
        if room.recognition_busy or (self.title and self.current):
            return
        history = room.history
        end = history.end_frame
        earliest = max(self.first_frame, history.blocks[0][2]) if history.blocks else end
        if self.quick_pending and earliest > self.first_frame:
            self.quick_pending = False
        quick = self.quick_pending
        frames = (QUICK_S if quick else WINDOW_S) * rate
        if end - earliest < frames:
            return
        now = time.monotonic()
        interval = room.policy["recognition_interval_s"]
        # A short operator polling interval must still provide distinct evidence for a double check.
        fresh_seconds = max(min(interval, WINDOW_S), WINDOW_S - 2 if self.candidate else 0)
        if not quick and self.last_end is not None and (
                end - self.last_end < fresh_seconds * rate or now - self.last_start < interval):
            return
        stop = self.first_frame + frames if quick else end
        start = stop - frames
        stride = width * 2
        parts = [memoryview(pcm)[(max(start, left)-left)*stride:(min(stop, right)-left)*stride]
                 for _, pcm, left, right, *_ in history.blocks if right > start and left < stop]
        if quick:
            self.quick_pending = False
        else:
            self.last_start, self.last_end = now, end
        room.recognition_busy = True
        threading.Thread(target=self.run, args=(room, parts, rate, width, quick),
                         name="song-recognition", daemon=True).start()

    def run(self, room, parts, rate, width, quick):
        with room.lock:
            if room.recognition is not self:
                room.recognition_busy = False
                return
            monitor = room.monitor
            trace = QueryTrace() if monitor.development and monitor.level != "off" else None
        started = time.monotonic()
        note = error = ""
        match = None
        try:
            match = asyncio.run(identify(encode_window(parts, rate, width, quick), trace))
            outcome = "命中" if match else "未匹配"
        except asyncio.TimeoutError as exc:
            outcome, note = "超时", "识别超时，稍后重试"
            error = f"{type(exc).__name__} · 查询时限 {QUERY_TIMEOUT_S:g} 秒"
        except Exception as exc:
            outcome, note = "失败", "识别暂不可用，稍后重试"
            error = type(exc).__name__
            if isinstance(exc, OSError) and isinstance(exc.errno, int):
                error += f" (errno {exc.errno})"
        finished = time.monotonic()
        with room.lock:
            room.recognition_busy = False
            if room.recognition is not self:
                return
            if note:
                self.notice = note
            else:
                self.receive(match, quick)
            if trace and (monitor.level == "debug" or (error and monitor.level == "errors")):
                mode = "Quick Fetch" if quick else "完整窗口"
                summary = f"{mode} · {outcome} · {round((finished-started)*1000)} ms"
                details = []
                if error:
                    details.extend((error, f"{trace.stage} {round((finished-trace.since)*1000)} ms"))
                elif match:
                    summary += f" · {match['title']} - {match['artist']}"
                    if self.title and not matches_title(match, self.title):
                        decision = "与播放器标题不符，未采用"
                    elif self.current and self.current["id"] == match["id"]:
                        decision = "已确认"
                    else:
                        decision = "候选，等待二次确认"
                    details.extend((decision, "Shazam ID " + match["id"]))
                    details.extend(match[k] for k in ("album", "released") if match.get(k))
                details.append(trace.http_summary())
                monitor.event("识别", summary, detail=" · ".join(details), error=bool(error))
