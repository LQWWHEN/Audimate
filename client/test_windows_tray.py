"""Run with the Windows desktop shell to exercise the actual notification icon."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QCoreApplication, QEvent, Qt
from PySide6.QtQml import QQmlExpression, qmlContext
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import local_settings
from .__main__ import DEFAULT_DLL
from .desktop import build_window
from .room_view import RoomView


@unittest.skipUnless(sys.platform == "win32", "requires Windows")
class TrayTests(unittest.TestCase):
    def test_close_remember_restore_and_change_preference(self):
        QQuickStyle.setStyle("Material")
        app = QApplication.instance() or QApplication([])
        if not isinstance(app, QApplication) or not QSystemTrayIcon.isSystemTrayAvailable():
            self.skipTest("requires QApplication and a desktop notification area")
        app.setQuitOnLastWindowClosed(False)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, LOCALAPPDATA=tmp):
            room = RoomView(DEFAULT_DLL)
            engine, win = build_window(room)
            tray = engine.tray
            sheet = win.findChild(QObject, "closeSheet")
            def run(item, expression):
                action = QQmlExpression(qmlContext(item), item, expression)
                action.evaluate()
                self.assertFalse(action.hasError(), str(action.error()))
                QTest.qWait(100)
            try:
                run(win, "toggleDefaults()")
                win.close()
                QTest.qWait(100)
                self.assertTrue(sheet.property("visible"))
                QTest.keyClick(win, Qt.Key_Escape)
                QTest.qWait(350)
                self.assertTrue(win.isVisible())
                self.assertFalse(sheet.property("visible"))
                self.assertTrue(win.property("editingDefaults"))
                run(win, "toggleDefaults()")
                win.showMaximized()
                win.close()
                win.findChild(QObject, "rememberClose").setProperty("checked", True)
                run(sheet, 'choose("tray")')
                self.assertFalse(win.isVisible())
                self.assertTrue(tray.icon.isVisible())
                self.assertEqual(local_settings.load(room.pref_file)["closeAction"], "tray")
                tray.icon.activated.emit(QSystemTrayIcon.Trigger)
                QTest.qWait(100)
                self.assertEqual(win.windowState(), Qt.WindowMaximized)
                self.assertFalse(tray.icon.isVisible())
                win.close()
                QTest.qWait(100)
                self.assertFalse(win.isVisible())
                tray.menu.actions()[0].trigger()
                run(win, "toggleDefaults()")
                win.findChild(QObject, "closeAction").setProperty("currentIndex", 0)
                run(win.findChild(QObject, "localSettings"), "save()")
                self.assertEqual(local_settings.load(room.pref_file)["closeAction"], "ask")
                with patch.object(tray, "available", return_value=False):
                    win.close()
                    QTest.qWait(50)
                    self.assertTrue(sheet.property("visible"))
                    self.assertFalse(sheet.property("trayAvailable"))
                win.findChild(QObject, "rememberClose").setProperty("checked", True)
                run(sheet, 'choose("exit")')
                self.assertTrue(tray.exiting())
                self.assertEqual(local_settings.load(room.pref_file)["closeAction"], "exit")
            finally:
                tray.icon.hide()
                room.close()
                engine.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
