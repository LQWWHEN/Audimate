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
        self.fft_scale = (self.window.sum() / 2) ** -2
        self.samples = np.zeros((size, 2), dtype="f")
        self.hop = round(rate * .010)
        self.hop_bytes = self.hop * 2 * (bits // 8)
        self.remainder = b""
        freqs = np.fft.rfftfreq(size, 1 / rate)
        edges = np.geomspace(32, min(17000, rate / 2), BANDS + 1)
        self.ranges = [(max(1, a), max(2, a + 1, b)) for a, b in
                       zip(np.searchsorted(freqs, edges[:-1]), np.searchsorted(freqs, edges[1:]))]
        low, body, end = freqs.searchsorted([40, 145, 420])
        self.drum_bins = slice(low, end)
        self.groups = [0, body-low]
        self.previous = np.zeros(end-low)
        self.background = np.zeros(end-low)
        self.flux_floor = np.zeros(2)
        self.pulses = np.zeros(2)
        dt = self.hop / rate
        self.adapt = 1 - np.exp(-dt / .20)
        self.pulse_decay = np.exp(-dt / np.array([.042, .029]))
        centers = np.sqrt(edges[:-1] * edges[1:])
        self.narrow = np.diff(edges) < rate / size
        bin_position = centers[self.narrow] * size / rate
        self.left_bin = bin_position.astype(int)
        self.bin_fraction = bin_position - self.left_bin
        self.body_weight = np.interp(centers, [32, 45, 320, 600], [.75, 1, 1, 0])
        self.band_background = np.zeros(BANDS)

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
        # Host, device and network callbacks have different block sizes. Advance
        # the detector on the sample clock and carry partial hops across calls.
        if self.remainder:
            chunk = self.remainder + chunk
        end = len(chunk) // self.hop_bytes * self.hop_bytes
        self.remainder = bytes(chunk[end:])
        if not end:
            return
        chunk = memoryview(chunk)[:end]
        if self.bits == 24:
            raw = np.frombuffer(chunk, dtype="u1").reshape(-1, 3).astype("i4")
            samples = ((raw[:, 0] << 8) | (raw[:, 1] << 16) | (raw[:, 2] << 24)) / 2147483648
        else:
            samples = np.frombuffer(chunk, dtype="<i" + str(self.bits // 8)) / (2 ** (self.bits - 1))
        for frame in samples.reshape(-1, self.hop, 2):
            self.analyze_frame(frame)

    def analyze_frame(self, frame):
        self.samples[:-self.hop] = self.samples[self.hop:]
        self.samples[-self.hop:] = frame
        fft = np.fft.rfft(self.samples * self.window[:, None], axis=0)
        power = (abs(fft) ** 2).mean(axis=1) * self.fft_scale
        # Weight positive changes per bin against the local musical background.
        # A sustained bass note has little flux; a new drum can still cut through it.
        magnitude = np.sqrt(power[self.drum_bins])
        growth = np.maximum(magnitude - self.previous, 0) / (.016 + .44*self.background)
        flux = np.sqrt(np.add.reduceat(growth**2, self.groups))
        strength = np.maximum(1.5 * (flux - 2.3*self.flux_floor - .20), 0)
        # A soft knee keeps successive loud hits distinguishable at the rim.
        above_knee = np.maximum(strength - .5, 0)
        attack = np.minimum(strength, .5) + .5 * above_knee / (.5 + above_knee)
        self.pulses = np.maximum(attack, self.pulses * self.pulse_decay)
        self.background += self.adapt * (magnitude - self.background)
        self.flux_floor += self.adapt * (flux - self.flux_floor)
        self.previous = magnitude

        bands = np.array([power[a:b].max() for a, b in self.ranges])
        # Several bass bands can fall between the same pair of FFT bins.
        # Interpolate those centers to avoid duplicating a peak across the arc.
        bands[self.narrow] = (power[self.left_bin] * (1 - self.bin_fraction)
                              + power[self.left_bin + 1] * self.bin_fraction)
        bands = np.clip((10 * np.log10(bands + 1e-12) + 65) / 58, 0, 1)
        contrast = np.clip(5 * (bands - self.band_background - .025), 0, 1)
        self.band_background += self.adapt * (bands - self.band_background)
        bands = np.clip(bands * (1 - .20*self.body_weight) + .26*self.body_weight*contrast, 0, 1)
        # Preserve the loudest band while lowering the shoulders of each peak.
        # The reference floor keeps quiet passages quiet; silence stays at zero.
        peak = max(.55, bands.max())
        bands = peak * (bands / peak) ** 1.65
        neighbors = np.convolve(np.pad(bands, 3, mode="edge"),
                                [.08, .13, .18, .22, .18, .13, .08], mode="valid")
        bands = np.clip(bands + .60 * (bands - neighbors), 0, peak)
        self.snapshot = (time.monotonic(), bands, float(self.pulses[0]), float(self.pulses[1]))
