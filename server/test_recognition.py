import asyncio
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch
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

    def player(self, title, artist):
        self.room.action("k", "Alice", "track", dict(stream=self.stream, track=dict(title=title, artist=artist)))
        return self.room.recognition

    def replay(self, provider, quick=False):
        reply = AsyncMock(side_effect=provider) if isinstance(provider, Exception) else AsyncMock(return_value=provider)
        with patch.object(recognition, "identify", reply):
            self.room.recognition.run(self.room, [], 8000, 2, quick)
        return self.room.monitor.events[0]

    def test_quick_corroborated_collaboration(self):
        player = "Digital (Spencer Sotelo)"
        self.player(player, "PhaseOne/Periphery")
        track = dict(id="123", title="Digital (feat. Periphery) (Spencer Sotelo)",
                     artist="Periphery & PhaseOne", album="Album", cover="https://example.test/cover")
        event = self.replay(track, True)
        self.assertIn("合作署名一致", event["detail"])
        self.assertIn("已确认", event["detail"])
        self.assertEqual(self.status()["source"], player+" - PhaseOne/Periphery")
        self.assertEqual(self.status()["recognition"]["current"], track)
        self.player("Digital (Someone Else)", "PhaseOne/Periphery")
        self.assertNotIn("已确认", self.replay(track, True)["detail"])

    def test_fuzzy_streak_requires_two_fresh_full_hits(self):
        state = self.player("Digital (Spensr Sotelo)", "PhaseOne/Periphery")
        track = dict(id="123", title="Digital (feat. Periphery) (Spencer Sotelo)", artist="Periphery & PhaseOne")
        self.assertIn("0/2", self.replay(track, True)["detail"])
        self.assertIsNone(state.candidate)
        self.assertIn("1/2", self.replay(track)["detail"])
        self.replay(TimeoutError())
        self.assertIsNone(state.candidate)
        self.assertIn("1/2", self.replay(track)["detail"])
        self.assertIn("1/2", self.replay(dict(track, id="456"))["detail"])
        self.replay(None)
        self.assertIsNone(state.candidate)
        self.assertIn("1/2", self.replay(track)["detail"])
        event = self.replay(track)
        for text in ("2/2", "模糊兜底采用", "歌名 0.", "艺人 1.000"):
            self.assertIn(text, event["detail"])
        self.assertEqual(self.status()["recognition"]["current"], track)
        self.assertEqual(self.status()["source"], "Digital (Spensr Sotelo) - PhaseOne/Periphery")
        self.player("Digital (Spencor Sotelo)", "PhaseOne/Periphery")
        self.assertIn("1/2", self.replay(track)["detail"])
        self.assertIsNone(self.status()["recognition"]["current"])

    def test_partial_creds_and_fuzzy_guards(self):
        state = self.player("Long Artist Collaboration", "Alice")
        track = dict(id="1", title="Long Artist Collaboration (ft. Bob)", artist="Alice")
        self.assertIn("0/2", self.replay(track, True)["detail"])
        self.assertIn("1/2", self.replay(track)["detail"])
        self.assertIn("2/2", self.replay(track)["detail"])
        self.assertEqual(state.current, track)
        for captured, returned, artist, source_artist in (
            ("One", "On", "Singer", "Singer"), ("爱", "爱你", "Singer", "Singer"),
            ("One (Spencer Sotelo)", "On (Spencer Sotelo)", "Singer", "Singer"),
            ("A very long song title (Live)", "A very long song title (Remix)", "Singer", "Singer"),
            ("A very long song title", "A very long song title - Acoustic", "Singer", "Singer"),
            ("A very long song title (1990 Remaster)", "A very long song title (2014 Remaster)", "Singer", "Singer"),
            ("Thunderstruck", "Thunderstruck", "AC/DC", "AC"),
            ("Thunderstruck", "Thunderstruck", "AC/DC", "AC & DC"),
            ("Same Long Title", "Same Long Title", "Singer", "Unrelated"),
        ):
            with self.subTest(captured=captured, returned=returned, artist=artist):
                state = self.player(captured, artist)
                for _ in range(2):
                    self.replay(dict(id="x", title=returned, artist=source_artist))
                self.assertIsNone(state.current)
                self.assertIsNone(state.candidate)

    def test_fuzzy_policy_live_and_strong_rules_take_precedence(self):
        track = dict(id="1", title="Long Melody Title", artist="John Singer")
        state = self.player("Long Melodi Title", "Jon Singer")
        self.assertIn("1/2", self.replay(track)["detail"])
        for changes in (dict(recognition_artist_min=1.0), dict(recognition_title_min=1.0), dict(recognition_fuzzy=False)):
            self.room.apply_limits(dict(DEFAULT, **changes), {})
            self.assertIsNone(state.candidate)
            self.assertNotIn("1/2", self.replay(track)["detail"])
        self.room.apply_limits(DEFAULT, {})
        self.assertIn("1/2", self.replay(track)["detail"])
        self.assertIn("2/2", self.replay(track)["detail"])
        self.player(track["title"], track["artist"])
        with patch.object(recognition.Credits, "fallback", side_effect=AssertionError("unexpected scoring")):
            self.assertIn("已确认", self.replay(track, True)["detail"])

    def test_scheduled_fuzzy_windows_use_new_audio(self):
        self.room.apply_limits(dict(DEFAULT, recognition_interval_s=1), {})
        state = self.player("I Will Surviva", "Gloria Gaynor")
        track = dict(id="482734446", title="I Will Survive", artist="Gloria Gaynor")
        ends = []
        for second in range(1, 24):
            with patch.object(self.room, "queue_recognition") as worker, patch("time.monotonic", return_value=200+second):
                self.room.append_audio(bytes(8000*4), 8000, 2)
            if worker.called:
                _, *args = worker.call_args.args
                with patch.object(recognition, "identify", AsyncMock(return_value=track)):
                    state.run(self.room, *args)
                ends.append((second, args[-1], state.current is not None))
        self.assertEqual(ends, [(5, True, False), (12, False, False), (22, False, True)])

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

    def test_localized_live_credit_precedes_fuzzy_scoring(self):
        state = self.player("モルヒネ (ライブ)", "椎名林檎")
        track = dict(id="123", title="Morphine (Live)", artist="Sheena Ringo")
        with patch.object(recognition, "localized_names", AsyncMock(return_value=state.title)), patch.object(
                recognition.Credits, "fallback", side_effect=AssertionError("unexpected scoring")):
            self.assertIn("已确认", self.replay(track, True)["detail"])
        self.assertEqual(state.current, track)

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
                raw, agreement, evidence, limited = asyncio.run(state.query(b"", None))
                self.assertIsNone(limited)
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
                    self.send_response(503)
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
        for expected in ("TimeoutError", "时限 2 秒", "等待上游响应头", "HTTP 尝试 2 次（503 → 未收到响应头）"):
            self.assertIn(expected, event["detail"])
        self.assertNotIn("private-path", str(event))
        self.assertIsNone(self.room.recognition_retry_at)

    def test_retry_after_seconds_or_http_date(self):
        with patch.object(recognition.time, "time", return_value=784111770):
            for value, expected in ((" 17 ", 17), ("0", 0), ("Sun, 06 Nov 1994 08:49:37 GMT", 7),
                                    ("Sun, 06 Nov 1994 08:49:29 GMT", 0), (None, None), ("", None),
                                    ("later", None), ("-10", None), ("1.5", None)):
                with self.subTest(value=value):
                    self.assertEqual(recognition.retry_after_seconds(value), expected)

    @unittest.skipUnless(recognition.AVAILABLE, "requires ShazamIO")
    def test_room_reuses_real_http_connection_across_queries_catalog_and_handoff(self):
        from shazamio.api import ShazamUrl

        requests = []
        retry_after = None
        class Upstream(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *args):
                pass
            def reply(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                requests.append((self.command, self.client_address))
                track = dict(key="123", title="Song", subtitle="Artist")
                content = json.dumps(dict(matches=[{}], track=track) if self.command == "POST" else track).encode()
                self.send_response(429 if retry_after else 200)
                if retry_after:
                    self.send_header("Retry-After", retry_after)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            do_POST = do_GET = reply

        def query(status=200):
            with self.room.lock:
                self.room.recognition_busy = True
                self.room.queue_recognition(self.room.recognition, [bytes(12*8000*4)], 8000, 2, False)
                worker = self.room.recognition_worker
            worker.executor.submit(lambda: None).result(timeout=8)
            self.assertFalse(self.room.recognition_busy)
            self.assertIn(f"HTTP 尝试 1 次（{status}）", self.room.monitor.events[0]["detail"])
            return worker

        with ThreadingHTTPServer(("127.0.0.1", 0), Upstream) as upstream:
            threading.Thread(target=upstream.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{upstream.server_port}"
            try:
                with patch.object(ShazamUrl, "SEARCH_FROM_FILE", url), patch.object(ShazamUrl, "ABOUT_TRACK", url):
                    self.player("曲名", "歌手")
                    first = query()
                    self.room.grant("next", dict(self.choice, name="Bob"))
                    self.assertIs(query(), first)
                    self.assertEqual([m for m, _ in requests], ["POST", "GET", "GET", "GET", "POST"])
                    self.assertEqual(len({addr for _, addr in requests}), 1)
                    self.room.expire(first.idle_since + recognition.SESSION_IDLE_S)
                    self.assertIsNone(self.room.recognition_worker)
                    first.executor.shutdown(wait=True)
                    retry_after = "120"
                    second = query(429)
                    self.assertNotEqual(requests[-1][1], requests[0][1])
                    deadline = self.room.recognition_retry_at
                    self.room.expire(second.idle_since + recognition.SESSION_IDLE_S)
                    self.room.append_audio(bytes(12*8000*4), 8000, 2)
                    self.assertIsNone(self.room.recognition_worker)
                    self.assertEqual(self.room.recognition_retry_at, deadline)
                    self.room.reset()
                    self.assertIsNone(self.room.recognition_worker)
                    second.executor.shutdown(wait=True)
            finally:
                self.room.close_recognizer()
                upstream.shutdown()

    def test_step_down_cancels_inflight_query(self):
        entered, stopped = threading.Event(), threading.Event()
        async def slow(wav, trace):
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()
        with patch.object(recognition, "identify", slow):
            with self.room.lock:
                self.room.recognition_busy = True
                self.room.queue_recognition(self.room.recognition, [], 8000, 2, False)
                worker = self.room.recognition_worker
            try:
                self.assertTrue(entered.wait(3))
                with self.room.lock:
                    self.room.grant("next", dict(self.choice, name="Bob", recognize=False))
                self.assertTrue(stopped.wait(2))
                self.assertIsNone(self.room.recognition_worker)
            finally:
                self.room.close_recognizer()
                worker.executor.shutdown(wait=True)
        self.assertFalse(self.room.recognition_busy)

    @unittest.skipUnless(recognition.AVAILABLE, "requires ShazamIO")
    def test_sdk_stops_on_first_429_for_fingerprint_and_catalog(self):
        from shazamio.api import ShazamUrl

        hit = dict(matches=[{}], track=dict(key="100", title="Song", subtitle="Artist"))
        responses = [(429, "41", {}), (200, None, hit), (429, None, {}), (429, "bad", {})]
        requests = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                requests.append(self.command)
                status, delay, payload = responses.pop(0) if responses else (400, None, {})
                self.send_response(status)
                if delay is not None:
                    self.send_header("Retry-After", delay)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            do_GET = do_POST = respond

        audio = [bytes(12 * 8000 * 4)]
        with ThreadingHTTPServer(("127.0.0.1", 0), Upstream) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with patch.object(ShazamUrl, "SEARCH_FROM_FILE", url), patch.object(ShazamUrl, "ABOUT_TRACK", url), \
                        patch.object(recognition, "time", wraps=time) as clock:
                    clock.monotonic.return_value = 100
                    self.room.monitor.set_level("errors")
                    state = self.room.recognition
                    state.current = song("A")
                    state.run(self.room, audio, 8000, 2, False)
                    event = self.room.monitor.events[0]
                    self.assertEqual(requests, ["POST"])
                    self.assertTrue(event["error"])
                    self.assertIn("上游限流", event["message"])
                    self.assertIn("冷却 41 秒（Retry-After）", event["detail"])
                    self.assertIn("HTTP 尝试 1 次（429）", event["detail"])
                    self.assertEqual((self.room.recognition_retry_at, self.status()["source"]), (141, "A - Artist"))

                    clock.monotonic.return_value = 141
                    state = self.player("歌曲", "艺人")
                    state.run(self.room, audio, 8000, 2, True)
                    event = self.room.monitor.events[0]
                    self.assertEqual(requests, ["POST", "POST", "GET"])
                    self.assertIn("命中", event["message"])
                    self.assertIn("Song - Artist", event["message"])
                    self.assertIn("本地化目录 HTTP 429 · 冷却 30 秒（默认）", event["detail"])
                    self.assertIn("指纹查询 HTTP 尝试 1 次（200）", event["detail"])
                    self.assertEqual((self.room.recognition_retry_at, self.status()["source"]), (171, "歌曲 - 艺人"))

                    clock.monotonic.return_value = 171
                    self.room.monitor.development = False
                    self.room.monitor.set_level("off")
                    state.run(self.room, audio, 8000, 2, False)
                    self.assertEqual(requests, ["POST", "POST", "GET", "POST"])
                    self.assertEqual(self.room.recognition_retry_at, 201)
                    self.assertFalse(self.room.monitor.events)
            finally:
                server.shutdown()

    def test_cooldown_survives_handoff_and_resumes_once_from_latest_audio(self):
        self.title("A")
        old = self.room.recognition

        async def limited_after_handoff(wav, trace):
            self.room.grant("another", dict(self.choice, name="Bob"))
            raise recognition.RateLimited(None)

        with patch.object(recognition, "time", wraps=time) as clock:
            clock.monotonic.return_value = 100
            with patch.object(recognition, "identify", limited_after_handoff):
                old.run(self.room, [], 8000, 2, True)
            self.assertEqual(self.room.recognition_retry_at, 130)
            self.assertFalse(self.room.recognition.notice)
            self.assertIn("上游限流", self.room.monitor.events[0]["message"])
            probes = []
            for second in range(1, 56):
                clock.monotonic.return_value = 100 + second
                if second in (23, 32):
                    title = "B" if second == 23 else "C"
                    self.room.action("another", "Bob", "track",
                                     dict(stream=self.room.host["stream"], track=dict(title=title, artist="Artist")))
                with patch.object(self.room, "queue_recognition") as worker:
                    self.room.append_audio(bytes([second]) * (8000 * 4), 8000, 2)
                if worker.called:
                    state, *args = worker.call_args.args
                    probes.append((second, args[-1], args[0][0][0], args[0][-1][-1]))
                    reply = AsyncMock(side_effect=recognition.RateLimited("15")) if second == 30 else AsyncMock(return_value=None)
                    with patch.object(recognition, "identify", reply):
                        state.run(self.room, *args)
            self.assertEqual(probes, [(30, True, 26, 30), (45, False, 34, 45), (55, False, 44, 55)])
            self.assertIsNone(self.room.recognition_retry_at)
            self.assertFalse(self.room.recognition.notice)

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
        def record(state, chunks, sample_rate, sample_width, quick):
            parts.append((quick, recognition.encode_window(chunks, sample_rate, sample_width, quick)))
            self.room.recognition_busy = False
        self.title("A")
        with patch.object(self.room, "queue_recognition", side_effect=record), patch("time.monotonic") as now:
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
