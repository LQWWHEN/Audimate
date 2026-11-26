import math
import random
import unittest
from . import flac


class FlacPackets(unittest.TestCase):
    def test_all_widths_and_packet_boundaries(self):
        rng = random.Random(41)
        for rate in (8000, 44100, 88200, 192000):
            for bits in (16, 24, 32):
                width = bits // 8
                peak = 2**(bits-1)-1
                pcm = b"".join(v.to_bytes(width, "little", signed=True)
                               for i in range(rate//5) for v in (round(peak*math.sin(i*.13)), -peak-1))
                for block in (pcm, rng.randbytes(rate*width), pcm[:width*2]):
                    with self.subTest(rate=rate, bits=bits, size=len(block)):
                        wire = flac.encode(block, rate, bits)
                        self.assertEqual(wire[:4], b"fLaC")
                        self.assertEqual(flac.decode(wire, rate, bits), block)
                        if block is pcm:
                            self.assertLess(len(wire), len(pcm))
