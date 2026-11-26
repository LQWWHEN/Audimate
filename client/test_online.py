import json
import logging
import os
import secrets
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import make_server

from host.capture.test_isolation import start_process, write_tone
from server.app import App
from server.dev import ThreadedServer
from server.test_app import Quiet
from .__main__ import DEFAULT_DLL
from .online import OnlineRoom
from .network import API


class OnlineTest(unittest.TestCase):
    def test_desktop_register_login_capture_and_receive(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            path = Path(tmp)
            stack.enter_context(patch.dict(os.environ, {"LOCALAPPDATA": str(path / "appdata")}))
            stack.enter_context(patch.object(logging.getLogger("audimate.desktop"), "handlers", [logging.NullHandler()]))
            url = os.environ.get("AUDIMATE_TEST_URL")
            if not url:
                http = make_server("127.0.0.1", 0, App(path/"room.db"), ThreadedServer, Quiet)
                stack.callback(http.server_close)
                threading.Thread(target=http.serve_forever, daemon=True).start()
                stack.callback(http.shutdown)
                url = f"http://127.0.0.1:{http.server_port}"
            suffix = secrets.token_hex(6)
            password = secrets.token_urlsafe(24)
            wav = path/"target.wav"
            write_tone(wav, 0, 900)
            source = start_process(stack, "--window-player", wav)
            player_pid = int(source.stdout.readline())
            def caption(text):
                source.stdin.write(json.dumps(text) + "\n")
                source.stdin.flush()
            root = tk.Tk()
            root.withdraw()
            ui = OnlineRoom(root, DEFAULT_DLL)
            stack.callback(ui.close)
            self.assertIsNone(ui.picker)
            self.assertIs(ui.window, root)
            def until(predicate, timeout=12):
                end = time.monotonic() + timeout
                while not predicate():
                    root.update()
                    if time.monotonic() > end:
                        self.fail(ui.status.get())
                    time.sleep(.01)
            ui.addr.set(url)
            ui.user.set("desktopQA_" + suffix)
            ui.password.set(password)
            ui.auth("register")
            until(lambda: ui.state is not None)
            ui.logout()
            until(lambda: not ui.name)
            ui.password.set(password)
            ui.auth("login")
            until(lambda: ui.state is not None)
            self.assertIsNone(ui.state["host"], "测试需要空闲房间")
            self.assertIsNone(ui.picker)
            ui.listen.invoke()
            self.assertEqual(ui.listen.cget("text"), "取消等候")
            ui.listen.invoke()
            ui.settings_button.invoke()
            self.assertEqual(ui.prefs.winfo_manager(), "grid")
            ui.settings_button.invoke()
            ui.stage.invoke()
            picker = ui.picker
            self.assertFalse(ui.frame.winfo_manager())
            self.assertTrue(picker.action.instate(["disabled"]))
            picker.tree.selection_set(next(row for row, src in picker.rows.items() if src.pid == player_pid))
            root.update()
            ui.show_room()
            self.assertIsNone(ui.api.request("room")["host"])
            ui.stage.invoke()
            self.assertEqual(picker.selected().pid, player_pid)
            picker.action.invoke()
            until(lambda: ui.stream)
            self.assertFalse(picker.winfo_manager())
            peer = API(url)
            peer_name = "peerQA_" + suffix
            peer.request("register", {"name":peer_name, "password":password})
            stack.callback(peer.request, "logout", {})
            peer_source = dict(source="测试音源", rate=48000, bits=16)
            original_stream = ui.stream
            original_capture = ui.capture
            process_name = ui.selection.source.name
            for title, expected in (("《第一首》 - 歌手甲", "第一首 - 歌手甲"),
                                    ("Audimate test player", process_name),
                                    ("《第二首》 - 歌手乙", "第二首 - 歌手乙")):
                caption(title)
                until(lambda: ui.state["host"]["source"] == expected)
                self.assertIn(expected, ui.info.cget("text"))
                self.assertEqual(peer.request("room")["host"]["source"], expected)
                self.assertEqual(ui.stream, original_stream)
                self.assertIs(ui.capture, original_capture)

            def decide_from_room(title):
                peer.request("stage/request", peer_source)
                until(lambda: ui.state["requests"])
                row = ui.requests.winfo_children()[0]
                next(widget for widget in row.winfo_children() if widget.cget("text") == title).invoke()
                until(lambda: not ui.state["requests"])

            decide_from_room("拒绝")
            self.assertFalse(peer.request("room")["pending"])
            from array import array
            received = []
            livekit = ui.state["livekit"]
            def read():
                packet = peer.request("audio?stream=" + ui.stream)
                if packet and max(map(abs,array("h",packet[1])[1::2]), default=0) > 50:
                    received.append(packet[1])
                return bool(received)
            until(read, timeout=30)
            samples = array("h", received[-1])
            self.assertEqual(len(samples), 48000 * 2 // 5)
            self.assertGreater(max(map(abs,samples[1::2])), 50)
            self.assertLess(max(map(abs,samples[::2])), 100 if livekit else 8)
            if os.environ.get("AUDIMATE_BROWSER_PEER") == "1":
                env = dict(os.environ, AUDIMATE_TEST_URL=url, AUDIMATE_PEER_NAME=peer_name,
                           AUDIMATE_PEER_PASSWORD=password, AUDIMATE_PEER_TRACK="第二首 - 歌手乙")
                with subprocess.Popen(["node", "server/test_browser.cjs", "--desktop-listener"], env=env) as browser:
                    try:
                        until(lambda: browser.poll() is not None, timeout=45)
                        self.assertEqual(browser.returncode, 0)
                    finally:
                        if browser.poll() is None:
                            browser.terminate()
            ui.toggle_stage()
            until(lambda: ui.state["host"] is None)
            self.assertIsNone(peer.request("room")["host"])

            from client.playback import Output
            peer_host = peer.request("stage/request", peer_source)["host"]
            peer.request("audio?stream=" + peer_host["stream"], received[-1])
            until(lambda: ui.state["host"] and ui.state["host"]["stream"] == peer_host["stream"])
            peer.request("stage/track", {"stream": peer_host["stream"], "track": {"title": "听众测试", "artist": "歌手甲"}})
            until(lambda: "听众测试 - 歌手甲" in ui.info.cget("text"))
            written = []
            original_write = Output.write
            def recording_write(device, pcm):
                written.append(bytes(pcm))
                return original_write(device, pcm)
            with patch.object(Output, "write", recording_write):
                ui.toggle_listen()
                initial_listener = ui.listener
                last = 0
                def playing():
                    nonlocal last
                    if time.monotonic()-last >= .2:
                        peer.request("audio?stream=" + peer_host["stream"], received[-1])
                        last = time.monotonic()
                    return any(max(map(abs,array("h",pcm)),default=0)>50 for pcm in written)
                until(playing, timeout=30)
                if livekit:
                    from .rtc import RTCWorker
                    self.assertIsInstance(ui.listener, RTCWorker, initial_listener.error)
                else:
                    self.assertEqual(written[0], received[-1])
                ui.toggle_listen()

            def apply_for_stage(custom=False):
                ui.stage.invoke()
                self.assertEqual(picker.action.cget("text"), "提交申请")
                if custom:
                    picker.quality.current(len(picker.quality_keys))
                    picker.change_quality()
                    picker.rate.set("88200")
                    picker.depth.set("24")
                    picker.codec.set("无损 PCM")
                picker.action.invoke()
                until(lambda: ui.pending)
                self.assertIsNone(ui.capture)
                return peer.request("room")["requests"][0]["id"]

            apply_for_stage()
            ui.stage.invoke()
            until(lambda: not ui.pending)
            req = apply_for_stage()
            peer.request("stage/reject", {"stream": peer_host["stream"], "id": req})
            until(lambda: not ui.pending)
            req = apply_for_stage(custom=True)
            peer.request("stage/accept", {"stream": peer_host["stream"], "id": req})
            until(lambda: ui.capture and ui.capture.ready.is_set())
            host = peer.request("room")["host"]
            self.assertEqual((host["rate"], host["bits"], host["codec"]), (88200, 24, "pcm"))
            decide_from_room("接受")
            self.assertTrue(peer.request("room")["hosting"])
            self.assertIsNone(ui.capture)
            peer.request("stage/leave", {"stream": ui.state["host"]["stream"]})
            until(lambda: ui.state["host"] is None)
            ui.logout()
            until(lambda: not ui.name)
            self.assertIsNone(ui.picker)
