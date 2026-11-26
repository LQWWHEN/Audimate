"""Regression tests for lifecycle ordering, elapsed-time retention and media authorization."""
import http.client
import json
import os
import unittest
from unittest.mock import patch

from .app import App
from .media import History, Media
from .ops_admin import manage
from . import test_app


class Runtime(unittest.TestCase):
    setUp = test_app.Integration.setUp

    def ops(self, route, data=None):
        conn = http.client.HTTPConnection(self.alice.url.hostname, self.alice.url.port)
        headers = {"Content-Type": "application/json", "Cookie": getattr(self, "cookie", "")}
        conn.request("POST" if data is not None else "GET", "/ops/api/"+route,
                     None if data is None else json.dumps(data), headers)
        response = conn.getresponse()
        if response.getheader("Set-Cookie"):
            self.cookie = response.getheader("Set-Cookie").split(";")[0]
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_maintenance_and_accounts(self):
        secret = manage(self.db, "create", "operator")
        self.assertEqual(self.ops("login",dict(name="operator",password=secret))[0], 200)
        credentials = dict(name="listener",password="local-test-password")
        self.alice.request("register", credentials)
        self.alice.request("stage/request", self.choice)
        self.assertEqual(self.ops("service/configure",dict(history_s=2, intervals=[5]))[0],409)
        self.ops("service/maintenance", {})
        with self.assertRaisesRegex(OSError,"401"):
            self.alice.request("room")
        with self.assertRaisesRegex(OSError,"503"):
            self.alice.request("login",credentials)
        state = self.ops("service/configure",dict(history_s=2,intervals=[10,5]))[1]["service"]
        self.assertEqual((state["active"]["history_s"],state["pending"]["history_s"]),(5,2))
        self.assertEqual(self.ops("service/open",{})[0],409)
        self.assertTrue(self.ops("service/restart",{})[1]["service"]["maintenance"])
        restored = App(self.db)
        self.assertTrue(restored.control.maintenance)
        self.assertEqual(restored.room.history.seconds,2)
        self.ops("service/open",{})
        self.alice.request("login",credentials)
        self.assertIsNone(self.alice.request("room")["host"])
        self.ops("accounts",dict(action="disable",name="listener"))
        with self.assertRaisesRegex(OSError,"401"):
            self.alice.request("login",credentials)
        self.ops("accounts", dict(action="enable",name="listener"))
        self.ops("accounts", dict(action="password",name="listener",password="new-local-password"))
        with self.assertRaisesRegex(OSError,"401"):
            self.alice.request("login",credentials)
        self.alice.request("login",dict(credentials,password="new-local-password"))
        self.assertEqual(self.ops("accounts",dict(action="disable",name="operator"))[0],403)

    def test_grants_and_diagnostics(self):
        self.alice.request("register",dict(name="host",password="local-test-password"))
        self.bob.request("register",dict(name="peer",password="local-test-password"))
        with patch.dict(os.environ, LIVEKIT_URL="ws://localhost:1",LIVEKIT_API_KEY="local",
                        LIVEKIT_API_SECRET=os.urandom(32).hex()):
            media = self.app.room.media = Media()
        with patch.object(media,"start_bridge"), patch.object(media,"remove"):
            host = self.alice.request("stage/request",dict(self.choice,codec="opus"))["host"]
            request = dict(stream=host["stream"],role="publish")
            with self.assertRaisesRegex(OSError,"403"):
                self.bob.request("media", request)
            token = self.alice.request("media",request)["token"]
            self.assertTrue(media.current_grant(token,self.app.room))
            self.bob.request("diagnostics",dict(stream=host["stream"],event="play",transport="rtc",
                                               rtt_ms=31,playout_ms=-1,token="private"))
            sample = self.app.room.monitor.samples[0]
            self.assertEqual(sample["rtt_ms"],31)
            self.assertNotIn("playout_ms",sample)
            self.assertNotIn("token",sample)
            self.assertEqual(App(self.db).room.monitor.samples[0],sample)
            self.app.room.monitor.set_level("off")
            self.assertFalse(self.app.room.monitor.samples)
            self.alice.request("stage/leave",dict(stream=host["stream"]))
            self.assertFalse(media.current_grant(token,self.app.room))


class Timeline(unittest.TestCase):
    def test_media_duration_and_idle_expiry(self):
        history = History(2)
        with patch("server.media.time.monotonic",return_value=10):
            for _ in range(15):
                history.append(bytes(400),1000,1)
            self.assertLessEqual(history.end_frame-history.blocks[0][2],2000)
            block, expired = history.read(1,1000,False)
            self.assertTrue(expired)
            self.assertEqual(history.end_frame-block[2],600)
        with patch("server.media.time.monotonic",return_value=12):
            self.assertEqual(history.read(-1,1000,True),(None,False))
