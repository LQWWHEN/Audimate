"""Bounded, in-memory diagnostics for one room. Call under the room lock."""
from __future__ import annotations
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import time
import math
import secrets

# Detailed fields are accepted only by the development server. Units for stage
# durations are milliseconds; packet identifiers are scoped to one host stream.
STAGES = {
    "capture_read_ms": "采集 · 等待末帧", "pack_ms": "采集 · 组包跨度", "handoff_ms": "采集 · 工作线程交付",
    "queue_ms": "主持 · 待发排队", "connect_ms": "HTTP · 建连（含 TLS）", "send_ms": "HTTP · 写请求",
    "headers_ms": "HTTP · 等响应头", "body_ms": "HTTP · 读响应体", "fetch_ms": "听众 · 拉取总耗时",
    "server_body_ms": "服务 · WSGI 读包", "lock_ms": "服务 · 等房间锁", "store_ms": "服务 · 写历史缓存",
    "ingest_ms": "服务 · 请求进入至入库", "capture_age_ms": "估算 · 采集至入库",
    "residence_ms": "服务 · 包在缓存中驻留", "serve_ms": "服务 · 拉取请求处理",
    "uplink_est_ms": "估算 · 发起上传至入库", "downlink_est_ms": "估算 · 服务响应就绪至收齐",
    "poll_ms": "听众 · 等待新包（含空拉取）", "decode_ms": "听众 · PCM 解码",
    "play_queue_ms": "听众 · 本地排队", "device_ms": "听众 · 写播放设备", "late_ms": "听众 · 错过排播时间",
    "clock_sync_ms": "端侧 · 对时", "grant_ms": "RTC · 申请凭据", "rtc_connect_ms": "RTC · 加入房间", "publish_track_ms": "RTC · 发布音轨",
    "subscribe_ms": "RTC · 入房至收到音轨", "convert_ms": "音频格式转换", "sdk_push_ms": "RTC · SDK 接收帧",
    "rtp_send_queue_ms": "RTC · RTP 发送排队均值", "fetch_headers_ms":"Fetch · 发起至收头（含发送）",
    "elapsed_ms": "异常 · 当前阶段耗时", "upload_ms": "主持 · 上传总耗时",
    "buffer_ms": "接收缓冲", "rtt_ms": "媒体 RTT", "playout_ms": "估算 · 采集至播放",
}
COUNTERS = {"source_seq", "server_seq", "packet_bytes", "queue_depth", "reused", "empty_reads",
            "unconfirmed", "timed_out", "http_status", "concealed_samples", "source_clock_error_ms", "skipped_playback"}
PHASES = {"clock", "connect", "send", "headers", "body", "fetch", "capture", "output", "sdk", "grant",
          "upload", "decode", "manual", "auto", "history", "publish_track", "subscribe"}


def clean_detail(raw):
    if not isinstance(raw, dict):
        return {}
    detail = {}
    for key, value in raw.items():
        if key == "phase" and isinstance(value, str) and value in PHASES:
            detail[key] = value
        elif (key in STAGES or key in COUNTERS) and type(value) in (float, int) and 0 <= value <= 1e12:
            detail[key] = round(value, 2)
    return detail


@dataclass
class Member:
    name: str
    client: str
    joined: float
    seen: float
    uploaded: int = 0
    downloaded: int = 0
    last_pull: float | None = None
    last_sample: float = -math.inf
    last_fault: float = -math.inf
    ended_stream: str = ""
    ended_at: float = -math.inf
    fault_ids: deque = field(default_factory=lambda: deque(maxlen=24))
    peer_id: str = field(default_factory=lambda: secrets.token_hex(6))


def client_type(agent):
    if agent.startswith("Audimate-Desktop/"):
        return "Windows 客户端"
    if "Mozilla/" in agent:
        return "浏览器"
    return "未标识"


class Monitor:
    def __init__(self, development=False):
        self.development = development
        self.round_id = None
        self.service_pending = deque(maxlen=100)
        self.service_last = {}
        self.recent_ingest = OrderedDict()
        self.started = time.monotonic()
        self.uploaded = self.downloaded = 0
        self.uploads = self.downloads = self.empty_reads = self.cursor_gaps = 0
        self.failures = 0
        self.seconds = [None] * 601
        self.events = deque(maxlen=100)
        self.samples = deque(maxlen=600)
        self.level = "debug"
        self.event("服务", "房间工作进程已启动")

    def set_level(self, level):
        self.level = level
        if level != "debug":
            self.samples.clear()
            self.service_pending.clear()
            self.recent_ingest.clear()
        if level == "off":
            self.events.clear()

    def record(self, member, data, host, *, is_host=False):
        now = time.monotonic()
        event = data.get("event")
        stream = data.get("stream")
        current = host and stream == host["stream"]
        if not current and not (self.development and event == "error" and stream == member.ended_stream and
                                now - member.ended_at < 30):
            return
        if event not in ("publish", "play", "sync", "gap", "overload", "disconnect", "error"):
            return
        if data.get("transport") not in ("pcm", "rtc"):
            return
        fault = self.development and event in ("error", "overload", "disconnect", "gap", "sync")
        report_id = data.get("report_id")
        identified = fault and isinstance(report_id, str) and len(report_id) == 16 and all(c in "0123456789abcdef" for c in report_id)
        if identified and (stream, report_id) in member.fault_ids:
            return
        if now - (member.last_fault if fault else member.last_sample) < (0.2 if fault else 0.8):
            return
        if fault:
            member.last_fault = now
        else:
            member.last_sample = now
        if event == "play":
            member.last_pull = now
        if self.level == "off" or (self.level == "errors" and event not in ("error", "overload", "gap", "disconnect")):
            return
        if identified:
            member.fault_ids.append((stream, report_id))
        entry = dict(time=int(time.time()), name=member.name, client=member.client,
                     session=stream[:8], event=event, transport=data["transport"])
        if self.development and current:
            entry.update(peer=member.peer_id, round_id=self.round_id, role="host" if is_host else "listener")
        if self.development and (detail := clean_detail(data.get("detail"))):
            if fault and detail.get("unconfirmed") and "source_seq" in detail:
                receipt = self.recent_ingest.get((stream, detail["source_seq"]))
                if receipt and now - receipt[0] <= 30:
                    detail.update(receipt[1])
            entry["detail"] = detail
        for key in ("playout_ms", "rtcp_lag_ms", "timeline_ms", "clock_error_ms", "rtt_ms", "jitter_ms", "buffer_ms",
                    "lost", "discarded", "underruns", "bytes", "upload_ms"):
            value = data.get(key)
            if type(value) in (int, float) and 0 <= value <= 1e12 and math.isfinite(value):
                entry[key] = round(value, 2)
        self.samples.appendleft(entry)
        if event in ("gap", "overload", "disconnect", "error", "sync"):
            self.event("传输", member.name + " · " + event)
        return entry

    def service_sample(self, stream, event, transport, detail):
        if not self.development or self.level == "off":
            return
        now = time.monotonic()
        if event == "ingest" and "source_seq" in detail:
            # An ACK timeout can concern a packet outside the regular sample.
            key = (stream, detail["source_seq"])
            self.recent_ingest[key] = (now, clean_detail(detail))
            self.recent_ingest.move_to_end(key)
            if len(self.recent_ingest) > 128:
                self.recent_ingest.popitem(last=False)
            if detail["source_seq"] % 10 != 1:
                return
        if self.level != "debug":
            return
        if now - self.service_last.get(event, -math.inf) < 1:
            return
        self.service_last[event] = now
        row = dict(time=int(time.time()), session=stream[:8], name="服务端", client="服务端",
                   event=event, transport=transport, detail=clean_detail(detail))
        self.samples.appendleft(row)
        self.service_pending.append(row)

    def stage_summary(self, stream):
        groups = {}
        for sample in self.samples:
            if sample["session"] != stream:
                continue
            for stage, value in (sample | sample.get("detail", {})).items():
                if stage in STAGES:
                    direction = {"publish":"上传","overload":"上传","play":"收听","gap":"缺口","ingest":"入库",
                                 "opus_to_pcm":"Opus → PCM","pcm_to_opus":"PCM → Opus"}.get(sample["event"],sample["event"])
                    groups.setdefault((sample["client"], sample["transport"], direction, stage), []).append(value)
        rows = []
        for (client, transport, direction, stage), values in groups.items():
            values.sort()
            rows.append(dict(client=client, transport=transport, direction=direction, stage=stage, n=len(values),
                             p50=values[(len(values)-1)//2], p95=values[math.ceil(len(values)*.95)-1], peak=values[-1]))
        return dict(stream=stream, labels=STAGES, rows=rows)

    def event(self, kind, message):
        if self.level == "off" or self.level == "errors" and kind not in ("异常", "传输"):
            return
        timestamp = int(time.time())
        if self.events and kind == "异常":
            last = self.events[0]
            if (last["kind"], last["message"]) == (kind, message) and timestamp - last["time"] <= 5:
                last["time"] = timestamp
                last["count"] += 1
                return
        self.events.appendleft(dict(time=timestamp, kind=kind, message=message, count=1))

    def traffic(self, member, *, received=0, sent=0):
        second = int(time.monotonic())
        index = second % len(self.seconds)
        row = self.seconds[index]
        if row is None or row[0] != second:
            row = self.seconds[index] = [second, 0, 0]
        row[1] += received
        row[2] += sent
        self.uploaded += received
        self.downloaded += sent
        self.uploads += bool(received)
        self.downloads += bool(sent)
        member.uploaded += received
        member.downloaded += sent

    def snapshot(self, room, now, capacity):
        # Each point spans five completed seconds. Assign wall-clock labels
        # at read time so NTP adjustments cannot shift the underlying counters.
        end = int(now)
        opus_enabled = bool(room.media.traffic.url or room.media.enabled)
        rtp_seconds, totals, meter = (), (0, 0), None
        if opus_enabled:
            end, rtp_seconds, totals, meter = room.media.traffic.snapshot(now)
        wall = time.time()
        while self.samples and self.samples[-1]["time"] < wall - 600:
            self.samples.pop()
        points = []
        for start in range(end - 600, end, 5):
            if start + 5 <= self.started:
                continue
            incoming = outgoing = 0
            opus_rx = opus_tx = 0
            measured = True
            for sec in range(start, start + 5):
                row = self.seconds[sec % len(self.seconds)]
                if row is not None and row[0] == sec:
                    incoming += row[1]
                    outgoing += row[2]
                if opus_enabled:
                    sample = rtp_seconds[sec % len(rtp_seconds)]
                    if sample is None or sample[0] != sec:
                        measured = False
                    else:
                        opus_rx += sample[1]
                        opus_tx += sample[2]
            points.append(dict(time=int(wall - (now - start - 5)),
                               rx_bps=incoming * 8 / 5, tx_bps=outgoing * 8 / 5,
                               opus_rx_bps=opus_rx * 8 / 5 if measured else None,
                               opus_tx_bps=opus_tx * 8 / 5 if measured else None))

        host = room.host
        members = []
        pulling = 0
        for key, member in room.members.items():
            is_host = bool(host and host["key"] == key)
            listening = bool(host and not is_host and member.last_pull is not None and now - member.last_pull <= 7)
            pulling += listening
            members.append(dict(name=member.name, client=member.client, host=is_host, pulling=listening,
                                joined_s=int(now - member.joined), seen_s=int(now - member.seen),
                                pending=key in room.pending, rx=member.uploaded, tx=member.downloaded))
            if self.development:
                members[-1]["peer"] = member.peer_id
        members.sort(key=lambda m: (not m["host"], m["name"]))
        stream = None
        if host:
            stream = {k: host[k] for k in ("name", "source", "rate", "bits")}
            stream.update(codec=host["codec"], age_s=int(now - room.stage_since), sequence=room.history.sequence,
                          last_audio_s=None if room.last_audio is None else round(now - room.last_audio, 1))
        audio = {"opus_enabled": opus_enabled}
        for i, (direction, pcm) in enumerate((("rx", self.uploaded), ("tx", self.downloaded))):
            recent = points[-2:]
            field = direction + "_bps"
            ready = not opus_enabled or (len(recent) == 2 and totals is not None and
                                         all(p["opus_"+field] is not None for p in recent))
            audio[field] = sum(p[field]+p["opus_"+field] for p in recent)/2 if ready else None
            audio[direction] = pcm + totals[i] if totals is not None else None
        return dict(time=int(wall), uptime_s=int(now - self.started), capacity=capacity, lease_s=room.lease_s,
                    audio_traffic=audio, sfu=meter,
                    members=members, pulling=pulling, pending=len(room.pending), host=stream,
                    buffer_blocks=len(room.history.blocks), history_s=room.history.seconds, samples=list(self.samples),
                    rx=self.uploaded, tx=self.downloaded, uploads=self.uploads, downloads=self.downloads,
                    empty_reads=self.empty_reads, cursor_gaps=self.cursor_gaps, failures=self.failures,
                    rx_bps=sum(p["rx_bps"] for p in points[-2:]) / 2,
                    tx_bps=sum(p["tx_bps"] for p in points[-2:]) / 2, history=points, events=list(self.events))
