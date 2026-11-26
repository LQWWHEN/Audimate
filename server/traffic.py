"""Align SFU counter deltas to the room's monotonic, one-second PCM buckets."""
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
import re
import threading
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


def read_counters(lines):
    totals = {"incoming": 0, "outgoing": 0}
    started = None
    livekit = False
    for line in lines:
        if line.startswith("process_start_time_seconds "):
            started = line.split()[1]
        if line.startswith("livekit_"):
            livekit = True
        if not line.startswith("livekit_packet_bytes{"):
            continue
        labels, value = line.split("}", 1)
        direction = re.search(r'(?:\{|,)direction="(incoming|outgoing)"', labels)
        if direction:
            count = Decimal(value.strip())
            if not count.is_finite() or count < 0:
                raise ValueError("Invalid RTP byte counter")
            totals[direction[1]] += int(count)
    if not livekit:
        raise ValueError("Missing LiveKit metrics")
    return totals["incoming"], totals["outgoing"], started


class RtpTraffic:
    def __init__(self, url):
        self.url = url
        self.lock = threading.Lock()
        self.seconds = [None] * 610
        self.last = None
        self.fresh = False
        self.rx = self.tx = 0

    def collect(self):
        local = urlsplit(self.url).hostname in ("127.0.0.1", "::1", "localhost")
        opener = build_opener(ProxyHandler({})) if local else build_opener()
        while True:
            try:
                with opener.open(self.url, timeout=1.5) as response:
                    data = response.read(2_000_001)
                if len(data) > 2_000_000:
                    raise ValueError("Incomplete metrics")
                counts = read_counters(data.decode().splitlines())
            except (OSError, HTTPException, ValueError, InvalidOperation):
                counts = None
            with self.lock:
                self.observe(int(time.monotonic()), counts)
            time.sleep(2)

    def observe(self, second, counts):
        if counts is None:
            self.fresh = False
            return
        rx, tx, started = counts
        if self.last:
            previous, old_rx, old_tx, old_start = self.last
            restarted = started is not None and old_start is not None and started != old_start
            received = rx if restarted or rx < old_rx else rx-old_rx
            sent = tx if restarted or tx < old_tx else tx-old_tx
            self.rx += received
            self.tx += sent
            span = second - previous
            if self.fresh and not restarted and rx >= old_rx and tx >= old_tx and 0 < span <= 6:
                for sec in range(previous, second):
                    self.seconds[sec % len(self.seconds)] = (sec, received/span, sent/span)
        self.last = (second, rx, tx, started)
        self.fresh = True

    def snapshot(self, now):
        with self.lock:
            current = self.last and self.fresh and 0 <= now-self.last[0] <= 6
            end = self.last[0] if current else int(now)
            meter = None
            if current:
                row = self.seconds[(end-1) % len(self.seconds)]
                known = row and row[0] == end-1
                meter = dict(time=int(time.time()-(now-end)), incoming=self.last[1], outgoing=self.last[2],
                             rx_bps=row[1]*8 if known else None, tx_bps=row[2]*8 if known else None)
            return end, self.seconds.copy(), (self.rx, self.tx) if current else None, meter
