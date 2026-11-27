"""Opt-in live Shazam/Windows/Chrome acceptance with the upstream Gloria example as stereo WAV."""
from contextlib import ExitStack
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server

from host.capture.test_isolation import start_process
from server.app import App
from server.dev import ThreadedServer
from server.test_app import Quiet
from .__main__ import DEFAULT_DLL
from .online import OnlineRoom


@unittest.skipUnless(os.getenv("AUDIMATE_RECOGNITION_WAV"), "requires the public music fixture and a live Shazam connection")
class LiveRecognition(unittest.TestCase):
    def test_desktop_quick_and_browser_double_check(self):
        wav = Path(os.environ["AUDIMATE_RECOGNITION_WAV"]).resolve(strict=True)
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            path = Path(tmp)
            use_rtc = os.getenv("AUDIMATE_RECOGNITION_RTC") == "1"
            stack.enter_context(patch.dict(os.environ, {"LOCALAPPDATA": tmp, **({} if use_rtc else {"LIVEKIT_URL": ""})}))
            for name in ("audimate.desktop", "audimate.desktop.dev"):
                stack.enter_context(patch.object(logging.getLogger(name), "handlers", [logging.NullHandler()]))
            app = App(path/"room.sqlite", development=True)
            http = make_server("127.0.0.1", 0, app, ThreadedServer, Quiet)
            stack.callback(http.server_close)
            threading.Thread(target=http.serve_forever, daemon=True).start()
            stack.callback(http.shutdown)
            url = f"http://127.0.0.1:{http.server_port}"
            player = start_process(stack, "--window-player", wav)
            pid = int(player.stdout.readline())
            player.stdin.write(json.dumps("《I Will Survive》 - Gloria Gaynor")+"\n")
            player.stdin.flush()
            root = tk.Tk()
            root.withdraw()
            ui = OnlineRoom(root, DEFAULT_DLL, development=True)
            stack.callback(ui.close)
            peer = subprocess.Popen(["node", "server/test_recognition_browser.cjs", url, str(wav), tmp])
            def stop_peer():
                if peer.poll() is None:
                    peer.terminate()
                peer.wait(timeout=10)
            stack.callback(stop_peer)
            def until(check, seconds=75):
                end = time.monotonic()+seconds
                while not check():
                    root.update()
                    if peer.poll() not in (None, 0) or time.monotonic()>end:
                        events = [e["message"] for e in app.room.monitor.events if e["kind"] == "识别"]
                        self.fail(f"{ui.status.get()} · {events}")
                    time.sleep(.015)
            ui.addr.set(url)
            ui.user.set("desktop_host")
            ui.password.set("isolated-room-password")
            ui.auth("register")
            until(lambda: ui.state)
            ui.stage.invoke()
            picker = ui.picker
            picker.tree.selection_set(next(r for r,s in picker.rows.items() if s.pid==pid))
            root.update()
            picker.codec.set("Opus 128 kbps" if use_rtc else "无损 PCM")
            picker.recognize_check.invoke()
            picker.action.invoke()
            until(lambda: ui.stream and ui.cover.photo and "Love Tracks" in ui.song_hint.cget("text"))
            until(lambda: (path/"browser-received").exists())
            self.assertTrue(any("Quick Fetch · 命中" in e["message"] for e in app.room.monitor.events))
            ui.toggle_stage()
            until(lambda: not ui.stream)
            until(lambda: ui.state["host"] and ui.state["host"]["name"]=="web_peer")
            ui.toggle_listen()
            until(lambda: "即将到来" in ui.song_hint.cget("text"))
            until(lambda: ui.listener and ui.cover.photo and "Love Tracks" in ui.song_hint.cget("text")
                  and "I Will Survive" in ui.info.cget("text"))
            (path/"desktop-received").touch()
            until(lambda: peer.poll() is not None)
            self.assertEqual(peer.returncode, 0)
            until(lambda: ui.state["host"] is None and not ui.cover.photo)
