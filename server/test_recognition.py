from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch
import wave

from . import recognition
from .app import Room
from .control import DEFAULT, Problem


def song(key):
    return dict(id=key, title=key, artist="Artist", album="Album " + key)


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.room = Room(development=True)
        self.room.start_watcher = lambda: None
        self.addCleanup(patch.stopall)
        patch.object(recognition, "AVAILABLE", True).start()
        self.choice = dict(rate=8000, bits=16, source="player", recognize=True, codec="pcm")
        self.room.action("k", "Alice", "request", self.choice)
        self.stream = self.room.host["stream"]

    def title(self, value):
        track = dict(title=value, artist="Artist") if value else None
        self.room.action("k", "Alice", "track", dict(stream=self.stream, track=track))

    def status(self):
        return self.room.status("listener")["host"]

    def test_candidate_timeout_no_match_and_third_song(self):
        state = self.room.recognition
        state.receive(song("A"), False)
        self.assertEqual(self.status()["source"], "尝试匹配中")
        self.assertEqual(state.view()["candidate"], song("A"))
        state.receive(song("A"), False)
        state.receive(song("B"), False)
        with patch.object(recognition, "identify", AsyncMock(side_effect=TimeoutError())):
            state.run(self.room, [bytes(32)], 8000, 2, False)
        self.assertEqual(self.status()["source"], "A - Artist")
        self.assertEqual(state.view(), dict(current=song("A"), candidate=song("B"), notice="识别超时，稍后重试"))
        state.receive(song("C"), False)
        self.assertEqual(self.status()["source"], "尝试匹配中")
        state.receive(song("C"), False)
        self.assertEqual(self.status()["source"], "C - Artist")
        state.receive(song("B"), False)
        state.receive(None, False)
        self.assertEqual(state.view(), dict(current=None, candidate=None, notice=""))

    def test_title_priority_conflicting_version_and_quick(self):
        self.title("A (Live)")
        state = self.room.recognition
        for quick in (True, False, False):
            state.receive(song("A"), quick)
        self.assertEqual(self.status()["source"], "A (Live) - Artist")
        self.assertIsNone(state.view()["current"])
        state.receive(song("A (Live)"), True)
        self.assertEqual(state.current["album"], "Album A (Live)")
        self.title("B")
        self.assertIsNone(self.room.recognition.current)
        self.assertEqual(self.status()["source"], "B - Artist")

    def test_ops_reports_provider_match_and_acceptance_separately(self):
        self.title("B")
        state = self.room.recognition
        def query(key, quick):
            with patch.object(recognition, "identify", AsyncMock(return_value=song(key))):
                state.run(self.room, [bytes(32)], 8000, 2, quick)
            return self.room.monitor.events[0]
        event = query("A", True)
        self.assertIn("Quick Fetch · 命中", event["message"])
        self.assertIn("A - Artist", event["message"])
        self.assertIn("与播放器标题不符，未采用", event["detail"])
        self.assertIsNone(state.current)
        self.title(None)
        state = self.room.recognition
        self.assertIn("候选", query("B", False)["detail"])
        confirmed = query("B", False)
        self.assertIn("已确认", confirmed["detail"])
        self.assertIn("Shazam ID B · Album B", confirmed["detail"])

    def test_ops_log_levels_and_exception_redaction(self):
        monitor = self.room.monitor
        state = self.room.recognition
        monitor.set_level("errors")
        with patch.object(recognition, "identify", AsyncMock(return_value=song("A"))):
            state.run(self.room, [bytes(32)], 8000, 2, False)
        self.assertFalse(any(e["kind"] == "识别" for e in monitor.events))
        with patch.object(recognition, "identify", AsyncMock(side_effect=OSError(5, "private-url"))):
            state.run(self.room, [bytes(32)], 8000, 2, False)
            event = monitor.events[0]
            self.assertTrue(event["error"])
            self.assertIn("errno 5", event["detail"])
            self.assertNotIn("private-url", str(event))
            monitor.set_level("off")
            state.run(self.room, [bytes(32)], 8000, 2, False)
            self.assertFalse(monitor.events)
            monitor.set_level("debug")
            monitor.development = False
            state.run(self.room, [bytes(32)], 8000, 2, False)
            self.assertFalse(monitor.events)

    @unittest.skipUnless(recognition.AVAILABLE, "requires ShazamIO")
    def test_ops_timeout_after_http_retry(self):
        from shazamio.api import ShazamUrl
        release = threading.Event()
        class Upstream(BaseHTTPRequestHandler):
            attempts = 0

            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                Upstream.attempts += 1
                if Upstream.attempts == 1:
                    self.send_response(429)
                    self.end_headers()
                else:
                    release.wait(8)

        with ThreadingHTTPServer(("127.0.0.1", 0), Upstream) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                target = f"http://127.0.0.1:{server.server_port}/private-path"
                with patch.object(ShazamUrl, "SEARCH_FROM_FILE", target), patch.object(recognition, "QUERY_TIMEOUT_S", 2):
                    self.room.monitor.set_level("errors")
                    self.room.recognition.run(self.room, [bytes(12*8000*4)], 8000, 2, False)
            finally:
                release.set()
                server.shutdown()
        event = self.room.monitor.events[0]
        self.assertEqual((event["kind"], event["error"]), ("识别", True))
        self.assertIn("完整窗口 · 超时", event["message"])
        for expected in ("TimeoutError", "时限 2 秒", "等待上游响应头", "HTTP 尝试 2 次（429 → 未收到响应头）"):
            self.assertIn(expected, event["detail"])
        self.assertNotIn("private-path", str(event))

    def test_inflight_invalidated_by_title_cache_and_handoff(self):
        for change in ("title", "limits", "handoff"):
            self.title("A")
            old = self.room.recognition
            started, release = threading.Event(), threading.Event()
            async def delayed(wav, trace):
                started.set()
                release.wait(3)
                return song("A")
            with patch.object(recognition, "identify", delayed):
                worker = threading.Thread(target=old.run, args=(self.room, [bytes(32)], 8000, 2, True))
                worker.start()
                try:
                    self.assertTrue(started.wait(2))
                    with self.room.lock:
                        if change == "title":
                            self.title("B")
                        elif change == "limits":
                            self.room.apply_limits(dict(DEFAULT, history_s=11), {})
                            self.assertFalse(self.room.status("listener")["recognition_available"])
                            self.assertIsNone(self.room.recognition)
                            self.room.apply_limits(dict(DEFAULT, history_s=12), {})
                        else:
                            self.room.grant("another", dict(self.choice, name="Bob"))
                finally:
                    release.set()
                    worker.join(3)
            self.assertIsNone(self.room.recognition.current)
            self.assertFalse(any(e["kind"] == "识别" for e in self.room.monitor.events))

    def test_window_selection_padding_and_interval_changes(self):
        rate, width = 8000, 3
        parts = []
        def record(*, target, args, **kwargs):
            room, chunks, sample_rate, sample_width, quick = args
            parts.append((quick, recognition.encode_window(chunks, sample_rate, sample_width, quick)))
            return Mock(start=lambda: setattr(room, "recognition_busy", False))
        self.title("A")
        with patch("server.recognition.threading.Thread", side_effect=record), patch("time.monotonic") as now:
            for n in range(29):
                now.return_value = 200 + n * 0.5
                self.room.append_audio(bytes([n+1])*(rate*width), rate, width)
                if n == 24:
                    self.room.apply_limits(dict(DEFAULT, recognition_interval_s=2), {})
        self.assertEqual([quick for quick, _ in parts], [True, False, False])
        for quick, data in parts:
            with wave.open(BytesIO(data), "rb") as wav:
                self.assertEqual((wav.getnframes(), wav.getsampwidth(), wav.getframerate()), (rate*12,3,rate))
                if quick:
                    self.assertEqual(wav.readframes(rate*5)[0], 1)
                    self.assertFalse(any(wav.readframes(rate*7)))

    def test_gate_and_disabled_hosts(self):
        self.room.reset()
        self.room.apply_limits(dict(DEFAULT, history_s=11), {})
        with self.assertRaises(Problem):
            self.room.action("k", "Alice", "request", self.choice)
        self.room.action("k", "Alice", "request", dict(self.choice, recognize=False))
        self.room.apply_limits(DEFAULT, {})
        self.assertIsNone(self.room.recognition)
        self.assertEqual(self.status()["source"], "player")
