import http.client
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from wsgiref.simple_server import make_server, WSGIRequestHandler

from client.network import API
from .app import App, LEASE
from .dev import ThreadedServer


class Quiet(WSGIRequestHandler):
    def log_message(self, *_):
        pass


class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "accounts.sqlite3"
        self.app = App(self.db)
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
            name, t = self.app.room.members[key]
            self.app.room.members[key] = name, t - LEASE - 1
        self.assertIsNone(self.bob.request("room")["host"])

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
