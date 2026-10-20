"""混音器（§4.5）：每轨一条 72px 通道条 + 最右主通道条，可整体收起。

与轨道头**同一份 Model**：值只从 `ProjectModel` 读，改动也只写回它，
`paramsChanged` 到来时用 `set_value(..., emit=False)` 同步，不会互相打环。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..app.i18n import tr
from . import theme
from .widgets import Fader, Knob, Meter

STRIP_W = 72
MIXER_H = 180


class ChannelStrip(QWidget):
    def __init__(self, state, index: int, parent=None):
        super().__init__(parent)
        self.state = state
        self.index = index
        self.setFixedSize(STRIP_W, MIXER_H)

        self.name = QLabel(self)
        self.name.setGeometry(4, 4, STRIP_W - 8, 16)
        self.name.setStyleSheet(f"color:{theme.TEXT}; font-size:10px;")
        self.name.setAlignment(Qt.AlignCenter)

        self.pan = Knob(-1.0, 1.0, 0.0, self, formatter=_fmt_pan)
        self.pan.setGeometry((STRIP_W - 26) // 2, 22, 26, 26)
        self.pan.valueChanged.connect(self._on_pan)
        self.pan.editStarted.connect(lambda: state.live_begin("Mixer pan"))
        self.pan.editFinished.connect(state.live_end)

        self.low = Knob(-12.0, 12.0, 0.0, self, formatter=lambda v: f"{v:+.1f} dB")
        self.low.setGeometry(9, 50, 26, 26)
        self.low.valueChanged.connect(self._on_low)
        self.low.editStarted.connect(lambda: state.live_begin("Mixer EQ"))
        self.low.editFinished.connect(state.live_end)

        self.high = Knob(-12.0, 12.0, 0.0, self, formatter=lambda v: f"{v:+.1f} dB")
        self.high.setGeometry(37, 50, 26, 26)
        self.high.valueChanged.connect(self._on_high)
        self.high.editStarted.connect(lambda: state.live_begin("Mixer EQ"))
        self.high.editFinished.connect(state.live_end)

        self.fader = Fader(-60.0, 12.0, 0.0, self)
        self.fader.setGeometry(18, 80, 12, 78)
        self.fader.valueChanged.connect(self._on_gain)
        self.fader.editStarted.connect(lambda: state.live_begin("Mixer volume"))
        self.fader.editFinished.connect(state.live_end)

        self.meter = Meter(self)
        self.meter.setGeometry(34, 80, 8, 78)

        self.mute = _small_btn("M", self, 6, 162)
        self.mute.clicked.connect(self._on_mute)
        self.solo = _small_btn("S", self, STRIP_W - 26, 162)
        self.solo.clicked.connect(self._on_solo)

    # ---------------------------------------------------------------- 同步
    def load(self, track) -> None:
        self.name.setText(track.name)
        self.name.setToolTip(track.name)
        self.name.setStyleSheet(f"color:{track.color}; font-size:10px;")
        self.pan.set_value(track.pan, emit=False)
        self.low.set_value(track.eq_low_db, emit=False)
        self.high.set_value(track.eq_high_db, emit=False)
        self.fader.set_value(track.gain_db, emit=False)
        self.mute.setChecked(track.mute)
        self.solo.setChecked(track.solo)

    def tick_levels(self, engine) -> None:
        if engine is None:
            return
        pk = engine.mixer.peak if engine.mixer else None
        if pk is not None:
            self.meter.set_peak(float(pk[0]), float(pk[1]))

    # ---------------------------------------------------------------- 写回
    def _track(self):
        if 0 <= self.index < len(self.state.project.tracks):
            return self.state.project.tracks[self.index]
        return None

    def _on_pan(self, v: float) -> None:
        t = self._track()
        if t is not None:
            t.pan = v
            self.pan.setToolTip(_fmt_pan(v))
            self.state.live_touch()

    def _on_low(self, v: float) -> None:
        t = self._track()
        if t is not None:
            t.eq_low_db = v
            self.state.live_touch()

    def _on_high(self, v: float) -> None:
        t = self._track()
        if t is not None:
            t.eq_high_db = v
            self.state.live_touch()

    def _on_gain(self, db: float) -> None:
        t = self._track()
        if t is not None:
            t.gain_db = db
            self.state.live_touch()

    def _on_mute(self) -> None:
        t = self._track()
        if t is not None:
            with self.state.edit("Mute"):
                t.mute = self.mute.isChecked()

    def _on_solo(self) -> None:
        t = self._track()
        if t is not None:
            with self.state.edit("Solo"):
                t.solo = self.solo.isChecked()


class MasterStrip(QWidget):
    masterChanged = Signal(float)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setFixedSize(STRIP_W, MIXER_H)
        self.setStyleSheet(f"background:{theme.PANEL_ALT};")
        label = QLabel(tr("Mixer", "Master"), self)
        label.setGeometry(4, 4, STRIP_W - 8, 16)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(f"color:{theme.SELECT}; font-size:10px;")
        self.fader = Fader(-60.0, 12.0, 0.0, self)
        self.fader.setGeometry(18, 40, 12, 118)
        self.fader.valueChanged.connect(self.masterChanged)
        self.fader.editStarted.connect(lambda: state.live_begin("Master volume"))
        self.fader.editFinished.connect(state.live_end)
        self.meter = Meter(self)
        self.meter.setGeometry(34, 40, 8, 118)


def _small_btn(text: str, parent, x: int, y: int) -> QPushButton:
    b = QPushButton(text, parent)
    b.setGeometry(x, y, 20, 16)
    b.setCheckable(True)
    b.setFocusPolicy(Qt.NoFocus)
    b.setStyleSheet(
        f"QPushButton{{padding:0;border:1px solid {theme.LINE};border-radius:3px;"
        f"color:{theme.TEXT_DIM};background:{theme.PANEL};font-size:9px;}}"
        f"QPushButton:checked{{background:{theme.SELECT};color:{theme.BG};border-color:{theme.SELECT};}}"
    )
    return b


class MixerPanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setFixedHeight(MIXER_H)
        self.setObjectName("MixerPanel")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._inner = QWidget()
        self._inner.setFixedHeight(MIXER_H)
        self._lay = QHBoxLayout(self._inner)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(1)
        self._lay.addStretch(1)
        self.scroll.setWidget(self._inner)
        lay.addWidget(self.scroll, 1)

        self.master = MasterStrip(state, self)
        lay.addWidget(self.master)

        self.strips: list[ChannelStrip] = []
        self.rebuild()

    def rebuild(self) -> None:
        for s in self.strips:
            s.setParent(None)
            s.deleteLater()
        self.strips.clear()
        for i, t in enumerate(self.state.project.tracks):
            s = ChannelStrip(self.state, i, self._inner)
            s.load(t)
            self._lay.insertWidget(i, s)
            self.strips.append(s)
        self._inner.setFixedWidth(max(1, len(self.strips)) * (STRIP_W + 1))

    def sync_from_model(self) -> None:
        for i, s in enumerate(self.strips):
            if i < len(self.state.project.tracks):
                s.load(self.state.project.tracks[i])

    def tick(self) -> None:
        eng = self.state.engine
        for s in self.strips:
            s.tick_levels(eng)
        if eng is not None and eng.mixer is not None:
            self.master.meter.set_peak(float(eng.mixer.peak[0]), float(eng.mixer.peak[1]))

    def retranslate(self) -> None:
        pass


def _fmt_pan(v: float) -> str:
    if abs(v) < 0.01:
        return "C"
    return f"{'L' if v < 0 else 'R'}{abs(v) * 100:.0f}"
