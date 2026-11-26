"""Check cross-endpoint correlation and the release/development boundary over HTTP."""
from contextlib import closing
import json
import sqlite3
import time
import unittest

from . import test_app, test_runtime
from .app import App
from .ops_admin import manage


class TimingCase(unittest.TestCase):
    setUp = test_app.Integration.setUp
    ops = test_runtime.Runtime.ops

    def start(self):
        password = manage(self.db, "create", "ops_timing")
        self.assertEqual(self.ops("login", dict(name="ops_timing", password=password))[0], 200)
        for name, api in (("sender", self.alice), ("listener", self.bob)):
            api.request("register", dict(name=name, password="local-test-password"))
        room = self.alice.request("stage/request", self.choice)
        self.assertEqual(room["config"].get("dev_timing"), True if self.app.development else None)
        return room["host"]["stream"]

    def asset(self, name="timing-dev.js"):
        with closing(self.alice.connection()) as conn:
            conn.request("GET", "/ops/"+name)
            with conn.getresponse() as response:
                return response.status


class ReleaseTiming(TimingCase):
    def test_release_ignores_development_fields_even_at_debug(self):
        stream = self.start()
        self.assertEqual(self.ops("overview")[1]["service"]["active"]["logging"], "debug")
        self.assertEqual(self.asset(), 404)
        self.assertEqual(self.asset("latency-dev.js"), 404)
        path = f"audio?stream={stream}&packet=11"
        self.assertEqual(self.alice.request(path, bytes(120)), {})
        headers = self.bob.request(path+"&trace=1", metadata=True)[2]
        self.assertFalse(any(key.startswith("X-Audimate-") for key in headers))
        self.alice.request("diagnostics", dict(stream=stream, event="publish", transport="pcm",
                           upload_ms=40, detail={"source_seq":11, "queue_ms":8}))
        overview = self.ops("overview")[1]
        self.assertNotIn("timing", overview)
        self.assertNotIn("detail", overview["samples"][0])
        self.assertNotIn("peer", overview["samples"][0])
        self.assertEqual(overview["samples"][0]["upload_ms"], 40)
        self.assertNotIn("detail", App(self.db).room.monitor.samples[0])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertFalse(conn.execute("SELECT name FROM sqlite_master WHERE name='dev_transport_log'").fetchone())
        self.alice.request("stage/leave", {"stream":stream})


class DevelopmentTiming(TimingCase):
    development = True

    def test_development_correlates_packets_and_late_timeout(self):
        stream = self.start()
        self.assertEqual(self.asset(), 200)
        self.assertEqual(self.asset("latency-dev.js"), 200)
        now = round(time.time()*1000)
        path = f"audio?stream={stream}&packet=11&sent={now}&captured={now-200}"
        self.assertEqual(self.alice.request(path, bytes(120)), {"server_seq":1})
        _, _, headers = self.bob.request(path+"&trace=1", metadata=True)
        trace = json.loads(headers["X-Audimate-Timing"])
        self.assertEqual((trace["server_seq"], trace["source_seq"]), (1, 11))
        for stage in ("lock_ms","store_ms","server_body_ms","capture_age_ms","uplink_est_ms","serve_ms","residence_ms"):
            self.assertGreaterEqual(trace[stage], 0)
        self.assertIn("X-Audimate-Served-Ms", headers)
        self.bob.request("diagnostics", dict(stream=stream, event="play", transport="pcm",
                         detail=dict(trace, decode_ms=2.75, token="invalid", queue_ms=-1)))
        snap = self.ops("overview")[1]
        row = next(r for r in snap["timing"]["rows"] if r["stage"] == "decode_ms")
        self.assertEqual((row["n"],row["p50"],row["p95"],row["peak"]), (1,2.75,2.75,2.75))
        self.assertEqual({s["event"] for s in snap["samples"]}, {"ingest","play"})
        self.assertNotIn("token", snap["samples"][0]["detail"])
        self.assertNotIn("queue_ms", snap["samples"][0]["detail"])
        self.assertEqual(snap["samples"][0]["round_id"], snap["timing"]["round_id"])
        self.assertEqual(snap["samples"][0]["role"], "listener")
        self.assertIn(snap["samples"][0]["peer"], [m["peer"] for m in snap["members"] if not m["host"]])

        self.ops("service/logging", {"logging":"errors"})
        self.alice.request(f"audio?stream={stream}&packet=12", bytes(120))
        self.alice.request("stage/leave", {"stream":stream,"reason":"upload"})
        fault = dict(stream=stream, event="error", transport="pcm", report_id="ab"*8,
                     detail=dict(source_seq=12, phase="headers", timed_out=1, unconfirmed=1))
        self.bob.request("diagnostics", fault)
        self.alice.request("diagnostics", fault)
        self.alice.request("diagnostics", fault)
        samples = self.ops("overview")[1]["samples"]
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["detail"]["server_seq"], 2)
        self.assertEqual(samples[0]["detail"]["phase"], "headers")
        self.assertEqual(samples[0]["name"], "sender")
        end = time.monotonic()+5
        with closing(sqlite3.connect(self.db)) as conn:
            while not conn.execute("SELECT 1 FROM dev_transport_log").fetchone():
                self.assertLess(time.monotonic(), end)
                time.sleep(.04)
        self.assertEqual(App(self.db, development=True).room.monitor.samples[0], samples[0])
        self.assertFalse(App(self.db).room.monitor.samples)
        self.ops("service/logging", {"logging":"off"})
        self.assertFalse(App(self.db, development=True).room.monitor.samples)
