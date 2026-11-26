"""Bounded, in-memory diagnostics for one room. Call under the room lock."""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
import time


@dataclass
class Member:
    name: str
    client: str
    joined: float
    seen: float
    uploaded: int = 0
    downloaded: int = 0
    last_pull: float | None = None


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
        self.event("服务", "房间工作进程已启动")

    def event(self, kind, message):
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
            listening = bool(host and not is_host and member.last_pull is not None and now - member.last_pull <= 3)
            pulling += listening
            members.append(dict(name=member.name, client=member.client, host=is_host, pulling=listening,
                                joined_s=int(now - member.joined), seen_s=int(now - member.seen),
                                pending=key in room.pending, rx=member.uploaded, tx=member.downloaded))
        members.sort(key=lambda m: (not m["host"], m["name"]))
        stream = None
        if host:
            stream = {k: host[k] for k in ("name", "source", "rate", "bits")}
            stream.update(age_s=int(now - room.stage_since), sequence=room.seq,
                          last_audio_s=None if room.last_audio is None else round(now - room.last_audio, 1))
        return dict(time=int(wall), uptime_s=int(now - self.started), capacity=capacity, lease_s=lease,
                    members=members, pulling=pulling, pending=len(room.pending), host=stream,
                    buffer_blocks=len(room.packets), buffer_limit=room.packets.maxlen,
                    rx=self.uploaded, tx=self.downloaded, uploads=self.uploads, downloads=self.downloads,
                    empty_reads=self.empty_reads, cursor_gaps=self.cursor_gaps, failures=self.failures,
                    rx_bps=sum(p["rx_bps"] for p in points[-2:]) / 2,
                    tx_bps=sum(p["tx_bps"] for p in points[-2:]) / 2, history=points, events=list(self.events))
