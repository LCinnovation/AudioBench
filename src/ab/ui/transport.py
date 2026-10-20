"""走带条（§4.2）：固定 56px。

左到右：播放 / 停止 / 回起点 / 循环 / 节拍器 / 时间显示 / BPM / 调号 /
         总输出电平 / 主音量 || 右侧：分离、转换、导出、设置、帮助。
M2/M3 才实现的功能按钮此阶段可见但禁用，并给出「M2 提供」的提示。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QSlider, QWidget

from ..app.i18n import tr
from .timefmt import samples_to_bars, samples_to_time
from .widgets import IconButton, Meter
from . import theme


class TimeDisplay(QLabel):
    """双击在「时码 / 小节」之间切换。"""

    def __init__(self, parent=None):
        super().__init__("00:00:00.000", parent)
        self.setFont(theme.mono_font(11))
        self.setStyleSheet(
            f"color:{theme.TEXT}; background:{theme.PANEL}; border:1px solid {theme.LINE};"
            f"border-radius:4px; padding:2px 8px;"
        )
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(132, 26)
        self.bars = False
        self.setToolTip(tr("Transport", "Double-click to switch time / bars"))

    def mouseDoubleClickEvent(self, _e):
        self.bars = not self.bars
        self.update()

    def set_position(self, samples: int, sr: int, bpm: float) -> None:
        self.setText(samples_to_bars(samples, sr, bpm) if self.bars
                     else samples_to_time(samples, sr))


class TransportBar(QWidget):
    playClicked = Signal()
    stopClicked = Signal()
    rewindClicked = Signal()
    loopToggled = Signal(bool)
    metronomeToggled = Signal(bool)
    masterDbChanged = Signal(float)
    featureClicked = Signal(str)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setFixedHeight(56)
        self.setObjectName("TransportBar")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)

        self.btn_play = IconButton("play", tooltip=tr("Transport", "Play / pause (Space)"))
        self.btn_stop = IconButton("stop", tooltip=tr("Transport", "Stop"))
        self.btn_rewind = IconButton("rewind", tooltip=tr("Transport", "Go to start"))
        self.btn_loop = IconButton("loop", tooltip=tr("Transport", "A-B loop"), checkable=True)
        self.btn_metro = IconButton("metronome", tooltip=tr("Transport", "Metronome"), checkable=True)
        self.btn_play.clicked.connect(lambda _v: self.playClicked.emit())
        self.btn_stop.clicked.connect(lambda _v: self.stopClicked.emit())
        self.btn_rewind.clicked.connect(lambda _v: self.rewindClicked.emit())
        self.btn_loop.clicked.connect(lambda v: self.loopToggled.emit(v))
        self.btn_metro.clicked.connect(lambda v: self.metronomeToggled.emit(v))

        for b in (self.btn_play, self.btn_stop, self.btn_rewind, self.btn_loop, self.btn_metro):
            lay.addWidget(b)
        lay.addSpacing(4)

        self.time = TimeDisplay(self)
        lay.addWidget(self.time)

        self.bpm = QLineEdit("120", self)
        self.bpm.setFixedSize(52, 26)
        self.bpm.setAlignment(Qt.AlignCenter)
        self.bpm.setToolTip(tr("Transport", "BPM"))
        lay.addWidget(self.bpm)

        self.key = QLabel(tr("Transport", "C maj"), self)
        self.key.setStyleSheet(f"color:{theme.TEXT_DIM};")
        self.key.setFixedWidth(48)
        lay.addWidget(self.key)

        self.meter = Meter(self, horizontal=True, height=10)
        self.meter.setFixedWidth(96)
        lay.addWidget(self.meter)

        self.vol = QSlider(Qt.Horizontal, self)
        self.vol.setRange(-600, 120)
        self.vol.setValue(0)
        self.vol.setFixedWidth(96)
        self.vol.setToolTip(tr("Transport", "Master volume"))
        self.vol.valueChanged.connect(lambda v: self.masterDbChanged.emit(v / 10.0))
        lay.addWidget(self.vol)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(spacer)

        self.feature_buttons: dict[str, IconButton] = {}
        for key, glyph, tip, enabled in (
            ("separate", "other", tr("Transport", "Separate stems (M3)"), False),
            ("convert", "settings", tr("Transport", "Format convert (M2)"), False),
            ("export", "export", tr("Transport", "Export (M2)"), False),
            ("settings", "settings", tr("Transport", "Settings"), True),
            ("help", "info", tr("Transport", "Help"), True),
        ):
            b = IconButton(glyph, tooltip=tip)
            b.setEnabled(enabled)
            b.clicked.connect(lambda _v, k=key: self.featureClicked.emit(k))
            self.feature_buttons[key] = b
            lay.addWidget(b)

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---------------------------------------------------------------- 刷新
    def _tick(self) -> None:
        st = self.state
        pos = st.position()
        sr = st.project.sample_rate
        try:
            bpm = float(self.bpm.text() or 120)
        except ValueError:
            bpm = 120.0
        self.time.set_position(pos, sr, bpm)
        if st.engine is not None:
            pl, pr, rl, rr = st.engine.levels()
            self.meter.set_peak(pl, pr)
        self.btn_play.setChecked(bool(st.engine and st.engine.playing))

    def set_bpm(self, bpm: float) -> None:
        self.bpm.setText(f"{bpm:g}")

    def current_bpm(self) -> float:
        try:
            return max(20.0, min(300.0, float(self.bpm.text() or 120)))
        except ValueError:
            return 120.0

    def retranslate(self) -> None:
        self.btn_play.setToolTip(tr("Transport", "Play / pause (Space)"))
        self.btn_stop.setToolTip(tr("Transport", "Stop"))
        self.btn_rewind.setToolTip(tr("Transport", "Go to start"))
        self.btn_loop.setToolTip(tr("Transport", "A-B loop"))
        self.btn_metro.setToolTip(tr("Transport", "Metronome"))
        self.bpm.setToolTip(tr("Transport", "BPM"))
        self.vol.setToolTip(tr("Transport", "Master volume"))
        self.key.setText(tr("Transport", "C maj"))
        for key, tip in (
            ("separate", tr("Transport", "Separate stems (M3)")),
            ("convert", tr("Transport", "Format convert (M2)")),
            ("export", tr("Transport", "Export (M2)")),
            ("settings", tr("Transport", "Settings")),
            ("help", tr("Transport", "Help")),
        ):
            self.feature_buttons[key].setToolTip(tip)
