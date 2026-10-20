"""轨道头（§4.3）：固定 220px，每轨 72px。

布局（220×72）：
    行1  [序号][乐器图标][轨道名...............][M][S]
    行2  [颜色条 4px][推子 12×48][声像][低架][高架][渐入][渐出][折叠]

所有控件只读写 `ProjectModel` 的字段；连续拖动用 live_begin/live_touch/live_end，
避免拖一次推子就把控件重建一遍。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..app.i18n import tr
from ..model.track import Track
from . import theme
from .widgets import Fader, Knob, draw_glyph

HEADER_W = 220
HEADER_H = 72


class _MSButton(QPushButton):
    def __init__(self, letter: str, parent=None):
        super().__init__(letter, parent)
        self.setCheckable(True)
        self.setFixedSize(20, 18)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(
            f"QPushButton{{padding:0;border:1px solid {theme.LINE};border-radius:3px;"
            f"color:{theme.TEXT_DIM};background:{theme.PANEL};font-size:10px;}}"
            f"QPushButton:checked{{background:{theme.SELECT};color:{theme.BG};"
            f"border-color:{theme.SELECT};}}"
        )


class _NumberBox(QLineEdit):
    """渐入/渐出：0–10s，步进 0.1。"""

    valueChanged = Signal(float)

    def __init__(self, value: float = 0.0, parent=None):
        super().__init__(f"{value:.1f}", parent)
        self.setFixedSize(48, 18)
        self.setAlignment(Qt.AlignCenter)
        self.setToolTip(tr("TrackHeader", "Fade seconds (0 disables)"))
        self.setStyleSheet(
            f"QLineEdit{{background:{theme.PANEL};border:1px solid {theme.LINE};"
            f"border-radius:3px;color:{theme.TEXT};font-size:10px;padding:0;}}"
        )
        self.editingFinished.connect(self._commit)

    def _commit(self) -> None:
        try:
            v = max(0.0, min(10.0, float(self.text() or 0)))
        except ValueError:
            v = 0.0
        self.setText(f"{v:.1f}")
        self.valueChanged.emit(v)


class TrackHeader(QWidget):
    changed = Signal()          # 需要重建（结构）
    paramChanged = Signal()     # 只刷新数值
    clicked = Signal(int)       # 选中该轨

    def __init__(self, state, index: int, track: Track, parent=None):
        super().__init__(parent)
        self.state = state
        self.index = index
        self.track = track
        self.setFixedSize(HEADER_W, HEADER_H)
        self.setAutoFillBackground(False)

        # 名称
        self.name = QLineEdit(track.name, self)
        self.name.setGeometry(50, 5, 116, 20)
        self.name.setReadOnly(True)
        self.name.setFrame(False)
        self.name.setStyleSheet(
            f"QLineEdit{{background:transparent;color:{theme.TEXT};border:none;"
            f"font-size:12px;padding:1px 2px;}}"
            f"QLineEdit:focus{{background:{theme.PANEL};border:1px solid {theme.SELECT};}}"
        )
        self.name.setToolTip(track.name)
        self.name.editingFinished.connect(self._commit_name)
        self.name.installEventFilter(self)
        self._show_name(track.name)

        self.mute = _MSButton("M", self)
        self.mute.setGeometry(172, 6, 20, 18)
        self.mute.setChecked(track.mute)
        self.solo = _MSButton("S", self)
        self.solo.setGeometry(194, 6, 20, 18)
        self.solo.setChecked(track.solo)
        self.mute.clicked.connect(self._toggle_mute)
        self.solo.clicked.connect(self._toggle_solo)

        # 推子
        self.fader = Fader(-60.0, 12.0, track.gain_db, self)
        self.fader.setGeometry(12, 26, 12, 42)
        self.fader.set_value(track.gain_db, emit=False)
        self.fader.valueChanged.connect(self._on_gain)
        self.fader.editStarted.connect(lambda: self.state.live_begin("Volume"))
        self.fader.editFinished.connect(self.state.live_end)

        # 三个旋钮：声像 / 低架 / 高架
        self.pan = Knob(-1.0, 1.0, 0.0, self, formatter=_fmt_pan)
        self.pan.setGeometry(32, 34, 26, 26)
        self.pan.set_value(track.pan, emit=False)
        self.pan.valueChanged.connect(self._on_pan)

        self.eq_low = Knob(-12.0, 12.0, 0.0, self, formatter=lambda v: f"{v:+.1f} dB")
        self.eq_low.setGeometry(62, 34, 26, 26)
        self.eq_low.set_value(track.eq_low_db, emit=False)
        self.eq_low.valueChanged.connect(self._on_eq_low)

        self.eq_high = Knob(-12.0, 12.0, 0.0, self, formatter=lambda v: f"{v:+.1f} dB")
        self.eq_high.setGeometry(92, 34, 26, 26)
        self.eq_high.set_value(track.eq_high_db, emit=False)
        self.eq_high.valueChanged.connect(self._on_eq_high)

        for k in (self.pan, self.eq_low, self.eq_high):
            k.editStarted.connect(lambda: self.state.live_begin("Track parameter"))
            k.editFinished.connect(self.state.live_end)

        # 渐入/渐出
        self.fade_in = _NumberBox(track.fade_in_s, self)
        self.fade_in.setGeometry(126, 28, 44, 18)
        self.fade_in.setToolTip(tr("TrackHeader", "Fade in (s)"))
        self.fade_in.valueChanged.connect(self._on_fade_in)
        self.fade_out = _NumberBox(track.fade_out_s, self)
        self.fade_out.setGeometry(126, 50, 44, 18)
        self.fade_out.setToolTip(tr("TrackHeader", "Fade out (s)"))
        self.fade_out.valueChanged.connect(self._on_fade_out)

        # EQ 旁通 + 折叠
        self.eq_btn = _MSButton("EQ", self)
        self.eq_btn.setGeometry(174, 50, 26, 18)
        self.eq_btn.setChecked(track.eq_enabled)
        self.eq_btn.setToolTip(tr("TrackHeader", "EQ on/off"))
        self.eq_btn.clicked.connect(self._toggle_eq)

        self.collapse = QPushButton("▾", self)
        self.collapse.setGeometry(200, 50, 18, 18)
        self.collapse.setCheckable(True)
        self.collapse.setChecked(track.collapsed)
        self.collapse.setStyleSheet(
            f"QPushButton{{padding:0;border:1px solid {theme.LINE};border-radius:3px;"
            f"background:{theme.PANEL};color:{theme.TEXT_DIM};font-size:10px;}}"
        )
        self.collapse.setToolTip(tr("TrackHeader", "Collapse"))
        self.collapse.clicked.connect(self._toggle_collapse)

    # ---------------------------------------------------------------- 交互
    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent

        if obj is self.name and ev.type() == QEvent.MouseButtonDblClick:
            self.name.setReadOnly(False)
            self.name.setFocus()
            self.name.selectAll()
            return True
        return super().eventFilter(obj, ev)

    def _commit_name(self) -> None:
        new = self.name.text().strip() or self.track.name
        self.name.setReadOnly(True)
        if new != self.track.name:
            with self.state.edit("Rename track"):
                self.track.name = new
        self._show_name(self.track.name)

    def _show_name(self, text: str) -> None:
        """只读态显示名字的**开头**。

        QLineEdit.setText 会把光标放到末尾，长名字因此只露出结尾
        （实测「沈腾-平凡之路」显示成「凡之路(15647731_2)」），
        而结尾恰恰是最不具辨识度的部分。光标归零后从头开始显示，完整名走 tooltip。
        """
        self.name.setText(text)
        self.name.setToolTip(text)
        self.name.setCursorPosition(0)

    def _toggle_mute(self) -> None:
        with self.state.edit("Mute"):
            self.track.mute = self.mute.isChecked()

    def _toggle_solo(self) -> None:
        with self.state.edit("Solo"):
            self.track.solo = self.solo.isChecked()

    def _toggle_eq(self) -> None:
        with self.state.edit("EQ enable"):
            self.track.eq_enabled = self.eq_btn.isChecked()

    def _toggle_collapse(self) -> None:
        with self.state.edit("Collapse track"):
            self.track.collapsed = self.collapse.isChecked()

    def _on_gain(self, db: float) -> None:
        self.track.gain_db = db
        self.state.live_touch()

    def _on_pan(self, v: float) -> None:
        self.track.pan = v
        self.pan.setToolTip(_fmt_pan(v))
        self.state.live_touch()

    def _on_eq_low(self, v: float) -> None:
        self.track.eq_low_db = v
        self.state.live_touch()

    def _on_eq_high(self, v: float) -> None:
        self.track.eq_high_db = v
        self.state.live_touch()

    def _on_fade_in(self, v: float) -> None:
        with self.state.edit("Fade in"):
            self.track.fade_in_s = v

    def _on_fade_out(self, v: float) -> None:
        with self.state.edit("Fade out"):
            self.track.fade_out_s = v

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        selected = getattr(self, "selected", False)
        bg = theme.PANEL_ALT if selected else theme.PANEL
        p.fillRect(QRect(0, 0, HEADER_W - 1, HEADER_H - 1), theme.qcolor(bg))
        p.setPen(QPen(theme.qcolor(theme.LINE), 1))
        p.drawLine(0, HEADER_H - 1, HEADER_W, HEADER_H - 1)
        if selected:
            p.setPen(QPen(theme.qcolor(theme.SELECT), 2))
            p.drawRect(QRect(1, 1, HEADER_W - 2, HEADER_H - 2))
        # 颜色条
        p.fillRect(QRect(0, 0, 4, HEADER_H), theme.qcolor(self.track.color))
        # 序号
        p.setPen(QPen(theme.qcolor(theme.TEXT_FAINT), 1))
        p.setFont(QFont("Consolas", 8))
        p.drawText(QRect(6, 8, 16, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{self.index + 1:02d}")
        # 乐器图标
        p.setRenderHint(QPainter.Antialiasing, True)
        draw_glyph(p, self.track.instrument, 34, 15, 8.0, theme.qcolor(theme.TEXT_DIM))

    def mousePressEvent(self, ev):
        self.clicked.emit(self.index)
        super().mousePressEvent(ev)

    def retranslate(self) -> None:
        self.fade_in.setToolTip(tr("TrackHeader", "Fade in (s)"))
        self.fade_out.setToolTip(tr("TrackHeader", "Fade out (s)"))
        self.eq_btn.setToolTip(tr("TrackHeader", "EQ on/off"))
        self.collapse.setToolTip(tr("TrackHeader", "Collapse"))


def _fmt_pan(v: float) -> str:
    if abs(v) < 0.01:
        return "C"
    return f"{'L' if v < 0 else 'R'}{abs(v) * 100:.0f}"


class TrackHeaderColumn(QWidget):
    """220px 列：内部一块 `_inner` 承载所有轨道头，靠移动 `_inner` 做纵向同步。"""

    selectionChanged = Signal(int)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setFixedWidth(HEADER_W)
        self._inner = QWidget(self)
        self._inner.setGeometry(0, 0, HEADER_W, 0)
        self.headers: list[TrackHeader] = []
        self.selected = -1
        self._scroll = 0
        self.rebuild()

    def rebuild(self) -> None:
        for h in self.headers:
            h.setParent(None)
            h.deleteLater()
        self.headers.clear()
        for i, t in enumerate(self.state.project.tracks):
            h = TrackHeader(self.state, i, t, self._inner)
            h.selected = (i == self.selected)
            h.clicked.connect(self._select)
            h.setGeometry(0, i * HEADER_H, HEADER_W, HEADER_H)
            h.show()
            self.headers.append(h)
        self._inner.setGeometry(0, -self._scroll, HEADER_W, max(0, len(self.headers) * HEADER_H))

    def _select(self, idx: int) -> None:
        self.selected = idx
        for i, h in enumerate(self.headers):
            h.selected = (i == idx)
            h.update()
        self.selectionChanged.emit(idx)

    def selected_track(self):
        if 0 <= self.selected < len(self.state.project.tracks):
            return self.state.project.tracks[self.selected]
        return None

    def set_scroll(self, value: int) -> None:
        self._scroll = max(0, int(value))
        self._inner.move(0, -self._scroll)

    def sync_from_model(self) -> None:
        """工程参数变了：把值写回控件（不重建）。"""
        for i, h in enumerate(self.headers):
            if i >= len(self.state.project.tracks):
                break
            t = self.state.project.tracks[i]
            h.track = t
            h.fader.set_value(t.gain_db, emit=False)
            h.pan.set_value(t.pan, emit=False)
            h.eq_low.set_value(t.eq_low_db, emit=False)
            h.eq_high.set_value(t.eq_high_db, emit=False)
            h.mute.setChecked(t.mute)
            h.solo.setChecked(t.solo)
            h.eq_btn.setChecked(t.eq_enabled)
            if h.name.isReadOnly():        # 正在改名时不要覆盖用户输入
                h._show_name(t.name)
            h.update()

    def retranslate(self) -> None:
        for h in self.headers:
            h.retranslate()
