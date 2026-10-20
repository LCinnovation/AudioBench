"""自绘小控件：旋钮、推子、电平表、图标。全部 QPainter，无图片资源、无动画。"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme


class Knob(QWidget):
    """24px 旋钮：拖动改值、双击回默认、滚轮微调；范围 [-1,1] 映射到角度。"""

    valueChanged = Signal(float)
    editStarted = Signal()
    editFinished = Signal()

    def __init__(self, minimum=-1.0, maximum=1.0, default=0.0, parent=None,
                 *, formatter=None, diameter: int = 24):
        super().__init__(parent)
        self._min, self._max, self._default = float(minimum), float(maximum), float(default)
        self._value = self._default
        self._drag_y = None
        self._formatter = formatter or (lambda v: f"{v:+.1f}")
        self._d = diameter
        self.setFixedSize(diameter + 6, diameter + 6)
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip(self._formatter(self._value))

    # ---------------------------------------------------------------- 值
    def value(self) -> float:
        return self._value

    def set_value(self, v: float, *, emit: bool = True) -> None:
        v = max(self._min, min(self._max, float(v)))
        if abs(v - self._value) < 1e-9:
            return
        self._value = v
        self.setToolTip(self._formatter(v))
        self.update()
        if emit:
            self.valueChanged.emit(v)

    def reset(self) -> None:
        self.set_value(self._default)

    # ---------------------------------------------------------------- 交互
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_y = e.position().y()
            self.editStarted.emit()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_y is None:
            return
        dy = self._drag_y - e.position().y()
        self._drag_y = e.position().y()
        span = self._max - self._min
        self.set_value(self._value + dy * span / 160.0)

    def mouseReleaseEvent(self, e):
        if self._drag_y is not None:
            self._drag_y = None
            self.editFinished.emit()

    def mouseDoubleClickEvent(self, e):
        self.reset()

    def wheelEvent(self, e):
        step = (self._max - self._min) / 50.0
        self.set_value(self._value + (step if e.angleDelta().y() > 0 else -step))

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(3, 3, self._d, self._d)
        p.setPen(QPen(theme.qcolor(theme.LINE), 1))
        p.setBrush(QBrush(theme.qcolor(theme.PANEL_ALT)))
        p.drawEllipse(r)
        frac = (self._value - self._min) / (self._max - self._min)
        ang = math.radians(225 - 270 * frac)
        cx, cy = r.center().x(), r.center().y()
        rad = self._d * 0.32
        p.setPen(QPen(theme.qcolor(theme.SELECT if abs(self._value) > 1e-6 else theme.TEXT_DIM), 2))
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(ang) * rad, cy - math.sin(ang) * rad))


class Fader(QWidget):
    """垂直推子：范围 dB，双击归零；刻度线 0dB。"""

    valueChanged = Signal(float)
    editStarted = Signal()
    editFinished = Signal()

    def __init__(self, minimum=-60.0, maximum=12.0, default=0.0, parent=None):
        super().__init__(parent)
        self._min, self._max, self._default = minimum, maximum, default
        self._value = default
        self._drag = None
        self.setFixedSize(12, 48)
        self.setCursor(Qt.SizeVerCursor)

    def value(self) -> float:
        return self._value

    def set_value(self, v: float, *, emit: bool = True) -> None:
        v = max(self._min, min(self._max, float(v)))
        if abs(v - self._value) < 1e-9:
            return
        self._value = v
        self.setToolTip(f"{v:.1f} dB")
        self.update()
        if emit:
            self.valueChanged.emit(v)

    def _y_to_value(self, y: float) -> float:
        frac = 1.0 - max(0.0, min(1.0, y / max(1, self.height())))
        return self._min + frac * (self._max - self._min)

    def mousePressEvent(self, e):
        self.editStarted.emit()
        self.set_value(self._y_to_value(e.position().y()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.set_value(self._y_to_value(e.position().y()))

    def mouseReleaseEvent(self, e):
        self.editFinished.emit()

    def mouseDoubleClickEvent(self, e):
        self.set_value(self._default)
        self.editStarted.emit()
        self.editFinished.emit()

    def wheelEvent(self, e):
        self.set_value(self._value + (1.0 if e.angleDelta().y() > 0 else -1.0))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.setPen(QPen(theme.qcolor(theme.LINE), 1))
        p.drawLine(0, 2, 0, h - 2)
        # 0dB 刻度
        frac0 = (0.0 - self._min) / (self._max - self._min)
        y0 = (1 - frac0) * h
        p.setPen(QPen(theme.qcolor(theme.TEXT_FAINT), 1))
        p.drawLine(0, int(y0), w, int(y0))
        frac = (self._value - self._min) / (self._max - self._min)
        y = (1 - frac) * (h - 6) + 3
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(theme.qcolor(theme.TEXT)))
        p.drawRoundedRect(QRectF(1, y - 4, w - 2, 8), 2, 2)


class Meter(QWidget):
    """立体声电平表（dBFS），0 是 0dB。"""

    def __init__(self, parent=None, *, horizontal: bool = False, height: int = 8):
        super().__init__(parent)
        self._peak = [-60.0, -60.0]
        self._horizontal = horizontal
        if horizontal:
            self.setFixedHeight(height)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            self.setFixedWidth(8)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def set_peak(self, left: float, right: float) -> None:
        db = [20 * math.log10(max(1e-6, v)) for v in (left, right)]
        if abs(db[0] - self._peak[0]) > 0.3 or abs(db[1] - self._peak[1]) > 0.3:
            self._peak = db
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.qcolor(theme.BG))
        n = 24
        if self._horizontal:
            for i in range(2):
                frac = max(0.0, min(1.0, (self._peak[i] + 60) / 60.0))
                w = int(self.width() * frac)
                y = i * (self.height() // 2)
                h = self.height() // 2 - 1
                p.fillRect(0, y, w, h, theme.qcolor(theme.OK if frac < 0.92 else theme.ERR))
        else:
            for i in range(2):
                frac = max(0.0, min(1.0, (self._peak[i] + 60) / 60.0))
                h = int(self.height() * frac)
                x = i * (self.width() // 2)
                w = self.width() // 2 - 1
                col = theme.ERR if frac > 0.95 else theme.SELECT if frac > 0.85 else theme.OK
                p.fillRect(x, self.height() - h, w, h, theme.qcolor(col))
        _ = n


class IconButton(QWidget):
    """24×24 图标按钮：自绘图标，hover/pressed 变色，checkable。"""

    clicked = Signal(bool)

    def __init__(self, kind: str, parent=None, *, size: int = 24, checkable: bool = False,
                 tooltip: str = ""):
        super().__init__(parent)
        self.kind = kind
        self._checked = False
        self._hover = False
        self._checkable = checkable
        self.setFixedSize(size + 8, size + 4)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, v: bool) -> None:
        if self._checkable and self._checked != v:
            self._checked = v
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self._checkable:
                self._checked = not self._checked
            self.update()
            self.clicked.emit(self._checked)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if not self.isEnabled():
            col = theme.TEXT_FAINT
        elif self._checked:
            col = theme.SELECT
        elif self._hover:
            col = theme.TEXT
        else:
            col = theme.TEXT_DIM
        p.setPen(QPen(theme.qcolor(col), 1.6))
        p.setBrush(Qt.NoBrush)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        s = min(w, h) * 0.28
        draw_glyph(p, self.kind, cx, cy, s, theme.qcolor(col))


def draw_glyph(p: QPainter, kind: str, cx: float, cy: float, s: float, color: QColor) -> None:
    """用最少图元画 24px 单色图标（§4.3 乐器图标 + 走带按钮）。"""
    p.setPen(QPen(color, max(1.4, s * 0.16)))
    p.setBrush(Qt.NoBrush)
    if kind == "play":
        path = QPainterPath()
        path.moveTo(cx - s * 0.6, cy - s)
        path.lineTo(cx + s, cy)
        path.lineTo(cx - s * 0.6, cy + s)
        path.closeSubpath()
        p.setBrush(QBrush(color))
        p.drawPath(path)
    elif kind == "stop":
        p.setBrush(QBrush(color))
        p.drawRect(QRectF(cx - s * 0.75, cy - s * 0.75, s * 1.5, s * 1.5))
    elif kind == "rewind":
        p.setBrush(QBrush(color))
        for dx in (-0.6, 0.4):
            path = QPainterPath()
            path.moveTo(cx + dx * s, cy - s)
            path.lineTo(cx + dx * s - s, cy)
            path.lineTo(cx + dx * s, cy + s)
            path.closeSubpath()
            p.drawPath(path)
    elif kind == "loop":
        p.drawArc(QRectF(cx - s, cy - s * 0.6, s * 2, s * 1.2), 30 * 16, 300 * 16)
    elif kind == "metronome":
        path = QPainterPath()
        path.moveTo(cx - s * 0.7, cy + s)
        path.lineTo(cx - s * 0.2, cy - s)
        path.lineTo(cx + s * 0.3, cy - s)
        path.lineTo(cx + s * 0.8, cy + s)
        p.drawPath(path)
        p.drawLine(QPointF(cx, cy - s * 0.2), QPointF(cx + s * 0.5, cy - s * 0.8))
    elif kind == "vocal":
        p.drawEllipse(QRectF(cx - s * 0.45, cy - s, s * 0.9, s * 1.1))
        p.drawArc(QRectF(cx - s * 0.8, cy - s * 0.3, s * 1.6, s * 1.5), 200 * 16, 140 * 16)
        p.drawLine(QPointF(cx, cy + s * 0.9), QPointF(cx, cy + s * 1.3))
    elif kind == "drum":
        p.drawEllipse(QRectF(cx - s, cy - s * 0.5, s * 2, s * 1.1))
        p.drawLine(QPointF(cx - s * 0.7, cy + s * 0.2), QPointF(cx + s * 0.5, cy + s))
        p.drawLine(QPointF(cx + s * 0.7, cy + s * 0.2), QPointF(cx - s * 0.5, cy + s))
    elif kind in ("bass", "guitar"):
        p.drawRoundedRect(QRectF(cx - s * 0.9, cy - s * 0.2, s * 1.1, s * 1.1), s * 0.5, s * 0.5)
        p.drawLine(QPointF(cx - s * 0.1, cy), QPointF(cx + s, cy - s))
        p.drawEllipse(QRectF(cx - s * 0.6, cy + s * 0.1, s * 0.35, s * 0.35))
    elif kind == "piano":
        p.drawRect(QRectF(cx - s, cy - s * 0.6, s * 2, s * 1.2))
        for i in range(1, 4):
            x = cx - s + i * s * 0.5
            p.drawLine(QPointF(x, cy - s * 0.6), QPointF(x, cy + s * 0.6))
    elif kind == "other":
        path = QPainterPath()
        path.moveTo(cx - s, cy)
        path.cubicTo(cx - s * 0.4, cy - s, cx + s * 0.4, cy + s, cx + s, cy)
        p.drawPath(path)
    elif kind == "export":
        p.drawLine(QPointF(cx, cy - s), QPointF(cx, cy + s * 0.4))
        p.drawLine(QPointF(cx, cy + s * 0.4), QPointF(cx + s * 0.55, cy - s * 0.15))
        p.drawLine(QPointF(cx, cy + s * 0.4), QPointF(cx - s * 0.55, cy - s * 0.15))
        p.drawLine(QPointF(cx - s, cy + s * 0.9), QPointF(cx + s, cy + s * 0.9))
    elif kind == "info":
        p.drawEllipse(QRectF(cx - s, cy - s, 2 * s, 2 * s))
        p.drawLine(QPointF(cx, cy - s * 0.1), QPointF(cx, cy + s * 0.5))
        p.drawPoint(QPointF(cx, cy - s * 0.45))
    elif kind == "settings":
        p.drawEllipse(QRectF(cx - s * 0.45, cy - s * 0.45, s * 0.9, s * 0.9))
        for k in range(6):
            a = k * math.pi / 3
            p.drawLine(QPointF(cx + math.cos(a) * s * 0.7, cy + math.sin(a) * s * 0.7),
                       QPointF(cx + math.cos(a) * s, cy + math.sin(a) * s))
    else:
        p.drawEllipse(QRectF(cx - s, cy - s, 2 * s, 2 * s))


def icon_pixmap(kind: str, size: int, color_hex: str) -> "QPixmap":  # pragma: no cover
    from PySide6.QtGui import QPixmap

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    draw_glyph(p, kind, size / 2, size / 2, size * 0.28, theme.qcolor(color_hex))
    p.end()
    return pm


__all__ = ["Knob", "Fader", "Meter", "IconButton", "draw_glyph", "icon_pixmap", "QSize", "QLinearGradient"]
