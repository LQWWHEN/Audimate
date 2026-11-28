"""Windows notification area controls for the Qt player."""
from pathlib import Path

from PySide6.QtCore import QObject, QCoreApplication, Qt, Slot
from PySide6.QtGui import QGuiApplication, QIcon, QWindow
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class WindowsTray(QObject):
    def __init__(self, frame):
        super().__init__(frame)
        self.frame = frame
        self.quitting = False
        self.icon = QSystemTrayIcon(QIcon(str(Path(__file__).with_name("audimate.ico"))), self)
        self.icon.setToolTip("Audimate")
        self.menu = QMenu()
        self.menu.addAction("显示主窗口", self.restore)
        self.menu.addAction("退出应用", self.quitApplication)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self.activated)
        QCoreApplication.instance().aboutToQuit.connect(self.icon.hide)

    @Slot(result=bool)
    def exiting(self):
        return self.quitting or QGuiApplication.instance().isSavingSession()

    @Slot(result=bool)
    def available(self):
        return self.icon.isSystemTrayAvailable() and not self.icon.icon().isNull()

    @Slot(result=bool)
    def hideWindow(self):
        if not self.available():
            return False
        self.icon.show()
        self.frame.window.hide()
        return True

    def activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore()

    @Slot()
    def restore(self):
        window = self.frame.window
        self.frame.animate(False)
        if window.windowStates() & Qt.WindowMaximized:
            window.showMaximized()
        elif window.visibility() in (QWindow.Hidden, QWindow.Minimized):
            window.showNormal()
        window.raise_()
        window.requestActivate()
        self.icon.hide()

    @Slot()
    def quitApplication(self):
        self.quitting = True
        QCoreApplication.quit()
