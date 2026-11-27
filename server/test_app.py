import http.client
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

from client.network import API
from .app import App
from .dev import ThreadedServer
from .ops_admin import manage


class Quiet(WSGIRequestHandler):
    def log_message(self, *_):
        pass


class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "accounts.sqlite3"
        self.app = App(self.db, development=getattr(self, "development", False))
        server = make_server("127.0.0.1", 0, self.app, ThreadedServer, Quiet)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"
        self.alice = API(self.url)
        self.bob = API(self.url)
        self.choice = dict(source="音乐播放器", rate=44100, bits=24)

    def register(self, api, name):
        return api.request("register", {"name": name, "password": "local-test-password"})

    def test_accounts(self):
        with self.assertRaisesRegex(OSError, "401"):
            self.bob.request("room")
        self.assertEqual(self.register(self.alice, "Alice")["name"], "Alice")
        self.assertEqual(self.alice.request("me")["name"], "Alice")
        with self.assertRaisesRegex(OSError, "409"):
            self.register(self.bob, "alice")
        with self.assertRaisesRegex(OSError, "401"):
            self.bob.request("login", {"name": "Alice", "password": "incorrect-test"})
        token = self.alice.cookie
        self.alice.request("logout", {})
        self.alice.cookie = token
        with self.assertRaisesRegex(OSError, "401"):
            self.alice.request("me")
        self.bob.request("login", {"name": "alice", "password": "local-test-password"})
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT length(salt),length(hash) FROM users").fetchone(), (16, 32))
        # A new app instance opens the same account/session database.
        self.assertEqual(len(App(self.db).room.members), 0)

    def test_flac_round_and_cache(self):
        from . import flac
        self.register(self.alice, "发送")
        self.register(self.bob, "接收")
        host = self.alice.request("stage/request", dict(self.choice, compression="flac"))["host"]
        self.assertEqual(self.bob.request("room")["host"]["compression"], "flac")
        base = "audio?stream="+host["stream"]
        path = base+"&compression=flac"
        pcm = (b"\xff\xff\x7f\x00\x00\x80") * 8820
        wire = flac.encode(pcm, 44100, 24)
        self.assertLess(len(wire), len(pcm))
        self.alice.request(path, wire)
        seq, received, headers = self.bob.request(path+"&sync=1", metadata=True)
        self.assertEqual((seq, received, headers["Content-Type"]), (1, wire, "audio/flac"))
        self.assertEqual(flac.decode(received, 44100, 24), pcm)
        self.assertEqual(self.app.room.history.end_frame, 8820)
        self.assertEqual(self.app.room.history.blocks[-1][1], pcm)  # shared Opus bridge input
        self.assertIsNone(self.app.room.media.bridge)
        self.assertEqual(self.app.room.monitor.uploaded, len(wire))
        self.assertEqual(self.app.room.monitor.downloaded, len(wire))
        for invalid in (pcm, wire[:-7], flac.encode(pcm, 48000, 24)):
            with self.assertRaisesRegex(OSError, "400"):
                self.alice.request(path, invalid)
        with self.assertRaisesRegex(OSError, "415"):
            self.bob.request(base)
        with self.assertRaisesRegex(OSError, "415"):
            self.alice.request(base, pcm)
        with self.assertRaisesRegex(OSError, "403"):
            self.bob.request(path, wire)
        self.assertEqual(self.app.room.history.sequence, 1)
        self.bob.request("stage/request", self.choice)
        pending = self.alice.request("room")["requests"][0]
        self.alice.request("stage/accept", dict(id=pending["id"],stream=host["stream"]))
        self.assertEqual(self.alice.request("room")["host"]["compression"], "none")
        with self.assertRaisesRegex(OSError, "409"):
            self.alice.request(path, wire)

    def test_visual_style_is_owned_by_each_publishing_round(self):
        self.register(self.alice, "主持")
        self.register(self.bob, "听众")
        a = self.alice.request("stage/request", self.choice)["host"]
        self.assertEqual(a["visual_style"], "pop")
        change = {"stream": a["stream"], "visual_style": "heavy"}
        with self.assertRaisesRegex(OSError, "403"):
            self.bob.request("stage/style", change)
        self.alice.request("stage/style", change)
        self.assertEqual(self.bob.request("room")["host"]["visual_style"], "heavy")
        profile = dict(self.bob.request("room")["host"]["spectrum"], start=30, custom=True)
        self.alice.request("stage/style", dict(change, spectrum=profile))
        self.assertEqual(self.bob.request("room")["host"]["spectrum"], profile)
        for invalid in (dict(profile, count=10000), dict(profile, eq=[3]), dict(profile, version=2)):
            with self.assertRaisesRegex(OSError, "400"):
                self.alice.request("stage/style", dict(change, spectrum=invalid))
        self.assertEqual(self.bob.request("room")["host"]["spectrum"], profile)
        self.alice.request("stage/style", change)
        self.assertFalse(self.bob.request("room")["host"]["spectrum"]["custom"])
        with self.assertRaisesRegex(OSError, "400"):
            self.alice.request("stage/style", dict(change, visual_style="invalid"))
        self.bob.request("stage/request", dict(self.choice, visual_style="light"))
        req = self.alice.request("room")["requests"][0]
        state = self.alice.request("stage/accept", dict(stream=a["stream"], id=req["id"]))
        self.assertEqual(state["host"]["visual_style"], "light")
        with self.assertRaisesRegex(OSError, "409"):
            self.alice.request("stage/style", change)

    def test_handoff_and_audio(self):
        self.register(self.alice, "主持甲")
        self.register(self.bob, "主持乙")
        a = self.alice.request("stage/request", self.choice)["host"]
        path = "audio?stream=" + a["stream"]
        pcm = bytes(range(120))
        self.alice.request(path, pcm)
        self.assertEqual(self.bob.request(path + "&after=-1"), (1, pcm))
        self.assertIsNone(self.bob.request(path + "&after=1"))
        with self.assertRaisesRegex(OSError, "403"):
            self.bob.request(path, pcm)
        self.assertTrue(self.bob.request("stage/request", self.choice)["pending"])
        req = self.alice.request("room")["requests"][0]
        with self.assertRaisesRegex(OSError, "403"):
            self.bob.request("stage/accept", {"id": req["id"], "stream": a["stream"]})
        self.alice.request("stage/reject", {"id": req["id"], "stream": a["stream"]})
        self.assertFalse(self.bob.request("room")["pending"])
        self.bob.request("stage/request", self.choice)
        req = self.alice.request("room")["requests"][0]
        b = self.alice.request("stage/accept", {"id":req["id"], "stream":a["stream"]})["host"]
        self.assertEqual(b["name"], "主持乙")
        with self.assertRaisesRegex(OSError, "409"):
            self.alice.request(path, pcm)
        self.assertIsNone(self.bob.request("audio?stream=" + b["stream"]))
        self.assertIsNone(self.bob.request("stage/leave", {"stream":b["stream"]})["host"])
        self.alice.request("stage/request", self.choice)
        with self.app.room.lock:
            key = self.app.room.host["key"]
            self.app.room.members[key].seen -= self.app.room.lease_s + 1
        self.assertIsNone(self.bob.request("room")["host"])

    def test_track_follows_the_current_stream(self):
        self.register(self.alice, "主持甲")
        self.register(self.bob, "听众乙")
        original = self.alice.request("stage/request", self.choice)["host"]
        stream = original["stream"]
        change = {"stream": stream, "track": {"title": "夜曲", "artist": "周杰伦"}}
        with self.assertRaisesRegex(OSError, "403"):
            self.bob.request("stage/track", change)
        self.alice.request("stage/track", change)
        host = self.bob.request("room")["host"]
        self.assertEqual((host["stream"], host["source"], host["track"]), (stream, "夜曲 - 周杰伦", change["track"]))
        for track in ({}, {"title": "下一首", "artist": ""}, {"title": "a"*161, "artist": "b"},
                      {"title": "\ud800", "artist": "b"}, "raw caption"):
            with self.assertRaisesRegex(OSError, "400"):
                self.alice.request("stage/track", dict(change, track=track))
        cleared = self.alice.request("stage/track", dict(change, track=None))["host"]
        self.assertEqual(cleared["source"], self.choice["source"])
        self.alice.request("stage/track", change)
        self.alice.request("stage/leave", {"stream": stream})
        self.assertIsNone(self.alice.request("stage/request", self.choice)["host"]["track"])
        with self.assertRaisesRegex(OSError, "409"):
            self.alice.request("stage/track", change)

    def test_web_origin_and_files(self):
        con = http.client.HTTPConnection(API(self.url).url.netloc)
        try:
            for path in ("/", "/app.js", "/app.css", "/audio-worker.js"):
                con.request("GET", path)
                response = con.getresponse()
                self.assertEqual(response.status, 200)
                self.assertGreater(len(response.read()), 100)
            con.request("POST", "/api/register", json.dumps({}),
                        {"Origin":"https://another.example","Content-Type":"application/json"})
            self.assertEqual(con.getresponse().status, 403)
        finally:
            con.close()

    def test_ops_session_scope_and_role(self):
        def request(path, cookie="", data=None):
            con = http.client.HTTPConnection(API(self.url).url.netloc)
            try:
                headers = {"Cookie": cookie, "Content-Type": "application/json"}
                con.request("GET" if data is None else "POST", path,
                            json.dumps(data) if data is not None else None, headers)
                resp = con.getresponse()
                resp.read()
                return resp.status, resp.getheader("Set-Cookie", "").split(";")[0]
            finally:
                con.close()

        path = "/ops/api/overview"
        login = "/ops/api/login"
        self.assertEqual(request(path)[0], 401)
        self.register(self.alice, "Alice")
        creds = dict(name="Alice", password="local-test-password")
        self.assertEqual(request(login, data=creds)[0], 403)
        manage(self.db, "grant", "Alice")
        self.assertEqual(request(path, self.alice.cookie)[0], 401)
        code, cookie = request(login, data=creds)
        self.assertEqual(code, 200)
        self.assertEqual(request(path, cookie)[0], 200)
        self.assertEqual(len(self.app.room.members), 0)
        self.assertEqual(request("/api/room", cookie.replace("audimate_ops=", "audimate="))[0], 401)
        self.assertEqual(request("/ops/api/logout", cookie, {})[0], 200)
        self.assertEqual(request(path, cookie)[0], 401)
        cookie = request(login, data=creds)[1]
        manage(self.db, "revoke", "Alice")
        manage(self.db, "grant", "Alice")
        self.assertEqual(request(path, cookie)[0], 401)
        self.assertEqual(self.alice.request("me")["name"], "Alice")

    def test_ops_traffic_history_and_expiry(self):
        self.register(self.alice, "主持人")
        self.register(self.bob, "听众")
        room = self.app.room
        now = int(time.monotonic())
        with patch("server.monitor.time.monotonic", return_value=now):
            host = self.alice.request("stage/request", self.choice)["host"]
            path = "audio?stream=" + host["stream"]
            self.assertIsNone(self.bob.request(path))
            pcm = bytes(range(120))
            for _ in range(14):
                self.alice.request(path, pcm)
            self.assertEqual(self.bob.request(path + "&after=0"), (1, pcm))
            with self.assertRaisesRegex(OSError, "403"):
                self.bob.request(path, pcm)
            with room.lock:
                s = room.monitor.snapshot(room, now + 1, 19)
            self.assertEqual((s["rx"], s["tx"], s["uploads"], s["downloads"]), (1680, 120, 14, 1))
            self.assertEqual((s["empty_reads"], s["cursor_gaps"], s["failures"], s["pulling"]), (1, 0, 1, 1))
            self.assertEqual(s["members"][0]["client"], "Windows 客户端")
            self.assertEqual((s["buffer_blocks"], s["host"]["sequence"]), (14, 14))
            self.assertEqual((s["rx_bps"], s["tx_bps"]), (1344, 96))
            self.assertNotIn(host["stream"], json.dumps(s))
            self.assertNotIn("audimate=", json.dumps(s))
        with room.lock:
            room.expire(now + room.lease_s + 1)
            later = room.monitor.snapshot(room, now + 610, 19)
        self.assertEqual(later["members"], [])
        self.assertIsNone(later["host"])
        self.assertTrue(all(p["rx_bps"] == p["tx_bps"] == 0 for p in later["history"]))
        self.assertTrue(any("连接超时" in e["message"] for e in later["events"]))
