"""Geometry and focus rules of the portrait edge strip, in logical pixels."""
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QWindow

from .window_frame import HIDE_DELAY, LIP, WindowFrame, dock_edge, exposed, fit


class EdgeTests(unittest.TestCase):
    def test_snap_and_reveal_use_the_current_monitors_work_area(self):
        area = QRect(-1600, 40, 1600, 860)
        for side, x, y in (("left", -1593, 100), ("right", -425, 120), ("top", -1200, 45)):
            rect = QRect(x, y, 420, 600)
            self.assertEqual(dock_edge(rect, area), side)
            strip = exposed(rect.size(), side, 1)
            if side == "top":
                self.assertEqual(strip, QRect(0, 0, 420, LIP))
            elif side == "left":
                self.assertEqual(strip, QRect(0, 0, LIP, 600))
            else:
                self.assertEqual(strip, QRect(420-LIP, 0, LIP, 600))
        self.assertEqual(dock_edge(QRect(-1200, 300, 420, 600), area), "")
        self.assertEqual(fit(QRect(-1670, 120, 420, 600), area).left(), area.left())

    @patch("client.window_frame.QGuiApplication.mouseButtons", return_value=Qt.NoButton)
    @patch("client.window_frame.QCursor.pos")
    @patch("client.window_frame.time.monotonic")
    def test_hide_only_after_idle_then_reveal_on_strip_without_activation(self, clock, cursor, _buttons):
        frame = WindowFrame(None)
        w = frame.window = Mock()
        w.property.side_effect = lambda key: {"portrait": True, "popupOpen": False}.get(key)
        w.visibility.return_value = QWindow.Windowed
        w.screen.return_value.availableGeometry.return_value = QRect(0,0,1600,900)
        rect = QRect(0,140,420,600)
        w.geometry.return_value = rect
        w.size.return_value = rect.size()
        frame.edge = "left"
        frame.animate = Mock()
        w.isActive.return_value = False
        cursor.return_value = QPoint(1200,480)
        clock.return_value = 10
        frame.poll()
        frame.animate.assert_not_called()
        clock.return_value += HIDE_DELAY+.01
        frame.poll()
        frame.animate.assert_called_with(True)
        frame.fold = 1.
        cursor.return_value = QPoint(7,500)
        frame.poll()
        frame.animate.assert_called_with(False)
        w.requestActivate.assert_not_called()
        frame.edge = ""
        frame.fold = 0.
        frame.animate.reset_mock()
        w.geometry.return_value = QRect(200,140,420,600)
        frame.poll()
        frame.animate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
