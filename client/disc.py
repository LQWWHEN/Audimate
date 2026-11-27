"""One painted Qt Quick item for the record, mirrored ribbon and artwork transition."""
import math
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtQuick import QQuickPaintedItem

from .spectrum import BANDS


ICE = QColor("#9edcf4")
# Ribbon amplitude, whole-ring displacement, cover displacement, band release seconds.
MOTION = {"heavy": (.38, .155, .048, .055), "pop": (.33, .115, .030, .075),
          "light": (.16, .045, .008, .135)}


def tint(color, alpha):
    result = QColor(color)
    result.setAlphaF(alpha)
    return result


def demo_art():
    """Original landscape print for the local synth track, rendered once."""
    y, x = np.mgrid[-1:1:600j, -1:1:600j]
    sky = np.clip((y + 1) * .85, 0, 1)[:, :, None]
    rgb = (1 - sky) * [64, 81, 161] + sky * [244, 168, 166]
    sun = (x - .19)**2 + (y + .26)**2 < .215**2
    rgb[sun] = [255, 223, 178]
    for i in range(13):
        ridge = (-.24 + i * .105 + .09 * np.sin(x * 3.4 + i * .54)
                 + .065 * np.cos(x * 6 - i * .16) + .018 * np.sin(x * 18 + i))
        below = y > ridge
        depth = i / 12
        rgb[below] = np.array([117, 119, 177]) * (1 - depth) + np.array([25, 37, 73]) * depth
        rgb[(y > ridge) & (y < ridge + .009)] += [45, 42, 41]
    rgb += np.random.default_rng(74).normal(0, 2.5, (600, 600, 1))
    data = np.ascontiguousarray(rgb.clip(0, 255).astype("u1"))
    return QImage(data.data, 600, 600, data.strides[0], QImage.Format_RGB888).copy()


class Disc(QQuickPaintedItem):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAntialiasing(True)
        self.setOpaquePainting(False)
        self.analyzer = None
        self.style = "pop"
        self.motion = np.array(MOTION["pop"])
        self.bands = np.zeros(BANDS)
        self.kick = self.snare = 0.
        self.last_tick = time.monotonic()
        self.last_audio = 0
        self.angle = np.linspace(0, math.pi * 2, 2 * (BANDS - 1), endpoint=False)
        self.dx, self.dy = np.sin(self.angle), -np.cos(self.angle)
        self.art = self.old_art = QImage()
        self.accent = self.old_accent = ICE
        self.fade_start = 0
        self.reduced = False
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.advance)
        self.timer.start(16)

    def artwork(self, img, color):
        self.old_art, self.art = self.art, img
        self.old_accent, self.accent = self.accent, color
        self.fade_start = time.monotonic()
        self.update()

    @Slot(bool)
    def reduceMotion(self, value):
        self.reduced = value
        self.update()

    def advance(self):
        now = time.monotonic()
        dt = min(.1, now - self.last_tick)
        self.last_tick = now
        if not self.isVisible():
            return
        stamp, bands, kick, snare = self.analyzer.snapshot if self.analyzer else (0, 0, 0, 0)
        fresh = now - stamp < .045
        if not fresh and max(self.kick, self.snare, self.bands.max()) < .001 and now - self.fade_start > .65:
            return
        self.motion += (np.array(MOTION[self.style]) - self.motion) * min(1., dt * 8)
        target = bands if fresh else 0
        self.bands += (target - self.bands) * (1 - np.exp(-dt / np.where(target > self.bands, .011, self.motion[3])))
        onset = fresh and stamp != self.last_audio
        self.kick = max(kick if onset else 0, self.kick * math.exp(-dt / .060))
        self.snare = max(snare if onset else 0, self.snare * math.exp(-dt / .043))
        self.last_audio = stamp
        self.update()

    def paint(self, p):
        w, h = self.width(), self.height()
        if min(w, h) <= 1:
            return
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        p.translate(w / 2, h / 2)
        unit = min(w, h) / 2
        fade = min(1., (time.monotonic() - self.fade_start) / .58)
        color = QColor.fromRgbF(*(a + (b-a)*fade for a,b in zip(
            self.old_accent.getRgbF()[:3], self.accent.getRgbF()[:3])))
        halo = QRadialGradient(0, 0, unit)
        halo.setColorAt(.43, tint(color, .15))
        halo.setColorAt(.73, tint(color, .052))
        halo.setColorAt(1, tint(color, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(), unit, unit)
        base = unit * .68
        # Coincident kick and snare share a single beat, with headroom for the ribbon.
        beat = 0 if self.reduced else max(self.kick, self.snare)
        inner = base * (1 + beat*self.motion[1])
        levels = np.r_[self.bands, self.bands[-2:0:-1]]
        levels = .82 * levels + .09 * (np.roll(levels, 1) + np.roll(levels, -1))
        power = .27 if self.reduced else 1.
        stroke = max(1.25, unit*.007)
        rest = inner + base * .016
        excursion = min(base * self.motion[0], max(0., unit - stroke/2 - rest))
        radii = rest + power * levels * excursion
        points = [QPointF(x, y) for x,y in zip(self.dx*radii, self.dy*radii)]
        ribbon = QPainterPath((points[-1]+points[0])*.5)
        for i, point in enumerate(points):
            ribbon.quadTo(point, (point + points[(i+1) % len(points)])*.5)
        ribbon.closeSubpath()
        grad = QRadialGradient(0, 0, inner + base*.36)
        grad.setColorAt(0, tint(color, .12))
        grad.setColorAt(inner/grad.radius(), tint(color, .22))
        grad.setColorAt(1, color.lighter(119))
        p.setBrush(grad)
        p.setPen(QPen(tint(color, .91), stroke))
        p.drawPath(ribbon)
        p.setBrush(QColor("#101524"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(), inner-1, inner-1)
        radius = base * .94 * (1 + beat*self.motion[2])
        clip = QPainterPath()
        rect = QRectF(-radius, -radius, radius*2, radius*2)
        clip.addEllipse(rect)
        p.setClipPath(clip)
        self.draw_art(p, self.old_art if fade < 1 else self.art, rect)
        if fade < 1:
            p.setOpacity(fade)
            self.draw_art(p, self.art, rect)
            p.setOpacity(1)

    @staticmethod
    def draw_art(p, image, rect):
        if not image.isNull():
            side = min(image.width(), image.height())
            p.drawImage(rect, image, QRectF((image.width()-side)/2, (image.height()-side)/2, side, side))
            return
        r = rect.width()/2
        surface = QLinearGradient(rect.topLeft(), rect.bottomRight())
        for stop, col in ((0,"#4f6f85"),(.24,"#293c54"),(.52,"#1e2b41"),(.78,"#344961"),(1,"#232239")):
            surface.setColorAt(stop, QColor(col))
        p.fillRect(rect, surface)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(tint(ICE, .087), .9))
        for i in range(18):
            rr = r*(.48 + i*.031)
            p.drawEllipse(QPointF(), rr, rr)
        # Three arched strokes are the Audimate mark on the record label.
        for i, height in enumerate((.12, .26, .17)):
            x = (i-1)*r*.16
            pen = QPen(QColor("#cee9ef"), r*.068)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x, r*height), QPointF(x + r*.07, -r*height))
