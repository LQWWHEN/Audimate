import unittest

from .app import Room
from .traffic import RtpTraffic, read_counters


class AudioTraffic(unittest.TestCase):
    def test_pcm_and_rtp_share_a_rate_window(self):
        room = Room()
        monitor = room.monitor
        monitor.started = 99
        meter = room.media.traffic = RtpTraffic("test")
        meter.observe(100, (10000, 20000, "1"))
        meter.observe(105, (10200, 20200, "1"))
        meter.observe(110, (10300, 20600, "1"))
        monitor.seconds[101] = [101, 100, 200]
        monitor.uploaded, monitor.downloaded = 100, 200
        s = monitor.snapshot(room, 110.4, 19)
        self.assertEqual((s["audio_traffic"]["rx"], s["audio_traffic"]["tx"]), (400, 800))
        self.assertEqual((s["audio_traffic"]["rx_bps"], s["audio_traffic"]["tx_bps"]), (320, 640))
        first, second = s["history"][-2:]
        self.assertEqual((first["rx_bps"], first["opus_rx_bps"], first["opus_tx_bps"]), (160, 320, 320))
        self.assertEqual((second["rx_bps"], second["opus_rx_bps"], second["opus_tx_bps"]), (0, 160, 640))

    def test_baseline_outage_recovery_and_counter_reset(self):
        meter = RtpTraffic("test")
        self.assertIsNone(meter.snapshot(100)[2])
        meter.observe(100, (100, 200, "1"))
        self.assertEqual(meter.snapshot(100)[2], (0, 0))
        meter.observe(102, (120, 240, "1"))
        meter.observe(104, None)
        self.assertIsNone(meter.snapshot(104)[2])
        meter.observe(106, (150, 300, "1"))
        end, buckets, total, raw = meter.snapshot(106)
        self.assertEqual(total, (50, 100))
        self.assertEqual((raw["incoming"],raw["outgoing"]), (150,300))
        self.assertIsNone(buckets[105])
        meter.observe(108, (3, 5, "2"))
        self.assertEqual(meter.snapshot(108)[2], (53, 105))
        self.assertIsNone(meter.snapshot(108)[1][107])
        meter.observe(110, (7, 9, "2"))
        self.assertEqual(meter.snapshot(110)[1][109], (109, 2, 2))
        self.assertIsNone(meter.snapshot(130)[2])
        meter.observe(112, (1, 13, "2"))
        self.assertEqual(meter.snapshot(112)[2], (58, 113))

    def test_prometheus_directions_and_idle_exporter(self):
        lines = ['process_start_time_seconds 123',
                 'livekit_packet_bytes{country="",direction="incoming",transmission="initial"} 4e2',
                 'livekit_packet_bytes{direction="incoming",transmission="retransmit"} 15',
                 'livekit_packet_bytes{direction="outgoing"} 3',
                 'livekit_fec_packet_bytes{direction="incoming"} 3']
        self.assertEqual(read_counters(lines), (415, 3, "123"))
        self.assertEqual(read_counters(["livekit_room_total 0"]), (0, 0, None))
        with self.assertRaises(ValueError):
            read_counters(["<html>bad gateway</html>"])
