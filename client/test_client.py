"""Run from the project root with python -m unittest client.test_client -v."""
from contextlib import ExitStack, nullcontext
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from host.capture.record import AudioFormat, Capture, QUALITY_PRESETS
from host.capture.test_isolation import expect, start_process, write_tone
from .__main__ import Client, DEFAULT_DLL
from .processes import ProcessSource, selected_process


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Client(self.root, DEFAULT_DLL)
        self.root.update()

    def tearDown(self):
        self.app.close()
        if self.app.worker:
            self.until(lambda: self.app.worker is None)

    def until(self, condition, timeout=12):
        deadline = time.monotonic() + timeout
        while not condition():
            self.root.update()
            if time.monotonic() > deadline:
                self.fail(f"Timed out: {self.app.message.get()}")
            time.sleep(0.01)

    def select_pid(self, pid):
        row = next(key for key, src in self.app.rows.items() if src.pid == pid)
        self.app.tree.selection_set(row)
        self.root.update()
        return self.app.selected()

    def quality(self, key):
        index = self.app.quality_keys.index(key) if key in self.app.quality_keys else len(self.app.quality_keys)
        self.app.quality.current(index)
        self.app.quality.event_generate("<<ComboboxSelected>>")
        self.root.update()

    def test_refresh_filter_and_validation(self):
        app = self.app
        one = ProcessSource(10, 100, "Music.exe", "第一首歌")
        two = ProcessSource(20, 200, "Music.exe", "第二首歌")
        with patch("client.__main__.list_sources", return_value=[one, two]) as sources:
            app.refresh_button.invoke()
            self.assertTrue(app.action.instate(["disabled"]))
            self.select_pid(two.pid)
            app.search.set("第二")
            self.assertEqual(list(app.rows.values()), [two])
            app.refresh_button.invoke()
            self.assertEqual(app.selected(), two)
            for key, fmt in QUALITY_PRESETS.items():
                self.quality(key)
                self.assertEqual(app.selected_format(), fmt)
            self.quality("custom")
            app.rate.set("22050")
            self.assertTrue(app.action.instate(["disabled"]))
            app.rate.set("88200")
            app.depth.set("32")
            self.assertTrue(app.action.instate(["!disabled"]))
            self.assertEqual(app.selected_format(), AudioFormat(88200, 32))
            sources.return_value = [one, replace(two, started=201)]
            app.refresh_button.invoke()
            self.assertIsNone(app.selected())
            self.assertTrue(app.action.instate(["disabled"]))
            app.search.set("missing-player")
            self.assertFalse(app.rows)

    def test_real_selection_capture_and_source_exit(self):
        app = self.app
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            stereo, right_only = Path(tmp) / "stereo.wav", Path(tmp) / "right.wav"
            write_tone(stereo, 700, 1200)
            write_tone(right_only, 0, 1800)
            first = start_process(stack, "--player", stereo)
            second = start_process(stack, "--player", right_only)
            for process in (first, second):
                expect(process, "ready")
            app.refresh_button.invoke()
            source = self.select_pid(first.pid)
            with self.assertRaises(OSError), selected_process(replace(source, started=source.started-1)):
                self.fail("A reused PID was accepted")

            with patch("client.sharing.Capture", wraps=Capture) as native:
                self.quality("studio")
                app.action.invoke()
                self.until(lambda: app.role.cget("text") == "本地主持" and min(app.worker.levels) > 0.001)
                native.assert_called_with(DEFAULT_DLL, first.pid, QUALITY_PRESETS["studio"])
                app.action.invoke()
                self.until(lambda: app.worker is None)
                self.assertEqual(app.role.cget("text"), "听众")

                app.search.set(str(second.pid))
                self.select_pid(second.pid)
                self.quality("custom")
                app.rate.set("88200")
                app.depth.set("32")
                app.action.invoke()
                self.until(lambda: app.worker and app.worker.levels[1] > 0.001)
                native.assert_called_with(DEFAULT_DLL, second.pid, AudioFormat(88200, 32))
                left, right = app.worker.levels
                self.assertLess(left, right * 0.02)
                second.stdin.close()
                second.wait(5)
                self.until(lambda: app.worker is None)
                self.assertIsNone(app.selected())
                self.assertIn("0x8007042B", app.message.get())
                self.assertTrue(app.action.instate(["disabled"]))

    def test_cancel_during_start_then_retry_after_error(self):
        app = self.app
        source = ProcessSource(10, 100, "Music.exe", "")
        entered, release = threading.Event(), threading.Event()

        def delayed_open(*_):
            entered.set()
            release.wait(5)
            return nullcontext()

        with patch("client.__main__.list_sources", return_value=[source]), \
             patch("client.sharing.selected_process", return_value=nullcontext()), \
             patch("client.sharing.Capture", side_effect=delayed_open) as native:
            app.refresh_button.invoke()
            self.select_pid(source.pid)
            app.action.invoke()
            self.until(entered.is_set)
            self.assertEqual(app.role.cget("text"), "准备中")
            app.action.invoke()
            self.assertTrue(app.action.instate(["disabled"]))
            release.set()
            self.until(lambda: app.worker is None)
            self.assertEqual(app.role.cget("text"), "听众")
            native.side_effect = OSError("测试音源不可用")
            app.action.invoke()
            self.until(lambda: app.worker is None)
            self.assertIn("测试音源不可用", app.message.get())
            self.assertTrue(app.action.instate(["!disabled"]))
            native.side_effect = None
            native.return_value.__enter__.return_value.read.side_effect = lambda _: time.sleep(0.02)
            app.action.invoke()
            self.until(lambda: app.role.cget("text") == "本地主持")
            app.action.invoke()
            self.until(lambda: app.worker is None)


if __name__ == "__main__":
    unittest.main()
