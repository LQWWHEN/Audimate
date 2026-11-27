import unittest

import numpy as np

from .spectrum import BANDS, Spectrum, band_edges


RATE = 48000


def pcm(mono):
    return (np.column_stack((mono, -mono)).clip(-1, 1) * 32767).astype("<i2").tobytes()


class SpectrumTests(unittest.TestCase):
    def test_bass_mapping_and_sub_bass_signal(self):
        for rate in (8000, 44100, 48000, 96000, 192000):
            with self.subTest(rate=rate):
                edges = band_edges(rate)
                self.assertEqual(len(edges), BANDS+1)
                self.assertTrue(np.all(np.diff(edges) > 0))
                self.assertAlmostEqual(edges[0], 20)
                self.assertAlmostEqual(edges[-1], min(17000, rate/2))
                centers = np.sqrt(edges[1:] * edges[:-1])
                degrees = np.linspace(0, 180, BANDS)
                start, end, octave = np.interp(np.log([80, 420, 840]), np.log(centers), degrees)
                self.assertAlmostEqual((end-start) / np.log2(420/80), 1.25 * (octave-end), delta=1)
                if rate == RATE:
                    self.assertAlmostEqual(start, 34, delta=1)
                    self.assertAlmostEqual(end, 87, delta=1)

                analyzer = Spectrum(rate, 16)
                t = np.arange(rate//2)/rate
                analyzer.analyze(pcm(.35*np.sin(2*np.pi*20*t)))
                levels = analyzer.snapshot[1]
                self.assertGreater(levels[:3].max(), .6)
                self.assertGreater(levels[:3].max(), levels[centers > 80].max() + .4)
                analyzer.analyze(pcm(np.full(len(t), .12)))
                np.testing.assert_array_equal(analyzer.snapshot[1], 0)

    def test_quieter_band_between_bass_peaks_stays_visible_as_a_valley(self):
        t = np.arange(round(.8 * RATE)) / RATE
        signal = sum(level * np.sin(2*np.pi*hz*t) for hz, level in
                     ((187.5, .32), (234.375, .004), (281.25, .055)))
        analyzer = Spectrum(RATE, 16)
        analyzer.analyze(pcm(signal))
        levels = analyzer.snapshot[1]
        edges = band_edges(RATE)
        centers = np.sqrt(edges[:-1] * edges[1:])
        peak, valley, shoulder = [levels[np.argmin(abs(centers - hz))] for hz in (187, 243, 277)]
        self.assertGreater(peak - valley, .45)
        self.assertGreater(shoulder - valley, .18)
        self.assertLess(max(analyzer.snapshot[2:]), .01)
        analyzer.analyze(pcm(np.zeros(RATE//4)))
        np.testing.assert_array_equal(analyzer.snapshot[1], 0)

    def test_low_drum_attacks_on_a_limited_mix_and_high_only_control(self):
        t = np.arange(RATE*2) / RATE
        bed = sum(gain*np.sin(2*np.pi*hz*t) for hz, gain in
                  ((83, .62), (245, .30), (330, .14), (1300, .11)))
        age = np.maximum(t-.8, 0)
        envelope = np.where(t >= .8, np.exp(-age/.045), 0)
        kick = .19*np.sin(2*np.pi*(58*age + .85*(1-np.exp(-age*35)))) * envelope
        body = .14*np.sin(2*np.pi*191*age) * envelope
        hat = .23*np.sin(2*np.pi*6700*age) * envelope
        backing = .77*np.tanh(2*bed)
        peaks = {}
        for label, signal, channel, threshold in (
                ("kick", .77*np.tanh(2*(bed+kick)), 0, .32),
                ("snare body", .77*np.tanh(2*(bed+body)), 1, .75),
                ("accented snare", .77*np.tanh(2*(bed+1.65*body)), 1, .90),
                ("high only", backing+hat, None, .10)):
            with self.subTest(label=label):
                analyzer = Spectrum(RATE, 16)
                data = pcm(signal)
                pulses = []
                for start in range(0, len(data), analyzer.hop_bytes):
                    analyzer.analyze(data[start:start+analyzer.hop_bytes])
                    pulses.append(analyzer.snapshot[2:])
                pulses = np.array(pulses)
                if channel is None:
                    self.assertLess(pulses[80:87].max(), threshold)
                else:
                    peaks[label] = pulses[80:87, channel].max()
                    self.assertGreater(peaks[label], threshold)
                self.assertLess(pulses[98:111].max(), .05)
        self.assertGreater(peaks["accented snare"] - peaks["snare body"], .04)

    def test_transport_block_size_preserves_spectrum_and_transients(self):
        data = pcm(np.random.default_rng(4).normal(0, .13, RATE//5))
        reference = Spectrum(RATE, 16)
        reference.analyze(data)
        for frames in (127, 480, 960):
            analyzer = Spectrum(RATE, 16)
            for start in range(0, len(data), frames*4):
                analyzer.analyze(data[start:start+frames*4])
            np.testing.assert_allclose(analyzer.snapshot[1], reference.snapshot[1])
            np.testing.assert_allclose(analyzer.snapshot[2:], reference.snapshot[2:])
