"""Qt Quick desktop entry point."""
from pathlib import Path
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtQuickControls2 import QQuickStyle

from .disc import Disc
from .room_view import RoomView


def build_window(model):
    qmlRegisterType(Disc,"Audimate",1,0,"SpectrumDisc")
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("room",model)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "Main.qml")))
    if not engine.rootObjects():
        raise RuntimeError("无法加载播放界面")
    window = engine.rootObjects()[0]
    model.disc = window.findChild(Disc,"record")
    return engine,window


def launch(dll, development, demo):
    QQuickStyle.setStyle("Material")
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("Audimate")
    model = RoomView(dll,development)
    engine,window = build_window(model)
    app.aboutToQuit.connect(model.close)
    if demo:
        model.auditionRoom()
    result = app.exec()
    del engine
    return result
