"""Qt Quick desktop entry point."""
from pathlib import Path
import os
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtQuickControls2 import QQuickStyle

from .disc import Disc
from .room_view import RoomView
from .window_frame import WindowFrame


def player_font_family():
    directory = Path(__file__).with_name("fonts")
    for weight in ("Regular", "Bold"):
        QFontDatabase.addApplicationFont(str(directory / f"NotoSansCJKsc-{weight}.otf"))
    installed = set(QFontDatabase.families())
    return next((f for f in ("Noto Sans CJK SC", "Noto Sans SC", "Microsoft YaHei UI", "PingFang SC")
                 if f in installed), QGuiApplication.font().family())


def build_window(model):
    # The disc paints in Python. Keep Qt Quick painting on the GUI thread so
    # synchronous native resize/move events cannot wait on a painter needing the GIL.
    os.environ.setdefault("QSG_RENDER_LOOP", "basic")
    qmlRegisterType(Disc,"Audimate",1,0,"SpectrumDisc")
    engine = QQmlApplicationEngine()
    font = QFont(player_font_family())
    font.setPixelSize(16)
    QGuiApplication.setFont(font)
    engine.rootContext().setContextProperty("uiFont", font)
    engine.window_frame = WindowFrame(engine)
    engine.rootContext().setContextProperty("frameControl", engine.window_frame)
    engine.rootContext().setContextProperty("room",model)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml" / "Main.qml")))
    if not engine.rootObjects():
        raise RuntimeError("无法加载播放界面")
    window = engine.rootObjects()[0]
    model.disc = window.findChild(Disc,"record")
    engine.window_frame.attach(window)
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
