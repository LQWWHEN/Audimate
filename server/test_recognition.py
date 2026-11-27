import asyncio
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
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

    def test_mastering_labels_preserve_identity_and_display(self):
        base = "Stairway to Heaven"
        artist = "Led Zeppelin"
        result = dict(id="1", title=base, artist=artist, album="IV")
        for suffix in ("(Remastered)", "（2014 Remaster）", " - Remastered 2014", " [Remastered]"):
            title = dict(title=base+suffix, artist=artist)
            self.room.action("k", "Alice", "track", dict(stream=self.stream, track=title))
            with patch.object(recognition, "identify", AsyncMock(return_value=result)), patch.object(recognition, "localized_names") as lookup:
                self.room.recognition.run(self.room, [], 8000, 2, True)
                lookup.assert_not_called()
            self.assertEqual(self.status()["source"], title["title"]+" - "+artist)
            self.assertEqual(self.status()["recognition"]["current"], result)
            self.assertIn("配置的曲名后缀等价", self.room.monitor.events[0]["detail"])
        for suffix in ("(Live)", "(Remix)", "(Acoustic)", "(Demo)", "(Instrumental)", "(Part II)"):
            self.assertFalse(recognition.song_title_match(base+suffix, base))
        self.assertFalse(recognition.song_title_match("A (2014 Remaster)", "A (1990 Remaster)"))
        self.assertFalse(recognition.title_agreement(result, dict(title=base+"(Remastered)", artist="Tribute Band")))

    def test_suffix_file_reload_and_localized_decision_cache(self):
        track = dict(id="42", title="Song", artist="Artist")
        state = recognition.Recognition(0, dict(title="歌名（2014 母带+）", artist="艺人"))
        alias = dict(title="歌名", artist="艺人")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "suffixes.txt"
            config = recognition.SuffixFile(path)
            path.write_text("# test\n母带+\n", encoding="utf-8-sig")
            with patch.object(recognition, "SUFFIX_FILE", config), patch.object(
                    recognition, "identify", AsyncMock(return_value=track)), patch.object(
                    recognition, "localized_names", AsyncMock(return_value=alias)) as lookup:
                self.assertEqual(asyncio.run(state.query(b"", None))[1], "localized")
                self.assertIn("复用", asyncio.run(state.query(b"", None))[2])
                lookup.assert_awaited_once()
                self.assertEqual(recognition.song_title_match("Song - 母带+ 2014", "Song"), "suffix")
                self.assertFalse(recognition.song_title_match("Song (母带带)", "Song"))
                self.assertFalse(recognition.song_title_match("Song (母带+ 2014)", "Song (母带+ 2015)"))
                path.write_bytes(b"\xff\xfe")
                with self.assertLogs(recognition.__name__, level="WARNING"):
                    self.assertEqual(recognition.song_title_match("歌名 (母带+)", "歌名"), "suffix")
                path.write_text("", encoding="utf-8")
                self.assertFalse(asyncio.run(state.query(b"", None))[1])
                self.assertFalse(recognition.song_title_match("Song (母带+)", "Song"))
                self.assertEqual(recognition.song_title_match("Песня", "ПЕСНЯ"), "exact")

    def test_localized_confirmation_and_cache(self):
        player = dict(title="モルヒネ", artist="椎名林檎")
        track = dict(id="40368919", title="Morphine", artist="Sheena Ringo", album="Muzai Moratorium")
        self.room.action("k", "Alice", "track", dict(stream=self.stream, track=player))
        state = self.room.recognition
        with patch.object(recognition, "identify", AsyncMock(return_value=track)), patch.object(
                recognition, "localized_names", AsyncMock(return_value=player)) as lookup:
            state.run(self.room, [], 8000, 2, False)
            self.assertIsNone(self.status()["recognition"]["current"])
            self.assertIn("候选", self.room.monitor.events[0]["detail"])
            state.run(self.room, [], 8000, 2, False)
            lookup.assert_awaited_once_with("40368919", "ja-JP", "JP")
        self.assertEqual(self.status()["source"], "モルヒネ - 椎名林檎")
        self.assertEqual(self.status()["recognition"]["current"], track)
        self.assertIn("复用目录核对", self.room.monitor.events[0]["detail"])
        self.assertIn("Morphine - Sheena Ringo", self.room.monitor.events[0]["message"])

    def test_chinese_catalog_fallback_and_artist_check(self):
        track = dict(id="100", title="English Title", artist="English Artist")
        player = dict(title="歌名", artist="藝人")
        state = recognition.Recognition(0, player)
        with patch.object(recognition, "identify", AsyncMock(return_value=track)), patch.object(
                recognition, "localized_names", AsyncMock(side_effect=[TimeoutError(), player])) as lookup:
            self.assertEqual(asyncio.run(state.query(b"", None))[1], "localized")
            self.assertEqual([call.args[1] for call in lookup.await_args_list], ["zh-CN", "zh-TW"])
        for alias in (dict(title="另一首歌", artist="藝人"), dict(title="歌名", artist="其他歌手"), None):
            state = recognition.Recognition(0, player)
            with patch.object(recognition, "identify", AsyncMock(return_value=track)), patch.object(
                    recognition, "localized_names", AsyncMock(return_value=alias)):
                self.assertFalse(asyncio.run(state.query(b"", None))[1])

    @unittest.skipUnless(recognition.AVAILABLE, "requires ShazamIO")
    def test_catalog_rejects_different_id_and_survives_timeout(self):
        wrong = dict(key="99", title="モルヒネ", subtitle="椎名林檎")
        with patch("shazamio.Shazam.track_about", AsyncMock(return_value=wrong)):
            self.assertIsNone(asyncio.run(recognition.localized_names("40368919", "ja-JP", "JP")))
        state = recognition.Recognition(0, dict(title="モルヒネ", artist="椎名林檎"))
        track = dict(id="40368919", title="Morphine", artist="Sheena Ringo")
        with patch.object(recognition, "identify", AsyncMock(return_value=track)), patch.object(
                recognition, "localized_names", AsyncMock(side_effect=TimeoutError())) as lookup:
            for _ in range(2):
                raw, agreement, evidence = asyncio.run(state.query(b"", None))
                self.assertIs(raw, track)
                self.assertFalse(agreement)
                self.assertIn("TimeoutError", evidence)
            lookup.assert_awaited_once()

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
