"""片段检查器（§3-P0-4 / P0-5）：选中片段后调速度、变调、增益、淡入淡出与分割。

放在走带条与编排区之间，36px 高；没有选中片段时整体禁用。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget,
)

from ..app.i18n import tr
from . import theme


class ClipInspector(QWidget):
    splitRequested = Signal()
    duplicateRequested = Signal()
    deleteRequested = Signal()

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.clip = None
        self.setFixedHeight(36)
        self.setStyleSheet(f"background:{theme.PANEL}; border-top:1px solid {theme.LINE};"
                           f"border-bottom:1px solid {theme.LINE};")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)

        self.title = QLabel(tr("Inspector", "No clip selected"), self)
        self.title.setMinimumWidth(150)
        self.title.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:11px;")
        lay.addWidget(self.title)

        lay.addWidget(QLabel(tr("Inspector", "Speed"), self))
        self.speed = QSpinBox(self)
        self.speed.setRange(50, 200)
        self.speed.setSingleStep(5)
        self.speed.setValue(100)
        self.speed.setSuffix(" %")
        self.speed.setFixedWidth(72)
        self.speed.setToolTip(tr("Inspector", "50–200%, pitch preserved"))
        self.speed.editingFinished.connect(self._apply_speed)
        lay.addWidget(self.speed)

        self.reset_speed = QPushButton(tr("Inspector", "1×"), self)
        self.reset_speed.setFixedWidth(44)
        self.reset_speed.clicked.connect(self._reset_speed)
        lay.addWidget(self.reset_speed)

        lay.addWidget(QLabel(tr("Inspector", "Pitch"), self))
        self.pitch = QDoubleSpinBox(self)
        self.pitch.setRange(-12.0, 12.0)
        self.pitch.setSingleStep(1.0)
        self.pitch.setDecimals(0)
        self.pitch.setSuffix(" st")
        self.pitch.setFixedWidth(66)
        self.pitch.setToolTip(tr("Inspector", "±12 semitones, tempo preserved"))
        self.pitch.editingFinished.connect(self._apply_pitch)
        lay.addWidget(self.pitch)

        lay.addWidget(QLabel(tr("Inspector", "Gain"), self))
        self.gain = QDoubleSpinBox(self)
        self.gain.setRange(-60.0, 12.0)
        self.gain.setDecimals(1)
        self.gain.setSuffix(" dB")
        self.gain.setFixedWidth(76)
        self.gain.editingFinished.connect(self._apply_gain)
        lay.addWidget(self.gain)

        lay.addWidget(QLabel(tr("Inspector", "Fade in/out"), self))
        self.fade_in = QDoubleSpinBox(self)
        self.fade_in.setRange(0.0, 10.0)
        self.fade_in.setSingleStep(0.1)
        self.fade_in.setDecimals(1)
        self.fade_in.setFixedWidth(62)
        self.fade_in.editingFinished.connect(self._apply_fades)
        lay.addWidget(self.fade_in)
        self.fade_out = QDoubleSpinBox(self)
        self.fade_out.setRange(0.0, 10.0)
        self.fade_out.setSingleStep(0.1)
        self.fade_out.setDecimals(1)
        self.fade_out.setFixedWidth(62)
        self.fade_out.editingFinished.connect(self._apply_fades)
        lay.addWidget(self.fade_out)

        self.shape = QComboBox(self)
        self.shape.addItem(tr("Inspector", "Linear"), "linear")
        self.shape.addItem(tr("Inspector", "Exponential"), "exp")
        self.shape.setFixedWidth(96)
        self.shape.currentIndexChanged.connect(self._apply_shape)
        lay.addWidget(self.shape)

        lay.addStretch(1)

        self.btn_split = QPushButton(tr("Inspector", "Split"), self)
        self.btn_dup = QPushButton(tr("Inspector", "Duplicate"), self)
        self.btn_del = QPushButton(tr("Inspector", "Delete"), self)
        for b, sig in ((self.btn_split, self.splitRequested),
                       (self.btn_dup, self.duplicateRequested),
                       (self.btn_del, self.deleteRequested)):
            b.setFixedWidth(78)
            b.clicked.connect(lambda _c=False, s=sig: s.emit())
            lay.addWidget(b)

        self.set_clip(None)

    # ---------------------------------------------------------------- 绑定
    def set_clip(self, clip) -> None:
        self.clip = clip
        enabled = clip is not None
        for w in (self.speed, self.reset_speed, self.pitch, self.gain, self.fade_in,
                  self.fade_out, self.shape, self.btn_split, self.btn_dup, self.btn_del):
            w.setEnabled(enabled)
        if not enabled:
            self.title.setText(tr("Inspector", "No clip selected"))
            return
        self.title.setText(f"{clip.name or clip.path.split('/')[-1]}")
        with _blocked(self.speed, self.pitch, self.gain, self.fade_in, self.fade_out, self.shape):
            self.speed.setValue(int(round(clip.speed * 100)))
            self.pitch.setValue(clip.pitch)
            self.gain.setValue(clip.gain_db)
            sr = max(1, self.state.project.sample_rate)
            self.fade_in.setValue(clip.fade_in / sr)
            self.fade_out.setValue(clip.fade_out / sr)
            self.shape.setCurrentIndex(max(0, self.shape.findData(clip.fade_shape)))

    def refresh(self) -> None:
        self.set_clip(self.clip)

    # ---------------------------------------------------------------- 写回
    def _begin(self) -> None:
        self.state.live_begin("Clip parameter")

    def _end(self) -> None:
        self.state.live_end()

    def _apply_speed(self) -> None:
        if self.clip is None:
            return
        self._begin()
        self.clip.speed = self.speed.value() / 100.0
        self.clip.clamp()
        self.state.live_touch()
        self._end()

    def _reset_speed(self) -> None:
        self.speed.setValue(100)
        self._apply_speed()

    def _apply_pitch(self) -> None:
        if self.clip is None:
            return
        self._begin()
        self.clip.pitch = float(self.pitch.value())
        self.clip.clamp()
        self.state.live_touch()
        self._end()

    def _apply_gain(self) -> None:
        if self.clip is None:
            return
        self._begin()
        self.clip.gain_db = float(self.gain.value())
        self.state.live_touch()
        self._end()

    def _apply_fades(self) -> None:
        if self.clip is None:
            return
        sr = max(1, self.state.project.sample_rate)
        self._begin()
        self.clip.fade_in = int(round(self.fade_in.value() * sr))
        self.clip.fade_out = int(round(self.fade_out.value() * sr))
        self.clip.clamp()
        self.state.live_touch()
        self._end()

    def _apply_shape(self, _index: int) -> None:
        if self.clip is None:
            return
        self._begin()
        self.clip.fade_shape = self.shape.currentData() or "linear"
        self.state.live_touch()
        self._end()

    def retranslate(self) -> None:
        if self.clip is None:
            self.title.setText(tr("Inspector", "No clip selected"))


class _blocked:
    """临时阻断控件信号，避免 set_clip 时把值又写回模型。"""

    def __init__(self, *widgets):
        self.widgets = widgets

    def __enter__(self):
        for w in self.widgets:
            w.blockSignals(True)
        return self

    def __exit__(self, *exc):
        for w in self.widgets:
            w.blockSignals(False)
        return False
