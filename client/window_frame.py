"""Window placement and edge folding in Qt logical desktop coordinates."""
import time

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer, QVariantAnimation, QEasingCurve, Slot
from PySide6.QtGui import QCursor, QGuiApplication, QRegion, QWindow


SNAP = 12
LIP = 6
HIDE_DELAY = 1.4


def dock_edge(rect, area):
    distances = [(abs(rect.top()-area.top()), "top"),
                 (abs(rect.left()-area.left()), "left"),
                 (abs(rect.right()-area.right()), "right")]
    distance, edge = min(distances, key=lambda item: item[0])
    return edge if distance <= SNAP else ""


def fit(rect, area):
    result = QRect(rect)
    result.moveLeft(max(area.left(), min(result.left(), area.right()-rect.width()+1)))
    result.moveTop(max(area.top(), min(result.top(), area.bottom()-rect.height()+1)))
    return result


def exposed(size, edge, fold):
    rect = QRect(QPoint(), size)
    if edge == "top":
        rect.setHeight(max(LIP, round(size.height() * (1-fold) + LIP*fold)))
    elif edge:
        width = max(LIP, round(size.width() * (1-fold) + LIP*fold))
        rect.setWidth(width)
        if edge == "right":
            rect.moveRight(size.width()-1)
    return rect


class WindowFrame(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.window = None
        self.saved = {}
        self.edge = ""
        self.fold = 0.
        self.hiding = False
        self.idle_since = None
        self.drag_point = None
        self.dragged = False
        self.animation = QVariantAnimation(self)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self.set_fold)
        self.timer = QTimer(self)
        self.timer.setInterval(70)
        self.timer.timeout.connect(self.poll)

    def attach(self, window):
        self.window = window
        screen = window.screen().availableGeometry()
        rect = window.geometry()
        rect.setSize(rect.size().boundedTo(screen.size()).expandedTo(window.minimumSize()))
        window.setGeometry(fit(rect, screen))
        window.activeChanged.connect(self.activity)
        window.visibilityChanged.connect(self.activity)
        window.widthChanged.connect(self.update_mask)
        window.heightChanged.connect(self.update_mask)

    def activity(self, *_):
        if self.window.isActive() or self.window.visibility() != QWindow.Windowed:
            self.idle_since = None
            self.animate(False)

    def set_edge(self, edge):
        if self.edge == edge:
            return
        self.animation.stop()
        self.hiding = False
        self.set_fold(0.)
        self.edge = edge
        self.window.setProperty("dockEdge", edge)
        self.window.setFlag(Qt.WindowStaysOnTopHint, bool(edge))
        self.idle_since = None

    def update_mask(self, *_):
        if self.fold:
            self.window.setMask(QRegion(exposed(self.window.size(), self.edge, self.fold)))

    def set_fold(self, fold):
        self.fold = float(fold)
        self.window.setProperty("fold", self.fold)
        if self.fold:
            self.update_mask()
        else:
            self.window.setMask(QRegion())

    def animate(self, hide):
        if self.hiding == hide:
            return
        self.hiding = hide
        self.animation.stop()
        self.animation.setStartValue(self.fold)
        self.animation.setEndValue(1. if hide else 0.)
        self.animation.setDuration(1 if self.window.property("reduced") else 165)
        self.animation.start()

    def poll(self):
        w = self.window
        if w.visibility() == QWindow.Hidden:
            return
        if not w.property("portrait") or w.visibility() != QWindow.Windowed:
            if self.edge:
                self.set_edge("")
            return
        if self.drag_point is not None or QGuiApplication.mouseButtons() != Qt.NoButton:
            self.idle_since = None
            return
        area = w.screen().availableGeometry()
        rect = w.geometry()
        edge = dock_edge(rect, area)
        self.set_edge(edge)
        if not edge:
            return
        aligned = fit(rect, area)
        if edge == "top":
            aligned.moveTop(area.top())
        elif edge == "left":
            aligned.moveLeft(area.left())
        else:
            aligned.moveRight(area.right())
        if aligned.topLeft() != rect.topLeft():
            w.setPosition(aligned.topLeft())
        pointer = QCursor.pos() - aligned.topLeft()
        hit = exposed(w.size(), edge, self.fold)
        if self.fold:
            hit.adjust(-4, -4, 4, 4)
        if w.isActive() or w.property("popupOpen") or hit.contains(pointer):
            self.idle_since = None
            self.animate(False)
        elif self.idle_since is None:
            self.idle_since = time.monotonic()
        elif time.monotonic()-self.idle_since >= HIDE_DELAY:
            self.animate(True)

    @Slot()
    def switchMode(self):
        w = self.window
        was_portrait = w.property("portrait")
        current = w.geometry()
        if w.visibility() == QWindow.Maximized:
            w.showNormal()
            current = w.geometry()
        self.saved[was_portrait] = current
        self.set_edge("")
        w.setProperty("portrait", not was_portrait)
        if was_portrait:
            self.timer.stop()
        else:
            self.timer.start()
        area = w.screen().availableGeometry()
        rect = QRect(self.saved.get(not was_portrait, QRect(current.topLeft(), QSize(440, 740))))
        if not was_portrait and True not in self.saved:
            rect.moveCenter(current.center())
        rect.setSize(rect.size().boundedTo(area.size()).expandedTo(w.minimumSize()))
        w.setGeometry(fit(rect, area))

    @Slot()
    def pressTitle(self):
        self.drag_point = QCursor.pos()
        self.drag_origin = self.window.position()
        self.dragged = False

    @Slot()
    def dragTitle(self):
        if self.drag_point is None:
            return
        w = self.window
        delta = QCursor.pos()-self.drag_point
        if delta.manhattanLength() < QGuiApplication.styleHints().startDragDistance():
            return
        if w.property("portrait") and w.visibility() == QWindow.Windowed:
            self.dragged = True
            self.set_edge("")
            w.setPosition(self.drag_origin+delta)
        else:
            self.drag_point = None
            w.startSystemMove()

    @Slot()
    def releaseTitle(self):
        if self.dragged:
            # A drag to the physical edge often overshoots it by the offset
            # between the pointer and the title bar's corner.
            screen = QGuiApplication.screenAt(QCursor.pos()) or self.window.screen()
            rect = fit(self.window.geometry(), screen.availableGeometry())
            self.window.setPosition(rect.topLeft())
        self.dragged = False
        self.drag_point = None
        self.poll()
