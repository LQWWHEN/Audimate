"""Qt player regression tests, including a real Windows process-to-device trip."""
from contextlib import ExitStack
from pathlib import Path
import os
import logging
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server

import numpy as np
from PySide6.QtCore import QObject, QMetaObject, QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlExpression, qmlContext
from PySide6.QtQuickControls2 import QQuickStyle

from host.capture.test_isolation import start_process, write_tone
from server.app import App
from server.dev import ThreadedServer
from server.test_app import Quiet
from .desktop import build_window
from .disc import Disc
from .room_view import RoomView
from . import local_settings
from .playback import Output
from .spectrum import Spectrum
from .__main__ import DEFAULT_DLL


class AnalysisTests(unittest.TestCase):
    def test_live_calibration_preserves_samples_across_delay_changes(self):
        for rate,bits in ((48000,16),(44100,24),(96000,32)):
            with self.subTest(rate=rate,bits=bits), patch("sounddevice.RawOutputStream") as factory:
                device = factory.return_value
                device.write.return_value = False
                observed = []
                with Output(rate,bits) as output:
                    output.observe = observed.append
                    chunk = bytes((i % 253 + 1 for i in range(output.frame_bytes)))
                    output.playback_delay = lambda: 50
                    output.write(chunk*4)
                    out = b"".join(c.args[0] for c in device.write.call_args_list)
                    silence = bytes(round(rate*.05)*2*(bits//8))
                    self.assertEqual(out, (silence+chunk*4)[:len(chunk)*4])
                    output.playback_delay = lambda: 0
                    output.write(chunk)
                    self.assertEqual(b"".join(c.args[0] for c in device.write.call_args_list), silence+chunk*5)
                    self.assertEqual(b"".join(observed),chunk*5)

    def test_settings_migrate_validate_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"desktop.json"
            path.with_name("listening.json").write_text('{"interval":10,"auto":false}',encoding="utf8")
            prefs = local_settings.load(path)
            self.assertEqual((prefs["delay"],prefs["interval"],prefs["auto"]),(50,10,False))
            with self.assertRaises(ValueError):
                local_settings.validate(dict(prefs,rate=44133))
            prefs = local_settings.validate(dict(prefs,rate=88200.,bits=24,delay=0))
            local_settings.save(path,prefs)
            self.assertEqual(local_settings.load(path),prefs)

    def test_stereo_opposition_and_supported_formats(self):
        for rate, bits in ((8000,16), (44100,24), (48000,16), (96000,32), (192000,24)):
            analyzer = Spectrum(rate,bits)
            t = np.arange(rate//5)/rate
            for hz in (80,3000):
                mono = .36*np.sin(2*np.pi*t*hz)
                stereo = (np.column_stack((mono,-mono))*(2**(bits-1))).astype("<i4")
                chunk = stereo.view("u1").reshape(-1,4)[:,:bits//8].copy().tobytes()
                analyzer.analyze(chunk)
                self.assertGreater(analyzer.snapshot[1].max(),.7)
                peak = analyzer.snapshot[1].argmax()
                self.assertTrue(peak < 16 if hz==80 else peak > 25)
            analyzer.analyze(bytes(len(chunk)))
            self.assertEqual(analyzer.snapshot[1].max(),0)


class RoomTests(unittest.TestCase):
    def test_track_refresh_fits_text_without_moving_the_player(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QQuickStyle.setStyle("Material")
        app = QGuiApplication.instance() or QGuiApplication([])
        model = RoomView(DEFAULT_DLL)
        model.clock.stop()
        model.user, model.want = "Léa", False
        host = dict(name="夜航电台", stream="a", recognize=True, rate=48000, bits=16)
        engine, window = build_window(model)
        try:
            frames = [window.findChild(QObject, name) for name in
                      ("record", "hostRail", "audioRail", "presetButton", "songInfo",
                       "songTitle", "songArtist", "songAlbum")]
            title, artist, album = frames[-3:]
            font_size = QQmlExpression(qmlContext(title), title, "fontInfo.pixelSize")
            short = dict(title="潮汐之间", artist="Léa", album="海岸线习作")
            long = dict(title="海岸线上的月光与潮汐"*5 + "（東京 · Live Session）",
                        artist="김민서 / Léa " * 16, album="現場錄音 " * 30)
            extreme = dict(long, title="MidnightSessions" * 25)
            pending = dict(candidate=dict(title=long["title"], artist="林澈"), notice="正在核对识别结果")
            for portrait, width, height in ((False,1140,760), (False,960,560), (True,420,600), (True,480,740)):
                model.apply(dict(host=host))
                window.setProperty("portrait", portrait)
                QTest.qWait(40)
                window.resize(width, height)
                QTest.qWait(40)
                reference = None
                for info in ({}, pending, dict(current=short), dict(current=long),
                             dict(current=extreme), dict(current=short), {}):
                    track = info.get("current", {})
                    with self.subTest(size=(width,height), title=track.get("title","等待")):
                        model.apply(dict(host=dict(host, recognition=info)))
                        QTest.qWait(35)
                        self.assertEqual((window.width(), window.height()), (width, height))
                        geometry = [(i.x(), i.y(), i.width(), i.height()) for i in frames]
                        if reference is None:
                            reference = geometry, window.minimumHeight()
                        self.assertEqual((geometry, window.minimumHeight()), reference)
                        self.assertEqual(title.property("text"), track.get("title","正在识别"))
                        self.assertEqual(artist.property("text"), track.get("artist",""))
                        self.assertEqual(album.property("text"), track.get("album",""))
                        self.assertLessEqual(title.property("lineCount"), 2)
                        for line in (title, artist, album):
                            self.assertLessEqual(line.property("contentHeight"), line.height()+1)
                            self.assertLessEqual(line.property("contentWidth"), line.width()+1)
                        size, error = font_size.evaluate()
                        self.assertFalse(error)
                        if track is extreme:
                            self.assertEqual(size, title.property("minimumPixelSize"))
                            self.assertTrue(title.property("truncated"))
                            full_name = QQmlExpression(qmlContext(title), title, "ToolTip.text")
                            self.assertEqual(full_name.evaluate(), (extreme["title"], False))
                        elif track is long:
                            self.assertLess(size, 22)
                            self.assertFalse(title.property("truncated"))
                            self.assertTrue(artist.property("truncated") and album.property("truncated"))
                        else:
                            self.assertEqual(size, 28 if portrait else 38)
                            self.assertFalse(title.property("truncated"))
        finally:
            model.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_metadata_clearance_with_both_rails_populated(self):
        os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
        QQuickStyle.setStyle("Material")
        app = QGuiApplication.instance() or QGuiApplication([])
        model = RoomView(DEFAULT_DLL)
        model.clock.stop()
        engine, window = build_window(model)
        try:
            model.view_data.update(entered=True, host="東京放送局", title="紅葉の夜 · L’Été / 봄날",
                                   artist="김민서", album="现场錄音", candidate="東京の夜 · 봄날",
                                   recognitionNotice="正在核对识别结果", shared="FLAC   192 kHz / 24 bit",
                                   received="主持本机采集", delay=1500, requests=[dict(name="听众甲", id="x")])
            model.changed.emit()
            host = window.findChild(QObject,"hostRail")
            audio = window.findChild(QObject,"audioRail")
            song = window.findChild(QObject,"songInfo")
            for portrait in (False, True):
                with self.subTest(portrait=portrait):
                    window.setProperty("portrait",portrait)
                    window.resize(window.minimumWidth(),window.minimumHeight())
                    QTest.qWait(80)
                    self.assertEqual(host.y(),audio.y())
                    self.assertAlmostEqual(host.x()+host.width()+audio.x(),window.width())
                    self.assertEqual(host.childItems()[1].y(),audio.childItems()[1].y())
                    if portrait:
                        self.assertLessEqual(song.y()+song.height(),host.y())
                    else:
                        self.assertLess(host.x()+host.width(),song.x())
                        self.assertLess(song.x()+song.width(),audio.x())
                    for rail in (host,audio):
                        self.assertLessEqual(rail.y()+rail.height(),rail.parentItem().height()+1)
        finally:
            model.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_qml_capture_handoff_and_local_override(self):
        os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
        QQuickStyle.setStyle("Material")
        app = QGuiApplication.instance() or QGuiApplication([])
        with ExitStack() as cleanup:
            root = Path(cleanup.enter_context(tempfile.TemporaryDirectory()))
            cleanup.enter_context(patch.dict(os.environ, {"LOCALAPPDATA":str(root)}))
            cleanup.enter_context(patch.object(logging.getLogger("audimate.desktop"), "handlers", [logging.NullHandler()]))
            service = App(root/"accounts.db")
            http = make_server("127.0.0.1",0,service,ThreadedServer,Quiet)
            cleanup.callback(http.server_close)
            threading.Thread(target=http.serve_forever,daemon=True).start()
            cleanup.callback(http.shutdown)
            url = f"http://127.0.0.1:{http.server_port}"
            wav = root/"player.wav"
            write_tone(wav,0,900)
            proc = start_process(cleanup,"--player",wav)
            self.assertEqual(proc.stdout.readline().strip(),"ready")

            a,b = RoomView(DEFAULT_DLL),RoomView(DEFAULT_DLL)
            codec = os.environ.get("AUDIMATE_PLAYER_TEST_CODEC", "PCM")
            if codec in ("PCM", "FLAC"):
                a.receive = b.receive = "无损 PCM"
            engine,window = build_window(a)
            b.disc = Disc()
            cleanup.callback(b.close)
            cleanup.callback(a.close)
            def wait(predicate, timeout=12):
                end = time.monotonic()+timeout
                while not predicate():
                    app.processEvents()
                    if time.monotonic()>end:
                        self.fail(f"room state: {a.view}, {b.view}")
                    time.sleep(.008)
            for model,name in ((a,"主持甲"),(b,"主持乙")):
                model.login(url,name,"test-player-password",True)
            wait(lambda: a.state and b.state)
            self.assertTrue(a.want and b.want)
            self.assertEqual(a.saveDefaults(dict(a.localDefaults,codec=codec,style="heavy",receive=a.receive)), "")
            sheet = window.findChild(QObject,"sourceSheet")
            self.assertTrue(QMetaObject.invokeMethod(sheet,"open"))
            wait(lambda: a.items)
            b.listSources()
            wait(lambda: b.items)
            for model,style in ((a,"heavy"),(b,"pop")):
                key = next(row["key"] for row in model.items if row["pid"]==proc.pid)
                if model is a:
                    window.findChild(QObject,"sourceSearch").setProperty("text",str(proc.pid))
                    def share_button():
                        nodes = [window.contentItem()]
                        while nodes:
                            node = nodes.pop()
                            if node.objectName() == "share-"+key:
                                return node
                            nodes.extend(node.childItems())
                    wait(share_button)
                    self.assertTrue(QMetaObject.invokeMethod(share_button(),"click"))
                    wait(lambda: b.view["playing"] and b.view["style"]=="heavy")
                    wait(lambda: b.analyzer.snapshot[1].max()>.3)
                    self.assertTrue(a.capture and a.view["mine"])
                    self.assertIn(codec.split()[0], b.view["received"])
                    self.assertFalse(sheet.property("opened"))
                    # Exercise the same snapshot renderer used by /room while the
                    # real PCM subscription remains active.
                    song = dict(title="曲目甲", artist="歌手甲", album="专辑甲")
                    candidate = dict(title="曲目乙", artist="歌手乙")
                    old_state = b.state
                    host = dict(old_state["host"], recognition=dict(current=song))
                    b.apply(dict(old_state, host=host))
                    host = dict(host, recognition=dict(candidate=candidate))
                    b.apply(dict(old_state, host=host))
                    self.assertEqual((b.view["title"], b.view["album"]), ("曲目甲", "专辑甲"))
                    self.assertEqual(b.view["candidate"], "曲目乙 — 歌手乙")
                    b.apply(old_state)
                    b.preset("light")
                    menu = window.findChild(QObject, "presets")
                    QMetaObject.invokeMethod(window.findChild(QObject, "presetButton"), "click")
                    wait(lambda: menu.property("opened"))
                    self.assertFalse(menu.property("modal"))
                    items = [window.contentItem()]
                    while items:
                        item = items.pop()
                        if item.objectName() == "preset-pop":
                            break
                        items.extend(item.childItems())
                    self.assertEqual(item.objectName(), "preset-pop")
                    point = item.mapToScene(item.boundingRect().center()).toPoint()
                    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
                    wait(lambda: b.state["host"]["visual_style"]=="pop")
                    self.assertEqual(b.view["style"],"light")
                else:
                    model.requestStage(key,48000,16,codec,False,"",style)
            wait(lambda: a.view["requests"] and b.view["pending"])
            self.assertTrue(b.listener and not b.capture)
            a.decide("accept",a.view["requests"][0]["id"])
            wait(lambda: b.capture and a.listener and a.view["playing"])
            self.assertEqual(b.view["style"],"pop")
            b.leaveStage("")
            wait(lambda: not a.state.get("host"))
            self.assertEqual(b.view["style"],"light")
            b.exitRoom()
            wait(lambda: not b.user)
            self.assertEqual(b.override,"")
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
