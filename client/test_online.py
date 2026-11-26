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
from wsgiref.simple_server import make_server

from host.capture.test_isolation import expect, start_process, write_tone
from server.app import App
from server.dev import ThreadedServer
from server.test_app import Quiet
from .__main__ import Client, DEFAULT_DLL
from .network import API


class OnlineTest(unittest.TestCase):
    def test_desktop_register_login_capture_and_receive(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            path = Path(tmp)
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
            source = start_process(stack, "--player", wav)
            expect(source, "ready")
            root = tk.Tk()
            root.withdraw()
            client = Client(root, DEFAULT_DLL)
            stack.callback(client.close)
            client.open_online()
            ui = client.online
            ui.window.withdraw()
            def until(predicate, timeout=12):
                end = time.monotonic() + timeout
                while not predicate():
                    root.update()
                    if time.monotonic() > end:
                        self.fail(ui.status.get())
                    time.sleep(.01)
            client.tree.selection_set(next(row for row, src in client.rows.items() if src.pid == source.pid))
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
            ui.toggle_stage()
            until(lambda: ui.stream)
            peer = API(url)
            peer_name = "peerQA_" + suffix
            peer.request("register", {"name":peer_name, "password":password})
            stack.callback(peer.request, "logout", {})
            received = []
            def read():
                packet = peer.request("audio?stream=" + ui.stream)
                if packet:
                    received.append(packet[1])
                return bool(received)
            until(read)
            from array import array
            samples = array("h", received[-1])
            self.assertEqual(len(samples), 48000 * 2 // 5)
            self.assertGreater(max(map(abs,samples[1::2])), 50)
            self.assertLess(max(map(abs,samples[::2])), 8)
            if os.environ.get("AUDIMATE_BROWSER_PEER") == "1":
                env = dict(os.environ, AUDIMATE_TEST_URL=url, AUDIMATE_PEER_NAME=peer_name, AUDIMATE_PEER_PASSWORD=password)
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

            import winsound
            from unittest.mock import patch
            peer_host = peer.request("stage/request", dict(source="测试音源", rate=48000, bits=16))["host"]
            peer.request("audio?stream=" + peer_host["stream"], received[-1])
            until(lambda: ui.state["host"] and ui.state["host"]["stream"] == peer_host["stream"])
            with patch("winsound.PlaySound", wraps=winsound.PlaySound) as playback:
                ui.toggle_listen()
                until(lambda: playback.called)
                wav_data = playback.call_args.args[0]
                self.assertTrue(wav_data.startswith(b"RIFF"))
                self.assertTrue(wav_data.endswith(received[-1]))
                ui.toggle_listen()
            peer.request("stage/leave", {"stream": peer_host["stream"]})
