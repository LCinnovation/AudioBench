"""编排区（§4.4）：时间标尺 + 波形时间线 + 播放头。

结构：
    TimelineArea
      ├── RulerWidget        28px，跟横向滚动联动
      └── QScrollArea
            └── TimelineCanvas   （整幅时间线，横向/纵向滚动由滚动区负责）
                   └── PlayheadOverlay（只重绘一条线，不触发整幅重绘）

交互（M1）：点击定位、点击选片段、Ctrl+滚轮缩放、Shift+滚轮纵向滚动、
拖入音频文件导入、标尺上 Shift+拖拽设 A-B 循环、右键菜单。
"""

from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractScrollArea, QMenu, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..app.i18n import tr
from ..model.track import Track
from . import theme
from .timefmt import samples_to_bars, samples_to_time

log = logging.getLogger("ab.ui.timeline")

TRACK_H = 72
RULER_H = 28
MIN_ZOOM = 4.0
MAX_ZOOM = 4000.0


class RulerWidget(QWidget):
    seekRequested = Signal(int)
    loopChanged = Signal(int, int)
    markerRequested = Signal(str, int)     # ("add"|"remove", pos)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setFixedHeight(RULER_H)
        self.zoom = 160.0
        self.offset = 0
        self._drag_a: int | None = None

    def set_view(self, zoom: float, offset: int) -> None:
        self.zoom, self.offset = zoom, offset
        self.update()

    def _x_to_sample(self, x: float) -> int:
        sr = self.state.project.sample_rate
        return max(0, int((x + self.offset) / self.zoom * sr))

    def _marker_at(self, x: float):
        for m in self.state.project.markers:
            mx = m["pos"] / max(1, self.state.project.sample_rate) * self.zoom - self.offset
            if abs(mx - x) <= 7:
                return m
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            marker = self._marker_at(e.position().x())
            if marker is not None:
                self.seekRequested.emit(int(marker["pos"]))
                return
            if e.modifiers() & Qt.ShiftModifier:
                self._drag_a = self._x_to_sample(e.position().x())
            else:
                self.seekRequested.emit(self._x_to_sample(e.position().x()))

    def mouseMoveEvent(self, e):
        if self._drag_a is not None:
            a = self._drag_a
            b = self._x_to_sample(e.position().x())
            self.loopChanged.emit(min(a, b), max(a, b))
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag_a = None

    def mouseDoubleClickEvent(self, e):
        self.loopChanged.emit(-1, -1)

    def _build_menu(self, x: float) -> QMenu:
        """右键菜单单独拆出来（QContextMenuEvent 没有 position()，只有 pos()）。"""
        pos = self._x_to_sample(x)
        marker = self._marker_at(x)
        menu = QMenu(self)
        if marker is not None:
            act = menu.addAction(tr("Timeline", "Delete marker"))
            act.triggered.connect(lambda: self.markerRequested.emit("remove", int(marker["pos"])))
            act2 = menu.addAction(tr("Timeline", "Rename marker…"))
            act2.triggered.connect(lambda: self.markerRequested.emit("rename", int(marker["pos"])))
        else:
            act = menu.addAction(tr("Timeline", "Add marker here"))
            act.triggered.connect(lambda: self.markerRequested.emit("add", pos))
        menu.addSeparator()
        act3 = menu.addAction(tr("Timeline", "Set loop start here"))
        act3.triggered.connect(
            lambda: self.loopChanged.emit(pos, pos + self.state.project.sample_rate * 4))
        return menu

    def contextMenuEvent(self, e):
        self._build_menu(e.pos().x()).exec(e.globalPos())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.qcolor(theme.PANEL))
        p.setPen(QPen(theme.qcolor(theme.LINE), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

        sr = self.state.project.sample_rate
        bpm = float(self.state.config.get("ui.bpm", 120.0) or 120.0)
        # 刻度步长：让主刻度间隔 >= 80px
        candidates = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
        step = next((c for c in candidates if c * self.zoom >= 80), 600)
        t0 = self.offset / self.zoom
        t1 = (self.offset + self.width()) / self.zoom
        first = int(t0 // step) * step
        t = first
        p.setFont(QFont(theme.app_family(), 8))
        while t <= t1 + step:
            x = t * self.zoom - self.offset
            if x >= -40:
                p.setPen(QPen(theme.qcolor(theme.LINE), 1))
                p.drawLine(int(x), RULER_H - 8, int(x), RULER_H - 1)
                p.setPen(QPen(theme.qcolor(theme.TEXT_FAINT), 1))
                if step < 1.0:
                    label = samples_to_time(int(t * sr), sr)[3:12]
                else:
                    label = samples_to_bars(int(t * sr), sr, bpm)
                p.drawText(int(x) + 3, RULER_H - 10, label)
            # 次刻度
            for k in (1, 2, 3):
                xs = (t + step * k / 4.0) * self.zoom - self.offset
                if xs >= 0:
                    p.setPen(QPen(theme.qcolor(theme.LINE_SOFT), 1))
                    p.drawLine(int(xs), RULER_H - 4, int(xs), RULER_H - 1)
            t += step

        a, b = self.state.engine.loop if self.state.engine else (None, None)
        if a is not None and b is not None:
            xa = a / sr * self.zoom - self.offset
            xb = b / sr * self.zoom - self.offset
            p.fillRect(QRectF(xa, 0, max(1.0, xb - xa), RULER_H - 1),
                       theme.qcolor(theme.SELECT, 60))
            p.setPen(QPen(theme.qcolor(theme.SELECT), 1))
            p.drawLine(QPointF(xa, 0), QPointF(xa, RULER_H))
            p.drawLine(QPointF(xb, 0), QPointF(xb, RULER_H))

        # 标记点：顶部小三角 + 竖细线（P2-11）
        for m in self.state.project.markers:
            mx = m["pos"] / max(1, sr) * self.zoom - self.offset
            if mx < -20 or mx > self.width() + 20:
                continue
            p.setPen(QPen(theme.qcolor(theme.OK), 1))
            p.setBrush(QBrush(theme.qcolor(theme.OK)))
            p.drawPolygon(QPolygonF([QPointF(mx - 5, RULER_H - 9),
                                     QPointF(mx + 5, RULER_H - 9),
                                     QPointF(mx, RULER_H - 1)]))
            p.setPen(QPen(theme.qcolor(theme.OK, 120), 1))
            p.drawLine(QPointF(mx, 2), QPointF(mx, RULER_H - 9))
            p.setPen(QPen(theme.qcolor(theme.TEXT), 1))
            p.setFont(QFont(theme.app_family(), 8))
            p.drawText(QPointF(mx + 7, 11), m.get("name", ""))


class PlayheadOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._x = 0

    def set_x(self, x: int) -> None:
        if x != self._x:
            self._x = x
            self.move(max(0, x - 5), 0)
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(QPen(theme.qcolor(theme.PLAYHEAD), 1))
        p.drawLine(5, 0, 5, self.height())
        p.setBrush(QBrush(theme.qcolor(theme.PLAYHEAD)))
        p.setPen(Qt.NoPen)
        tri = QPolygonF([QPointF(0, 0), QPointF(10, 0), QPointF(5, 8)])
        p.drawPolygon(tri)


class TimelineCanvas(QWidget):
    seekRequested = Signal(int)
    clipSelected = Signal(object)
    filesDropped = Signal(list)
    editRequested = Signal(str, object)

    EDGE_PX = 7
    FADE_PX = 14
    SNAP_PX = 8

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.zoom = 160.0
        self.selected_clip = None
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._font = QFont(theme.app_family(), 9)
        self._drag: dict | None = None
        self._hover = None

    # ---------------------------------------------------------------- 几何
    def track_y(self, index: int) -> int:
        return index * TRACK_H

    def x_to_sample(self, x: float) -> int:
        sr = self.state.project.sample_rate
        return max(0, int(x / self.zoom * sr))

    def sample_to_x(self, sample: int) -> float:
        sr = max(1, self.state.project.sample_rate)
        return sample / sr * self.zoom

    def update_geometry(self) -> None:
        sr = max(1, self.state.project.sample_rate)
        dur_s = max(self.state.duration() / sr, 10.0)
        w = int(dur_s * self.zoom) + 400
        h = max(TRACK_H * max(1, len(self.state.project.tracks)), 200)
        self.setMinimumSize(w, h)
        self.resize(w, h)
        self.update()

    # ---------------------------------------------------------------- 命中
    def _track_at(self, y: float):
        idx = int(y) // TRACK_H
        if 0 <= idx < len(self.state.project.tracks):
            return idx, self.state.project.tracks[idx]
        return -1, None

    def _hit(self, pos: QPoint):
        """返回 (kind, clip, track_index)；kind ∈ move/trim_l/trim_r/fade_in/fade_out。"""
        idx, track = self._track_at(pos.y())
        if track is None:
            return None, None, -1
        x = pos.x()
        top = self.track_y(idx) + 2
        for c in track.clips:
            x0 = self.sample_to_x(c.timeline_start)
            x1 = self.sample_to_x(c.timeline_end)
            if x0 - 3 <= x <= x1 + 3:
                if abs(x - x0) <= self.EDGE_PX:
                    return "trim_l", c, idx
                if abs(x - x1) <= self.EDGE_PX:
                    return "trim_r", c, idx
                if pos.y() - top <= self.FADE_PX:
                    if x - x0 <= self.FADE_PX * 2:
                        return "fade_in", c, idx
                    if x1 - x <= self.FADE_PX * 2:
                        return "fade_out", c, idx
                return "move", c, idx
        return None, None, idx

    # ---------------------------------------------------------------- 交互
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            kind, clip, idx = self._hit(e.position())
            sample = self.x_to_sample(e.position().x())
            if clip is not None:
                self.selected_clip = clip
                self.clipSelected.emit(clip)
                label = {"move": "Move clip", "trim_l": "Trim clip", "trim_r": "Trim clip",
                         "fade_in": "Clip fade in", "fade_out": "Clip fade out"}[kind]
                self.state.live_begin(label)
                self._drag = {
                    "kind": kind, "clip": clip, "start_x": e.position().x(),
                    "orig": (clip.timeline_start, clip.src_start, clip.src_end,
                             clip.fade_in, clip.fade_out),
                }
            else:
                self.selected_clip = None
                self.clipSelected.emit(None)
                self.seekRequested.emit(sample)
            self.update()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            kind, clip, _ = self._hit(e.position())
            cursor = Qt.ArrowCursor
            if kind in ("trim_l", "trim_r"):
                cursor = Qt.SizeHorCursor
            elif kind in ("fade_in", "fade_out"):
                cursor = Qt.PointingHandCursor
            elif kind == "move":
                cursor = Qt.OpenHandCursor
            self.setCursor(cursor)
            self._hover = kind
            return
        d = self._drag
        clip = d["clip"]
        sr = max(1, self.state.project.sample_rate)
        dx = e.position().x() - d["start_x"]
        delta = int(dx / self.zoom * sr)
        t0, s0, s1, f0, f1 = d["orig"]
        kind = d["kind"]
        if kind == "move":
            new_start = max(0, t0 + delta)
            new_start = self._snap(new_start, clip, exclude=clip)
            clip.timeline_start = new_start
        elif kind == "trim_l":
            new_t = max(0, t0 + delta)
            shift = new_t - t0
            clip.src_start = max(0, min(s0 + shift, s1 - 64))
            clip.timeline_start = max(0, t0 + (clip.src_start - s0))
        elif kind == "trim_r":
            clip.src_end = max(clip.src_start + 64, s1 + delta)
        elif kind == "fade_in":
            clip.fade_in = max(0, min(f0 + delta, clip.length - clip.fade_out))
        elif kind == "fade_out":
            clip.fade_out = max(0, min(f1 + delta, clip.length - clip.fade_in))
        clip.clamp()
        self.state.live_touch()
        self.update()
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.state.live_end()
            self.update()
            e.accept()

    def _snap(self, sample: int, clip, exclude=None) -> int:
        thresh = int(self.SNAP_PX / self.zoom * max(1, self.state.project.sample_rate))
        if thresh <= 0:
            return sample
        candidates = [0, self.state.position()]
        for t in self.state.project.tracks:
            for c in t.clips:
                if c is exclude or c is clip:
                    continue
                candidates += [c.timeline_start, c.timeline_end]
        best = sample
        best_d = thresh + 1
        for cand in candidates:
            d = abs(cand - sample)
            if d < best_d:
                best, best_d = cand, d
        return best if best_d <= thresh else sample

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected_clip is not None:
            self.editRequested.emit("delete_clip", self.selected_clip)
            e.accept()
        elif e.key() == Qt.Key_S:
            self.editRequested.emit("split_at_playhead", None)
            e.accept()
        else:
            super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.seekRequested.emit(self.x_to_sample(e.position().x()))

    def _build_menu(self, pos: QPoint) -> tuple[QMenu, dict]:
        """右键菜单单独拆出来，测试可以直接构造而不用 exec（exec 会阻塞）。"""
        menu = QMenu(self)
        _kind, clip, _idx = self._hit(pos)
        act_split = menu.addAction(tr("Timeline", "Split at playhead"))
        act_dup = menu.addAction(tr("Timeline", "Duplicate clip"))
        act_del = menu.addAction(tr("Timeline", "Delete clip"))
        menu.addSeparator()
        act_loop = menu.addAction(tr("Timeline", "Set loop start here"))
        for a in (act_dup, act_del):
            a.setEnabled(self.selected_clip is not None)
        return menu, {"split": act_split, "dup": act_dup, "del": act_del,
                      "loop": act_loop, "clip": clip, "x": pos.x()}

    def contextMenuEvent(self, e):
        menu, acts = self._build_menu(e.pos())
        chosen = menu.exec(e.globalPos())
        clip = acts["clip"]
        if chosen is acts["split"]:
            self.editRequested.emit("split_at_playhead", clip)
        elif chosen is acts["dup"] and self.selected_clip is not None:
            self.editRequested.emit("duplicate_clip", self.selected_clip)
        elif chosen is acts["del"] and self.selected_clip is not None:
            self.editRequested.emit("delete_clip", self.selected_clip)
        elif chosen is acts["loop"]:
            self.editRequested.emit("loop_start", self.x_to_sample(acts["x"]))

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            delta = e.angleDelta().y()
            factor = 1.25 if delta > 0 else 1 / 1.25
            parent = self.parentWidget()
            while parent is not None and not isinstance(parent, TimelineArea):
                parent = parent.parentWidget()
            if isinstance(parent, TimelineArea):
                parent.zoom_by(factor, e.position().x())
            e.accept()
        else:
            e.ignore()   # 交给滚动区

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            e.acceptProposedAction()

    def _hit_clip(self, pos: QPoint):
        idx = int(pos.y()) // TRACK_H
        if not (0 <= idx < len(self.state.project.tracks)):
            return None
        x = self.x_to_sample(pos.x())
        for c in self.state.project.tracks[idx].clips:
            if c.timeline_start <= x < c.timeline_end:
                return c
        return None

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, e):
        p = QPainter(self)
        p.setFont(self._font)
        p.fillRect(e.rect(), theme.qcolor(theme.BG))
        sr = max(1, self.state.project.sample_rate)
        tracks = self.state.project.tracks

        # 空工程提示
        if not tracks:
            p.setPen(QPen(theme.qcolor(theme.TEXT_FAINT), 1))
            p.setFont(QFont(theme.app_family(), 11))
            p.drawText(self.rect().adjusted(0, 40, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                       tr("Timeline", "Drop audio files here, or use Import (Ctrl+O)"))
            return

        clip_rect = e.rect()
        for i, t in enumerate(tracks):
            y = self.track_y(i)
            p.fillRect(QRect(0, y, self.width(), TRACK_H - 1),
                       theme.qcolor(theme.PANEL if i % 2 == 0 else theme.BG))
            p.setPen(QPen(theme.qcolor(theme.LINE_SOFT), 1))
            p.drawLine(0, y + TRACK_H - 1, self.width(), y + TRACK_H - 1)
            for c in t.clips:
                self._draw_clip(p, t, c, y, clip_rect)
        # 网格：默认按小节（4/4）画，可在「视图 → 节拍网格」里关掉
        if self.state.config.get("ui.grid", True):
            bpm = float(self.state.config.get("ui.bpm", 120.0) or 120.0)
            bar_s = 240.0 / max(20.0, bpm)          # 4 拍一小节
            while bar_s * self.zoom < 40:
                bar_s *= 2
            b = 0.0
            while b * self.zoom < clip_rect.right() + bar_s * self.zoom:
                x = b * self.zoom
                if x >= clip_rect.left():
                    p.setPen(QPen(theme.qcolor(theme.LINE, 200 if (round(b / bar_s) % 4) else 120), 1))
                    p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
                b += bar_s
        else:
            step = 1.0 if self.zoom >= 80 else 5.0 if self.zoom >= 20 else 30.0
            p.setPen(QPen(theme.qcolor(theme.LINE_SOFT), 1))
            t = 0.0
            while t * self.zoom < clip_rect.right() + step * self.zoom:
                x = t * self.zoom
                if x >= clip_rect.left():
                    p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
                t += step

    def _draw_clip(self, p: QPainter, track: Track, clip, y: int, visible: QRect) -> None:
        sr = max(1, self.state.project.sample_rate)
        x0 = self.sample_to_x(clip.timeline_start)
        x1 = self.sample_to_x(clip.timeline_end)
        if x1 < visible.left() - 2 or x0 > visible.right() + 2:
            return
        rect = QRectF(x0, y + 2, max(2.0, x1 - x0), TRACK_H - 6)
        base = theme.qcolor(track.color)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(theme.qcolor(track.color, 46)))
        p.drawRoundedRect(rect, 4, 4)

        # 波形
        peaks = self.state.peaks_for(clip.path) if clip.path else None
        if peaks is not None:
            vx0 = max(rect.left(), visible.left())
            vx1 = min(rect.right(), visible.right())
            if vx1 - vx0 >= 2:
                width = int(vx1 - vx0)
                src0 = clip.src_start + int((vx0 - rect.left()) / self.zoom * sr)
                src1 = clip.src_start + int((vx1 - rect.left()) / self.zoom * sr)
                try:
                    mins, maxs = peaks.columns(src0, max(src0 + 1, src1), width)
                except Exception as ex:  # 缓存坏了不能拖垮绘制
                    log.debug("peaks draw failed: %s", ex)
                    mins = maxs = None
                if mins is not None:
                    p.setPen(QPen(base, 1))
                    path = QPainterPath()
                    for ch in range(min(mins.shape[0], 2)):
                        cy = rect.top() + rect.height() * (0.30 if ch == 0 else 0.70)
                        scale = rect.height() * 0.22
                        for k in range(width):
                            x = vx0 + k
                            lo = cy - maxs[ch, k] * scale
                            hi = cy - mins[ch, k] * scale
                            if hi - lo < 0.6:
                                hi = lo + 0.6
                            path.moveTo(x, lo)
                            path.lineTo(x, hi)
                    p.drawPath(path)

        # 淡入淡出三角
        if clip.fade_in > 0 or clip.fade_out > 0:
            p.setPen(QPen(theme.qcolor(theme.TEXT_FAINT, 140), 1))
            if clip.fade_in > 0:
                fx = rect.left() + clip.fade_in / sr * self.zoom
                p.drawLine(QPointF(rect.left(), rect.bottom()),
                           QPointF(min(fx, rect.right()), rect.top()))
            if clip.fade_out > 0:
                fx = rect.right() - clip.fade_out / sr * self.zoom
                p.drawLine(QPointF(max(fx, rect.left()), rect.top()),
                           QPointF(rect.right(), rect.bottom()))

        # 边框 / 选中
        if clip is self.selected_clip:
            p.setPen(QPen(theme.qcolor(theme.SELECT), 2))
        else:
            p.setPen(QPen(theme.qcolor(track.color, 150), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, 4, 4)

        # 名字
        if rect.width() > 40:
            p.setPen(QPen(theme.qcolor(theme.TEXT), 1))
            label = clip.name or clip.path
            p.drawText(rect.adjusted(6, 2, -4, 0), Qt.AlignLeft | Qt.AlignTop,
                       p.fontMetrics().elidedText(label, Qt.ElideRight, int(rect.width()) - 10))


class TimelineArea(QWidget):
    seekRequested = Signal(int)
    clipSelected = Signal(object)
    filesDropped = Signal(list)
    editRequested = Signal(str, object)
    markerRequested = Signal(str, int)
    zoomChanged = Signal(float)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.zoom = float(state.config.get("ui.zoom", 160.0) or 160.0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.ruler = RulerWidget(state, self)
        lay.addWidget(self.ruler)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.canvas = TimelineCanvas(state, self.scroll)
        self.scroll.setWidget(self.canvas)
        lay.addWidget(self.scroll, 1)

        self.overlay = PlayheadOverlay(self.canvas)

        self.canvas.seekRequested.connect(self.seekRequested)
        self.canvas.clipSelected.connect(self.clipSelected)
        self.canvas.filesDropped.connect(self.filesDropped)
        self.canvas.editRequested.connect(self.editRequested)
        self.ruler.seekRequested.connect(self.seekRequested)
        self.ruler.loopChanged.connect(self._on_loop_drag)
        self.ruler.markerRequested.connect(self.markerRequested)
        self.scroll.horizontalScrollBar().valueChanged.connect(self._on_hscroll)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _v: None)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.apply_zoom(self.zoom, keep_view=False)
        self.canvas.update_geometry()

    # ---------------------------------------------------------------- 视图
    def _on_hscroll(self, value: int) -> None:
        self.ruler.set_view(self.zoom, value)

    def _on_loop_drag(self, a: int, b: int) -> None:
        if a < 0:
            self.state.set_loop(None, None)
        else:
            self.state.set_loop(a, b)

    def zoom_by(self, factor: float, anchor_x: float | None = None) -> None:
        new = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))
        if abs(new - self.zoom) < 1e-9:
            return
        hbar = self.scroll.horizontalScrollBar()
        if anchor_x is None:
            anchor_x = hbar.value() + self.scroll.viewport().width() / 2
        sample_at_anchor = anchor_x / self.zoom
        self.apply_zoom(new)
        hbar.setValue(int(sample_at_anchor * new - anchor_x + hbar.value() * 0))
        self.zoomChanged.emit(new)

    def apply_zoom(self, zoom: float, *, keep_view: bool = True) -> None:
        self.zoom = float(zoom)
        self.canvas.zoom = self.zoom
        self.ruler.set_view(self.zoom, self.scroll.horizontalScrollBar().value())
        self.canvas.update_geometry()
        self.state.config.set("ui.zoom", round(self.zoom, 2))

    def refresh(self) -> None:
        self.canvas.update_geometry()
        self.canvas.update()

    def set_vertical_scroll(self, value: int) -> None:
        self.scroll.verticalScrollBar().setValue(value)

    def vertical_scrollbar(self):
        return self.scroll.verticalScrollBar()

    # ---------------------------------------------------------------- 跟随
    def _tick(self) -> None:
        st = self.state
        x = int(self.canvas.sample_to_x(st.position()))
        self.overlay.set_x(x)
        if self.overlay.height() != self.canvas.height():
            self.overlay.resize(11, self.canvas.height())
        # 播放时自动跟随
        if st.engine is not None and st.engine.playing:
            vp = self.scroll.viewport()
            hbar = self.scroll.horizontalScrollBar()
            if x < hbar.value() or x > hbar.value() + vp.width() - 40:
                hbar.setValue(max(0, x - 40))

    def retranslate(self) -> None:
        pass
