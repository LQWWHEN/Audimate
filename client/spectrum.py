"""Stereo power spectrum and transient envelopes, fed only by the active audio path."""
from collections import deque
import threading
import time

import numpy as np


BANDS = 48
STYLES = ("pop", "heavy", "light")


class Spectrum(threading.Thread):
    def __init__(self, rate, bits):
        super().__init__(name="Audimate spectrum", daemon=True)
        self.rate, self.bits = rate, bits
        self.chunks = deque(maxlen=12)
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.last_input = 0.0
        self.snapshot = (0.0, np.zeros(BANDS), 0.0, 0.0)
        size = 1 << round(np.log2(rate * .05))
        self.window = np.hanning(size).astype("f")
        self.samples = np.zeros((size, 2), dtype="f")
        freqs = np.fft.rfftfreq(size, 1 / rate)
        edges = np.geomspace(32, min(17000, rate / 2), BANDS + 1)
        self.ranges = [(max(1, a), max(2, a + 1, b)) for a, b in
                       zip(np.searchsorted(freqs, edges[:-1]), np.searchsorted(freqs, edges[1:]))]
        self.kick = (freqs >= 40) & (freqs <= 145)
        self.body = (freqs >= 145) & (freqs <= 420)
        self.snap = (freqs >= 1500) & (freqs <= 5500)
        self.floor = np.zeros(3)
        self.previous = np.zeros(3)

    def feed(self, pcm):
        if not self.closed.is_set():
            self.chunks.append(bytes(pcm))
            self.last_input = time.monotonic()
            self.ready.set()

    def close(self):
        self.closed.set()
        self.ready.set()

    def run(self):
        while not self.closed.is_set():
            self.ready.wait(.1)
            self.ready.clear()
            while self.chunks and not self.closed.is_set():
                self.analyze(self.chunks.popleft())

    def analyze(self, chunk):
        if self.bits == 24:
            raw = np.frombuffer(chunk, dtype="u1").reshape(-1, 3).astype("i4")
            samples = ((raw[:, 0] << 8) | (raw[:, 1] << 16) | (raw[:, 2] << 24)) / 2147483648
        else:
            samples = np.frombuffer(chunk, dtype="<i" + str(self.bits // 8)) / (2 ** (self.bits - 1))
        samples = samples.reshape(-1, 2)
        n = min(len(samples), len(self.window))
        if not n:
            return
        self.samples[:-n] = self.samples[n:]
        self.samples[-n:] = samples[-n:]
        fft = np.fft.rfft(self.samples * self.window[:, None], axis=0)
        power = (abs(fft) ** 2).mean(axis=1) / (self.window.sum() / 2) ** 2
        bands = np.array([power[a:b].max() for a, b in self.ranges])
        bands = np.clip((10 * np.log10(bands + 1e-12) + 65) / 58, 0, 1)
        energy = np.sqrt([power[self.kick].sum(), power[self.body].sum(), power[self.snap].sum()])
        rise = np.clip((energy - self.previous) / (.014 + self.floor * .5), 0, 1)
        self.floor += (energy - self.floor) * (1 - np.exp(-n / (self.rate * .22)))
        self.previous = energy
        self.snapshot = (time.monotonic(), bands, float(rise[0]), float(np.sqrt(rise[1] * rise[2])))
