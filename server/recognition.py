"""Per-room recognition. Only immutable slices of the playback history leave the room lock."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import timezone
from email.utils import parsedate_to_datetime
from importlib.util import find_spec
import io
import logging
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
import wave

if __package__:
    from .control import DEFAULT, FUZZY_FIELDS
    from .song_matching import Credits, normalize, split_suffix
else:
    from control import DEFAULT, FUZZY_FIELDS
    from song_matching import Credits, normalize, split_suffix

WINDOW_S = 12
QUICK_S = 5
QUERY_TIMEOUT_S = 8
CATALOG_TIMEOUT_S = 3
RATE_LIMIT_COOLDOWN_S = 30
SESSION_IDLE_S = 60
_worker = ContextVar("recognition_worker", default=None)
_trace = ContextVar("fingerprint_trace", default=None)
AVAILABLE = find_spec("shazamio") is not None

_KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fa]")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]")


class SuffixFile:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.last_read = None
        self.pattern = re.compile(r"(?!)")

    def read(self):
        with self.lock:
            try:
                text = self.path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                error = (type(exc), getattr(exc, "errno", None))
                if self.last_read != error:
                    logging.getLogger(__name__).warning("无法读取曲名后缀配置 %s，沿用上次规则：%s", self.path, type(exc).__name__)
                self.last_read = error
                return self.pattern
            if text != self.last_read:
                labels = {normalize(line) for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")}
                values = "|".join(re.escape(label) for label in sorted(labels))
                year = r"(?:19|20)[0-9]{2}"
                tag = rf"(?:{year} )?(?:{values})(?: {year})?"
                self.pattern = re.compile(rf"(?:\s*\({tag}\)|\s*\[{tag}\]|\s+[-–—]\s*{tag})$" if labels else r"(?!)")
                self.last_read = text
            return self.pattern


SUFFIX_FILE = SuffixFile(os.getenv("AUDIMATE_RECOGNITION_SUFFIX_FILE") or
                         Path(__file__).with_name("deploy") / "recognition-suffixes.txt")


def song_title_match(left, right, suffixes=None):
    left, right = normalize(left), normalize(right)
    if left == right:
        return "exact"

    if suffixes is None:
        suffixes = SUFFIX_FILE.read()

    left_name, left_year = split_suffix(left, suffixes)
    right_name, right_year = split_suffix(right, suffixes)
    if left_name and left_name == right_name and not (left_year and right_year and left_year != right_year):
        return "suffix"
    return ""


def exact_names(track, title):
    return all(normalize(track[k]) == normalize(title[k]) for k in ("title", "artist"))


def title_agreement(track, title, suffixes=None, credits=None):
    if credits is None and exact_names(track, title):
        return "exact"
    suffixes = suffixes if suffixes is not None else SUFFIX_FILE.read()
    credits = credits or Credits.compare(track, title, suffixes)
    if credits.strong():
        if normalize(track["artist"]) == normalize(title["artist"]):
            reason = song_title_match(track["title"], title["title"], suffixes)
            if reason:
                return reason
        return "credits"
    return ""


def catalog_locales(title, match):
    names = normalize(title["title"] + " " + title["artist"])
    if _KANA.search(names):
        return (("ja-JP", "JP"),)
    if _HAN.search(names):
        return (("zh-CN", "CN"), ("zh-TW", "TW"), ("ja-JP", "JP"))
    returned = normalize(match["title"] + " " + match["artist"])
    if _KANA.search(returned) or _HAN.search(returned):
        return (("en-US", "US"),)
    return ()


def retry_after_seconds(value):
    if not value:
        return None
    value = value.strip()
    try:
        if re.fullmatch(r"[0-9]+", value):
            # Saturate oversized HTTP integers before adding them to a monotonic deadline.
            return int(min(float(value), 2**31 - 1))
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, math.ceil(date.timestamp() - time.time()))
    except (ValueError, TypeError, OverflowError, OSError):
        return None


class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__("HTTP 429")
        delay = retry_after_seconds(retry_after)
        self.seconds = RATE_LIMIT_COOLDOWN_S if delay is None else delay
        self.source = "默认" if delay is None else "Retry-After"
        self.retry_at = time.monotonic() + self.seconds


@asynccontextmanager
async def shazam_service(*, trace=None, language, endpoint_country="GB"):
    from shazamio import Shazam
    worker = _worker.get()
    owner = worker.provider if worker else None
    if owner is None:
        owner = Shazam(language=language, endpoint_country=endpoint_country)
        QueryTrace.attach(owner.http_client.trace_config)
        if worker:
            worker.provider = owner
    service = owner
    if (language, endpoint_country) != (owner.language, owner.endpoint_country):
        service = Shazam(language=language, endpoint_country=endpoint_country, http_client=owner.http_client)
    token = _trace.set(trace)
    try:
        if trace:
            trace.mark("生成指纹并准备请求")
        yield service
    finally:
        _trace.reset(token)
        if not worker:
            await owner.close()


async def localized_names(key, language, country):
    async with shazam_service(language=language, endpoint_country=country) as service:
        record = await service.track_about(int(key))
    if (isinstance(record, dict) and str(record.get("key")) == key and
            all(isinstance(record.get(k), str) and record[k].strip() for k in ("title", "subtitle"))):
        return dict(title=record["title"], artist=record["subtitle"])
    return None


async def check_catalog(track, player, locales, deadline, suffixes):
    original = Credits.compare(track, player, suffixes)
    artist_ok = original.left_artists == original.right_artists
    title_ok = not original.conflict and original.left_title == original.right_title
    problem = None
    for index, (language, country) in enumerate(locales):
        # Reserve time for another storefront when the first one is slow or unavailable.
        timeout = (deadline - time.monotonic()) / (len(locales) - index)
        try:
            alias = await asyncio.wait_for(localized_names(track["id"], language, country), timeout)
        except RateLimited:
            raise
        except Exception as exc:
            problem = type(exc).__name__
            continue
        if not alias:
            continue
        credits = Credits.compare(alias, player, suffixes)
        if credits.conflict:
            continue
        artist_ok = artist_ok or credits.left_artists == credits.right_artists
        title_ok = title_ok or (credits.left_title and credits.left_title == credits.right_title)
        if artist_ok and title_ok:
            return "localized", "同一 Shazam ID 的本地化曲名与歌手吻合", 120
    if problem:
        return "", f"本地化目录暂未核实（{problem}）", 30
    return "", "本地化目录无匹配", 120


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

    @staticmethod
    def attach(config):
        async def request_start(session, ctx, params):
            trace = _trace.get()
            if trace:
                trace.responses.append(None)
                trace.mark("准备连接")

        async def response_headers(session, ctx, params):
            trace = _trace.get()
            if trace:
                trace.responses[-1] = params.response.status
                trace.mark("收到响应头，等待读取或重试")
            if params.response.status == 429:
                limited = RateLimited(params.response.headers.get("Retry-After"))
                worker = _worker.get()
                if worker:
                    # A simultaneous step-down can cancel the task during SDK cleanup.
                    with worker.room.lock:
                        worker.room.recognition_retry_at = limited.retry_at
                raise limited

        def milestone(stage):
            async def record(session, ctx, params):
                trace = _trace.get()
                if trace:
                    trace.mark(stage)
            return record

        config.on_request_start.append(request_start)
        config.on_connection_create_start.append(milestone("建立 TCP/TLS 连接"))
        config.on_dns_resolvehost_start.append(milestone("解析 DNS"))
        config.on_dns_resolvehost_end.append(milestone("建立 TCP/TLS 连接"))
        config.on_request_headers_sent.append(milestone("发送请求体"))
        config.on_request_chunk_sent.append(milestone("等待上游响应头"))
        config.on_request_end.append(response_headers)

    def http_summary(self):
        if not self.responses:
            return "尚未发起 HTTP 请求"
        statuses = " → ".join(str(code) if code is not None else "未收到响应头" for code in self.responses)
        return f"HTTP 尝试 {len(self.responses)} 次（{statuses}）"


async def identify(wav, trace=None):
    async with shazam_service(language="en-US", trace=trace) as service:
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


class RecognitionWorker:
    """One serial executor owns the room's event loop and the SDK connection pool."""

    def __init__(self, room):
        self.room = room
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="room-shazam")
        self.runner = asyncio.Runner()
        self.provider = self.task = self.idle_since = None
        self.closed = False

    def expired(self, now):
        return self.idle_since is not None and now - self.idle_since >= SESSION_IDLE_S

    def submit(self, state, parts, rate, width, quick):
        self.idle_since = None
        self.executor.submit(self.work, state, parts, rate, width, quick)

    def work(self, state, parts, rate, width, quick):
        try:
            state.run(self.room, parts, rate, width, quick, execute=self.execute)
        finally:
            with self.room.lock:
                if not self.room.recognition_busy:
                    self.idle_since = time.monotonic()

    def execute(self, query):
        async def run():
            with self.room.lock:
                if self.closed:
                    query.close()
                    raise asyncio.CancelledError
                self.task = asyncio.current_task()
            token = _worker.set(self)
            try:
                return await query
            finally:
                _worker.reset(token)
                with self.room.lock:
                    self.task = None
        return self.runner.run(run())

    def close(self):
        # Called with the room lock. Socket cleanup stays on its owning thread.
        self.closed = True
        if self.task:
            self.task.get_loop().call_soon_threadsafe(self.task.cancel)
        self.executor.submit(self.close_connections)
        self.executor.shutdown(wait=False)

    def close_connections(self):
        try:
            if self.provider:
                self.runner.run(self.provider.close())
        finally:
            self.runner.close()


class Recognition:
    def __init__(self, first_frame, title):
        self.first_frame = first_frame
        self.title = title
        self.quick_pending = bool(title)
        self.last_end = None
        self.last_start = 0
        self.current = self.candidate = None
        self.notice = ""
        self.last_catalog = None

    def view(self):
        return dict(current=self.current, candidate=self.candidate if not self.title else None, notice=self.notice)

    def receive(self, match, quick, agreement=None):
        self.notice = ""
        if self.title and match:
            if agreement is None:
                agreement = title_agreement(match, self.title)
            if not agreement:
                match = None
            elif agreement not in ("partial", "fingerprint"):
                self.current, self.candidate = match, None
                return
        if quick:
            if agreement == "fingerprint":
                self.candidate = match
            return
        if match and self.current and match["id"] == self.current["id"]:
            self.candidate = None
        elif match and self.candidate and match["id"] == self.candidate["id"]:
            self.current, self.candidate = match, None
        else:
            if self.candidate:
                self.current = None
            self.candidate = match

    async def query(self, wav, trace, policy=None):
        deadline = time.monotonic() + QUERY_TIMEOUT_S
        match = await identify(wav, trace)
        if not match or not self.title:
            return match, "", "", None
        if exact_names(match, self.title):
            return match, "exact", "", None
        suffixes = SUFFIX_FILE.read()
        credits = Credits.compare(match, self.title, suffixes)
        policy = DEFAULT if policy is None else policy
        fingerprint = "fingerprint" if policy["recognition_fuzzy"] else ""
        if credits.conflict:
            return match, fingerprint, "录音版本标记不同", None
        agreement = title_agreement(match, self.title, suffixes, credits)
        if agreement:
            notes = {"suffix": "配置的曲名后缀等价", "credits": "合作署名一致"}
            return match, agreement, notes.get(agreement, ""), None
        agreement, note = credits.fallback(policy)
        if agreement:
            if agreement == "partial" and fingerprint:
                agreement = "fuzzy"
            return match, agreement, note, None
        candidate = self.candidate
        if fingerprint and candidate and candidate["id"] == match["id"]:
            return match, fingerprint, note, None
        limited = None
        try:
            agreement, catalog_note = await self.catalog_agreement(match, suffixes, deadline)
        except RateLimited as exc:
            limited = exc
            agreement, catalog_note = "", "本地化目录待核实"
        if agreement:
            return match, agreement, catalog_note, None
        return match, fingerprint, " · ".join(filter(None, (catalog_note, note))), limited

    async def catalog_agreement(self, match, suffixes, deadline):
        locales = catalog_locales(self.title, match)
        key = match["id"]
        if not locales or not re.fullmatch(r"[0-9]{1,20}", key):
            return "", ""
        if self.last_catalog:
            prior, prior_suffixes, expires, reason, note = self.last_catalog
            if prior == key and prior_suffixes == suffixes and expires > time.monotonic():
                return reason, note + " · 复用目录核对"

        # Only one worker uses this Recognition. Its cache expires with the player title.
        # The optional catalog check shares the original deadline and never discards a fingerprint hit.
        start = time.monotonic()
        reason, note, ttl = await check_catalog(match, self.title, locales, min(start + CATALOG_TIMEOUT_S, deadline), suffixes)
        self.last_catalog = (key, suffixes, time.monotonic()+ttl, reason, note)
        return reason, f"{note} · 目录核对 {round((time.monotonic()-start)*1000)} ms"

    def schedule(self, room, rate, width):
        # Called under the room lock, once per appended block. No PCM copies or IO here.
        if room.recognition_busy or (self.title and self.current):
            return
        now = time.monotonic()
        recovering = room.recognition_retry_at is not None
        if recovering and now < room.recognition_retry_at:
            return
        history = room.history
        end = history.end_frame
        earliest = max(self.first_frame, history.blocks[0][2]) if history.blocks else end
        if self.quick_pending and earliest > self.first_frame:
            self.quick_pending = False
        quick = self.quick_pending and not (recovering and end - earliest >= WINDOW_S * rate)
        frames = (QUICK_S if quick else WINDOW_S) * rate
        if end - earliest < frames:
            return
        interval = room.policy["recognition_interval_s"]
        # A short operator polling interval must still provide distinct evidence for a double check.
        fresh_seconds = max(min(interval, WINDOW_S), WINDOW_S - 2 if self.candidate else 0)
        if not quick and self.last_end is not None and (
                end - self.last_end < fresh_seconds * rate or now - self.last_start < interval):
            return
        stop = self.first_frame + frames if quick and not recovering else end
        start = stop - frames
        stride = width * 2
        parts = [memoryview(pcm)[(max(start, left)-left)*stride:(min(stop, right)-left)*stride]
                 for _, pcm, left, right, *_ in history.blocks if right > start and left < stop]
        self.quick_pending = False
        if not quick or recovering:
            self.last_start, self.last_end = now, end
        room.recognition_retry_at = None
        room.recognition_busy = True
        room.queue_recognition(self, parts, rate, width, quick)

    def run(self, room, parts, rate, width, quick, *, execute=asyncio.run):
        with room.lock:
            if room.recognition is not self:
                room.recognition_busy = False
                return
            monitor = room.monitor
            policy = room.policy
            trace = QueryTrace() if monitor.development and monitor.level != "off" else None
        started = time.monotonic()
        note = error = ""
        match = None
        limited = None
        agreement = evidence = ""
        try:
            match, agreement, evidence, limited = execute(self.query(encode_window(parts, rate, width, quick), trace, policy))
            outcome = "命中" if match else "未匹配"
        except asyncio.CancelledError:
            with room.lock:
                room.recognition_busy = False
            return
        except RateLimited as exc:
            limited = exc
            outcome = "上游限流"
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
            if agreement in ("fuzzy", "fingerprint") and any(room.policy[k] != policy[k] for k in FUZZY_FIELDS):
                agreement, evidence = "", "模糊匹配设置已更新，等待下一窗口"
            if limited:
                room.recognition_retry_at = limited.retry_at
                endpoint = "本地化目录" if match else "指纹查询"
                error = f"{endpoint} HTTP 429 · 冷却 {limited.seconds} 秒（{limited.source}）"
                if not agreement:
                    note = "识别限流，稍后重试"
            current_source = room.recognition is self
            if not current_source and not limited:
                return
            if current_source and note:
                self.notice = note
            elif current_source:
                self.receive(match, quick, agreement)
            if trace and (monitor.level == "debug" or (error and monitor.level == "errors")):
                mode = "Quick Fetch" if quick else "完整窗口"
                summary = f"{mode} · {outcome} · {round((finished-started)*1000)} ms"
                details = []
                if error:
                    details.append(error)
                    if not limited:
                        details.append(f"{trace.stage} {round((finished-trace.since)*1000)} ms")
                if match:
                    summary += f" · {match['title']} - {match['artist']}"
                    if not current_source:
                        decision = "原音源已变更"
                    elif limited and not agreement:
                        decision = "目录限流，等待核对"
                    elif self.title and not agreement:
                        decision = "与播放器标题不符，未采用"
                    elif quick and agreement == "partial":
                        decision = "等待完整窗口 · 同一 ID 命中 0/2"
                    elif self.current and self.current["id"] == match["id"]:
                        decision = "已确认"
                        if agreement in ("fingerprint", "partial"):
                            decision += " · 同一 ID 命中 2/2"
                        if agreement == "fuzzy":
                            decision += " · 文本佐证，单次命中采用"
                        elif agreement == "fingerprint":
                            decision += " · 指纹兜底采用"
                    else:
                        decision = "候选，等待二次确认"
                        if agreement in ("fingerprint", "partial"):
                            decision += " · 同一 ID 命中 1/2"
                    details.extend((decision, "Shazam ID " + match["id"]))
                    details.extend(match[k] for k in ("album", "released") if match.get(k))
                    if evidence:
                        details.append(evidence)
                details.append("指纹查询 " + trace.http_summary())
                monitor.event("识别", summary, detail=" · ".join(details), error=bool(error))
