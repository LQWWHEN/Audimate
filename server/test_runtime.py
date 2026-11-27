"""Regression tests for lifecycle ordering, elapsed-time retention and media authorization."""
import http.client
import json
import os
import sqlite3
import threading
import time
import unittest
from contextlib import closing
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
        self.assertEqual(self.ops("service/configure",dict(history_s=2, intervals=[5]))[0],200)
        self.ops("service/maintenance", {})
        with self.assertRaisesRegex(OSError,"401"):
            self.alice.request("room")
        with self.assertRaisesRegex(OSError,"503"):
            self.alice.request("login",credentials)
        state = self.ops("service/configure",dict(history_s=2,intervals=[10,5]))[1]["service"]
        self.assertEqual((state["active"]["history_s"],state["pending"]["history_s"]),(2,2))
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

    def test_conditional_room_views_interest_and_audio_progress(self):
        for peer, name in ((self.alice, "host"), (self.bob, "peer")):
            peer.request("register", dict(name=name, password="local-test-password"))
        self.bob.request("room?want=1")
        host = self.alice.request("stage/request", self.choice)
        stream = host["host"]["stream"]
        view = self.bob.request("room?want=1")
        poll = "room?want=1&since=" + view["cursor"]
        ack = {"unchanged": True, "cursor": view["cursor"]}
        self.assertEqual(self.bob.request(poll), ack)
        self.assertIsNone(self.bob.request("audio?stream=" + stream))
        room = self.app.room
        listener = next(m for m in room.members.values() if m.name == "peer")
        self.assertTrue(listener.want_listen)
        self.assertIsNone(listener.last_pull)
        room.monitor.set_level("off")
        for transport in ("pcm", "rtc"):
            received = poll + f"&stream={stream}&transport={transport}&position=2"
            self.assertEqual(self.bob.request(received), ack)
            observed = listener.last_pull
            self.assertIsNotNone(observed)
            self.bob.request(received)
            self.assertEqual(listener.last_pull, observed)
        self.bob.request("stage/request", self.choice)
        changed = self.alice.request("room?since=" + host["cursor"])
        self.assertEqual(changed["requests"][0]["name"], "peer")
        mine = self.bob.request(poll)
        self.assertTrue(mine["pending"])
        self.assertFalse(mine["requests"])
        self.assertNotEqual(mine["cursor"], changed["cursor"])
        self.alice.request("stage/leave", dict(stream=stream))
        self.assertTrue(listener.want_listen)
        self.assertIsNone(listener.last_pull)
        self.bob.request("room?want=0")
        self.assertFalse(listener.want_listen)
        self.assertIn("config", self.bob.request("room"))

    def test_audio_does_not_renew_host_past_fixed_grace(self):
        self.alice.request("register", dict(name="host", password="local-test-password"))
        self.bob.request("register", dict(name="peer", password="local-test-password"))
        room = self.app.room
        with patch.object(room, "start_watcher"), patch("time.monotonic", return_value=100) as clock:
            host = self.alice.request("stage/request", self.choice)["host"]
            key = room.host["key"]
            audio = "audio?stream=" + host["stream"]
            for second in range(1, 30):
                clock.return_value = 100 + second
                self.alice.request(audio, bytes(120))
                self.assertEqual(room.members[key].seen, 100)
            self.assertTrue(room.host_in_grace(room.members[key], 129))
            self.bob.request("stage/request", self.choice)
            clock.return_value = 130
            with self.assertRaisesRegex(OSError, "409"):
                self.alice.request(audio, bytes(120))
            self.assertIsNone(room.host)
            self.assertTrue(self.bob.request("stage/request", self.choice)["hosting"])
            clock.return_value = 151
            self.assertIsNone(self.bob.request("room")["host"])

    def test_heartbeat_recovers_host_during_grace(self):
        self.alice.request("register", dict(name="host", password="local-test-password"))
        room = self.app.room
        with patch.object(room, "start_watcher"), patch("time.monotonic", return_value=100) as clock:
            stream = self.alice.request("stage/request", self.choice)["host"]["stream"]
            clock.return_value = 119
            self.alice.request("audio?stream=" + stream, bytes(120))
            clock.return_value = 121
            self.assertTrue(self.alice.request("room?want=0")["hosting"])
            clock.return_value = 131
            self.assertTrue(self.alice.request("room?want=0")["hosting"])

    def test_member_timeout_configuration(self):
        password = manage(self.db, "create", "operator")
        self.ops("login", dict(name="operator", password=password))
        config = dict(history_s=5, intervals=[5, 15])
        self.assertEqual(self.ops("overview")[1]["lease_s"], 20)
        self.assertEqual(self.ops("service/configure", dict(config, lease_s=8))[0], 200)
        self.ops("service/maintenance", {})
        for seconds in (5, 300, 8):
            status, snapshot = self.ops("service/configure", dict(config, lease_s=seconds))
            self.assertEqual(status, 200)
            self.assertEqual(snapshot["service"]["pending"]["lease_s"], seconds)
            self.assertEqual(snapshot["lease_s"], seconds)
        for invalid in (4, 301, True, "8", 8.5, None):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.ops("service/configure", dict(config, lease_s=invalid))[0], 400)
        # An older dashboard can save the other settings without reverting this one.
        saved = self.ops("service/configure", config)[1]["service"]
        self.assertEqual(saved["pending"]["lease_s"], 8)
        self.assertIn("lease_s", saved["audit"][0]["action"])
        self.assertEqual(self.ops("service/restart", {})[1]["lease_s"], 8)
        self.assertEqual(self.ops("service/open", {})[0], 200)
        self.assertEqual(App(self.db).room.lease_s, 8)

        for api, name in ((self.alice, "host"), (self.bob, "listener")):
            api.request("register", dict(name=name, password="local-test-password"))
        self.alice.request("stage/request", self.choice)
        self.bob.request("stage/request", self.choice)
        room = self.app.room
        with room.lock:
            host = room.host["key"]
            applicant = next(iter(room.pending))
            now = time.monotonic()
            room.members[host].seen = now - 8
            room.members[applicant].seen = now - 8.01
            room.expire(now)
            self.assertEqual(set(room.members), {host})
            self.assertFalse(room.pending)
            self.assertEqual(room.host["key"], host)
            room.members[host].seen = now - 7
        self.assertTrue(self.alice.request("room")["hosting"])
        with room.lock:
            now = time.monotonic()
            room.expire(now + 7)
            self.assertEqual(room.host["key"], host)
            room.members[host].seen = now - 9
        self.assertIsNone(self.bob.request("room")["host"])
        self.assertEqual(self.ops("overview")[1]["members"][0]["name"], "listener")
        self.assertFalse(self.alice.request("room")["hosting"])

    def test_hot_threshold_during_host_handoff(self):
        password = manage(self.db, "create", "operator")
        self.ops("login", dict(name="operator", password=password))
        for api, name in ((self.alice, "host"), (self.bob, "listener")):
            api.request("register", dict(name=name, password="local-test-password"))
        room = self.alice.request("stage/request", dict(self.choice, sync_request_ms=750))
        stream = room["host"]["stream"]
        self.assertEqual(room["sync_policy"]["suggested_ms"], 1000)
        self.assertEqual(self.bob.request("room")["sync_policy"]["effective_ms"], 750)
        with self.assertRaisesRegex(OSError, "403"):
            self.bob.request("stage/sync", {"stream": stream, "sync_request_ms": 900})
        self.alice.request("stage/sync", {"stream": stream, "sync_request_ms": 1650})
        hot = {"room_id": room["room_id"], "version": room["config"]["version"],
               "room_overrides": {"sync_max_ms": 1300, "sync_default_ms": 1100}, "history_s": 3}
        status, result = self.ops("service/configure", hot)
        self.assertEqual(status, 200)
        version = result["service"]["active"]["version"]
        saved = self.ops("service/configure", dict(hot, version=version))[1]
        self.assertEqual(saved["service"]["active"]["version"], version)
        self.assertEqual(self.ops("service/configure", dict(hot, room_id="another"))[0], 409)
        self.assertEqual(self.ops("service/configure", dict(hot, room_id="another", version=version))[0], 404)
        self.assertEqual(self.ops("service/configure", hot)[0], 409)
        self.assertEqual(self.bob.request("room")["sync_policy"]["effective_ms"], 1300)
        self.assertEqual(self.alice.request("room")["host"]["stream"], stream)
        restored = App(self.db).room
        self.assertEqual(restored.sync_policy()["effective_ms"], 1100)
        self.assertEqual(restored.sync_policy(reference_ms=750)["suggested_ms"], 1000)
        self.assertEqual(restored.sync_policy(reference_ms=1450)["suggested_ms"], 1450)
        self.assertEqual(restored.sync_policy(reference_ms=1450)["effective_ms"], 1100)
        self.bob.request("stage/request", self.choice)
        request = self.alice.request("room")["requests"][0]
        self.alice.request("stage/accept", {"stream": stream, "id": request["id"]})
        self.assertEqual(self.bob.request("room")["sync_policy"]["effective_ms"], 1100)
        with self.assertRaisesRegex(OSError, "409"):
            self.alice.request("stage/sync", {"stream": stream, "sync_request_ms": 0})

    def test_shorter_lease_gives_online_host_a_heartbeat(self):
        password = manage(self.db, "create", "operator")
        self.ops("login", dict(name="operator", password=password))
        self.alice.request("register", dict(name="host", password="local-test-password"))
        stream = self.alice.request("stage/request", self.choice)["host"]["stream"]
        room = self.app.room
        with room.lock:
            room.members[room.host["key"]].seen = time.monotonic() - 12
        self.assertEqual(self.ops("service/configure", {"lease_s": 5})[0], 200)
        self.assertEqual(self.alice.request("room")["host"]["stream"], stream)

    def test_history_payload_and_eviction(self):
        history = History(12, byte_limit=1000, block_limit=2)
        history.append(bytes(400), 1000, 2, wire=bytes(60))
        history.append(bytes(400), 1000, 2, wire=bytes(200))
        self.assertEqual((history.payload_bytes, history.last_eviction), (600, "bytes"))
        history.append(bytes(80), 1000, 2)
        history.append(bytes(80), 1000, 2)
        self.assertEqual((history.payload_bytes, history.last_eviction, history.available_ms(1000)),
                         (160, "blocks", 40))
        history.clear()
        self.assertEqual(history.payload_bytes, 0)

    def test_legacy_timeout_default(self):
        active = dict(history_s=7, intervals=[5, 15], logging="errors")
        pending = dict(active, history_s=3)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE service SET active=?, pending=?", (json.dumps(active), json.dumps(pending)))
        restored = App(self.db)
        self.assertEqual({k: restored.control.active[k] for k in active}, active)
        self.assertEqual({k: restored.control.pending[k] for k in pending}, pending)
        self.assertEqual((restored.room.lease_s, restored.room.history.seconds), (20, 7))
        self.assertTrue(restored.room.policy["recognition_fuzzy"])
        self.assertEqual(restored.room.policy["recognition_title_min"], 0.90)
        self.assertEqual(restored.room.policy["recognition_artist_min"], 0.85)

    def test_fuzzy_policy_validation_and_persistence(self):
        secret = manage(self.db, "create", "operator")
        self.ops("login", dict(name="operator", password=secret))
        for invalid in (dict(recognition_fuzzy=1), dict(recognition_title_min=0),
                        dict(recognition_artist_min=True), dict(recognition_artist_min=1.01)):
            self.assertEqual(self.ops("service/configure", invalid)[0], 400)
        values = dict(recognition_fuzzy=False, recognition_title_min=0.95, recognition_artist_min=0.89)
        self.assertEqual(self.ops("service/configure", values)[0], 200)
        for policy in (self.app.room.policy, App(self.db).control.active):
            self.assertEqual({k:policy[k] for k in values}, values)

    def test_waiting_member_expires_without_room_requests(self):
        self.alice.request("register", dict(name="listener", password="local-test-password"))
        self.alice.request("room")
        room = self.app.room
        expired = threading.Event()
        leave = room.leave

        def observe_leave(key, reason):
            leave(key, reason)
            if reason == "连接超时":
                expired.set()

        with patch.object(room, "leave", observe_leave):
            with room.lock:
                self.assertIsNone(room.host)
                next(iter(room.members.values())).seen -= room.lease_s + 1
            self.assertTrue(expired.wait(5), "空席房间应主动释放超时成员")
        self.assertEqual(self.alice.request("me")["name"], "listener")


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
