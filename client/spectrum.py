"""Stereo power spectrum and transient envelopes, fed only by the active audio path."""
from collections import deque
import threading
import time

import numpy as np


from server.spectrum_config import EQ_HZ, preset

BANDS = 96


def band_edges(rate, start=20, count=BANDS):
    """Give 80–420 Hz a quarter more arc per octave on the mirrored spectrum."""
    log_hz = np.log([start, 80, 420, min(17000, rate / 2)])
    arc = np.r_[0, np.cumsum(np.diff(log_hz) * [1, 1.25, 1])]
    return np.exp(np.interp(np.linspace(0, arc[-1], count + 1), arc, log_hz))


class Spectrum(threading.Thread):
    def __init__(self, rate, bits, config=None):
        super().__init__(name="Audimate spectrum", daemon=True)
        self.rate, self.bits = rate, bits
        self.chunks = deque(maxlen=12)
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.last_input = 0.0
        self.requested_config = config or preset()
        self.snapshot = (0.0, np.zeros(self.requested_config["count"]), 0.0, 0.0)
        size = 1 << round(np.log2(rate * .05))
        self.window = np.hanning(size).astype("f")
        self.fft_scale = (self.window.sum() / 2) ** -2
        self.samples = np.zeros((size, 2), dtype="f")
        self.hop = round(rate * .010)
        self.hop_bytes = self.hop * 2 * (bits // 8)
        self.remainder = b""
        freqs = np.fft.rfftfreq(size, 1 / rate)
        self.freqs = freqs
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
        self.config = None
        self.configure(self.requested_config)

    def configure(self, config):
        size = len(self.window)
        edges = band_edges(self.rate, config["start"], config["count"])
        self.ranges = [(max(1, a), max(2, a + 1, b)) for a, b in
                       zip(np.searchsorted(self.freqs, edges[:-1]), np.searchsorted(self.freqs, edges[1:]))]
        centers = np.sqrt(edges[:-1] * edges[1:])
        self.narrow = np.diff(edges) < self.rate / size
        bin_position = centers[self.narrow] * size / self.rate
        self.left_bin = bin_position.astype(int)
        self.bin_fraction = bin_position - self.left_bin
        self.body_weight = np.interp(centers, [20, 45, 320, 600], [.75, 1, 1, 0])
        self.eq_db = np.interp(np.log(centers), np.log(EQ_HZ), config["eq"])
        if self.config is None or (config["count"], config["start"]) != (self.config["count"], self.config["start"]):
            self.band_background = np.zeros(config["count"])
        self.config = config

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
        # Only the analysis thread rebuilds its band arrays, between whole frames.
        config = self.requested_config
        if config is not self.config:
            self.configure(config)
        self.samples[:-self.hop] = self.samples[self.hop:]
        self.samples[-self.hop:] = frame
        # The 20 Hz bands can interpolate the first FFT bin. Remove channel
        # offsets before windowing so a DC bias cannot become a sub-bass peak.
        centered = self.samples - self.samples.mean(axis=0)
        fft = np.fft.rfft(centered * self.window[:, None], axis=0)
        power = (abs(fft) ** 2).mean(axis=1) * self.fft_scale
        # Weight positive changes per bin against the local musical background.
        # A sustained bass note has little flux; a new drum can still cut through it.
        magnitude = np.sqrt(power[self.drum_bins])
        growth = np.maximum(magnitude - self.previous, 0) / (.016 + .44*self.background)
        flux = np.sqrt(np.add.reduceat(growth**2, self.groups))
        strength = np.maximum(config["transient"] * 1.5 * (flux - 2.3*self.flux_floor - .20), 0)
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
        levels_db = 10 * np.log10(bands + 1e-12)
        # A 6 dB soft gate on each display band, before the four-point EQ.
        gate = np.clip((levels_db - config["gate"] + 3) / 6, 0, 1)
        bands = gate * np.clip((levels_db + self.eq_db + 65) / 58, 0, 1)
        contrast = np.clip(5 * (bands - self.band_background - .025), 0, 1)
        self.band_background += self.adapt * (bands - self.band_background)
        bands = np.clip(bands * (1 - .20*self.body_weight) + .26*self.body_weight*contrast, 0, 1)
        # Preserve the loudest band while lowering the shoulders of each peak.
        # The reference floor keeps quiet passages quiet; silence stays at zero.
        peak = max(.55, bands.max())
        bands = peak * (bands / peak) ** config["contrast"]
        neighbors = np.convolve(np.pad(bands, 3, mode="edge"),
                                [.08, .13, .18, .22, .18, .13, .08], mode="valid")
        bands = np.clip(bands + .60 * (bands - neighbors), 0, peak)
        self.snapshot = (time.monotonic(), bands, float(self.pulses[0]), float(self.pulses[1]))
