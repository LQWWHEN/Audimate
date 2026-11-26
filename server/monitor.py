"""Bounded, in-memory diagnostics for one room. Call under the room lock."""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
import time
import math


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


def client_type(agent):
    if agent.startswith("Audimate-Desktop/"):
        return "Windows 客户端"
    if "Mozilla/" in agent:
        return "浏览器"
    return "未标识"


class Monitor:
    def __init__(self):
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
        if level == "off":
            self.events.clear()

    def record(self, member, data, host):
        now = time.monotonic()
        if now - member.last_sample < 0.8 or not host or data.get("stream") != host["stream"]:
            return
        event = data.get("event")
        if event not in ("publish", "play", "sync", "gap", "overload", "disconnect", "error"):
            return
        if data.get("transport") not in ("pcm", "rtc"):
            return
        member.last_sample = now
        if event == "play":
            member.last_pull = now
        if self.level == "off" or (self.level == "errors" and event not in ("error", "overload", "gap", "disconnect")):
            return
        entry = dict(time=int(time.time()), name=member.name, client=member.client,
                     session=host["stream"][:8], event=event, transport=data["transport"])
        for key in ("playout_ms", "rtcp_lag_ms", "timeline_ms", "clock_error_ms", "rtt_ms", "jitter_ms", "buffer_ms",
                    "lost", "discarded", "underruns", "bytes", "upload_ms"):
            value = data.get(key)
            if type(value) in (int, float) and 0 <= value <= 1e12 and math.isfinite(value):
                entry[key] = round(value, 2)
        self.samples.appendleft(entry)
        if event in ("gap", "overload", "disconnect", "error", "sync"):
            self.event("传输", member.name + " · " + event)
        return entry

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

    def snapshot(self, room, now, capacity, lease):
        # Each point spans five completed seconds. Assign wall-clock labels
        # at read time so NTP adjustments cannot shift the underlying counters.
        end = int(now)
        wall = time.time()
        while self.samples and self.samples[-1]["time"] < wall - 600:
            self.samples.pop()
        points = []
        for start in range(end - 600, end, 5):
            if start + 5 <= self.started:
                continue
            incoming = outgoing = 0
            for sec in range(start, start + 5):
                row = self.seconds[sec % len(self.seconds)]
                if row is not None and row[0] == sec:
                    incoming += row[1]
                    outgoing += row[2]
            points.append(dict(time=int(wall - (now - start - 5)),
                               rx_bps=incoming * 8 / 5, tx_bps=outgoing * 8 / 5))

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
        members.sort(key=lambda m: (not m["host"], m["name"]))
        stream = None
        if host:
            stream = {k: host[k] for k in ("name", "source", "rate", "bits")}
            stream.update(codec=host["codec"], age_s=int(now - room.stage_since), sequence=room.history.sequence,
                          last_audio_s=None if room.last_audio is None else round(now - room.last_audio, 1))
        return dict(time=int(wall), uptime_s=int(now - self.started), capacity=capacity, lease_s=lease,
                    members=members, pulling=pulling, pending=len(room.pending), host=stream,
                    buffer_blocks=len(room.history.blocks), history_s=room.history.seconds, samples=list(self.samples),
                    rx=self.uploaded, tx=self.downloaded, uploads=self.uploads, downloads=self.downloads,
                    empty_reads=self.empty_reads, cursor_gaps=self.cursor_gaps, failures=self.failures,
                    rx_bps=sum(p["rx_bps"] for p in points[-2:]) / 2,
                    tx_bps=sum(p["tx_bps"] for p in points[-2:]) / 2, history=points, events=list(self.events))
