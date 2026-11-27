"""Per-listener automatic alignment, timed by fresh playback samples."""
import math


class SyncPolicy:
    def __init__(self, now):
        self.last_attempt = now
        self.attempts = 0
        self.last_sample = self.high_since = self.stable_since = self.threshold = None

    def observe(self, now, lag, threshold, interval):
        if self.last_sample is not None and now <= self.last_sample:
            return False
        if self.last_sample is None or now - self.last_sample > 3 or threshold != self.threshold:
            self.high_since = self.stable_since = None
        self.last_sample, self.threshold = now, threshold
        if lag is None:
            self.high_since = self.stable_since = None
            return False
        if lag <= threshold:
            self.high_since = None
            if lag <= 1000:
                if self.stable_since is None:
                    self.stable_since = now
                if now - self.stable_since >= 15:
                    self.attempts = 0
            else:
                self.stable_since = None
            return False
        self.stable_since = None
        if self.high_since is None:
            self.high_since = now
        wait = min(max(interval, 60), interval * 2 ** self.attempts)
        if now - self.high_since < 3 or now - self.last_attempt < wait:
            return False
        self.last_attempt = now
        self.high_since = None
        self.attempts = min(6, self.attempts + 1)
        return True


def threshold_ms(text, cap):
    text = text.strip()
    if text in ("", "跟随房间"):
        return None
    try:
        seconds = float(text)
        if math.isfinite(seconds) and 0 <= seconds <= cap / 1000:
            return round(seconds * 1000)
    except ValueError:
        pass
    raise ValueError(f"阈值范围 0–{cap / 1000:g} 秒")
