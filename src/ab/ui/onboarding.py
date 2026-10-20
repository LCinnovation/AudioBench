"""新手引导（§3-P2-12）：5 步浮层 + 可重开的帮助面板。

设计要点：
  * 浮层是主窗口的子控件，不建第二套窗口，CrossOver 下更稳；
  * 每一步只做一件事：高亮一个控件、说一句话、给一个「继续」；
  * 「跳过」写入 config.onboarding.skipped，之后再也不自动弹（帮助面板仍可手动重开）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..app.i18n import tr
from . import theme


@dataclass
class Step:
    title: str
    body: str
    target: Callable[[], QWidget | None]   # 高亮哪个控件；None = 居中说明
    hint: str = ""


class OnboardingOverlay(QWidget):
    finished = Signal(bool)     # True=走完, False=跳过

    CARD_W = 420

    def __init__(self, parent: QWidget, steps: list[Step], parent_window=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.steps = steps
        self.index = 0
        self._win = parent_window or parent.window()

        self.card = QWidget(self)
        self.card.setObjectName("OnboardingCard")
        self.card.setStyleSheet(
            f"#OnboardingCard{{background:{theme.PANEL_ALT};border:1px solid {theme.LINE};"
            f"border-radius:8px;}}"
            f"QLabel{{color:{theme.TEXT};}}"
        )
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)

        self.step_label = QLabel("", self.card)
        self.step_label.setStyleSheet(f"color:{theme.SELECT}; font-size:11px;")
        lay.addWidget(self.step_label)

        self.title_label = QLabel("", self.card)
        f = QFont(theme.app_family(), 13)
        f.setBold(True)
        self.title_label.setFont(f)
        lay.addWidget(self.title_label)

        self.body_label = QLabel("", self.card)
        self.body_label.setWordWrap(True)
        self.body_label.setMinimumHeight(52)
        self.body_label.setStyleSheet(f"color:{theme.TEXT_DIM};")
        lay.addWidget(self.body_label)

        self.hint_label = QLabel("", self.card)
        self.hint_label.setStyleSheet(f"color:{theme.TEXT_FAINT}; font-size:10px;")
        self.hint_label.setWordWrap(True)
        lay.addWidget(self.hint_label)

        row = QHBoxLayout()
        self.skip_btn = QPushButton(tr("Guide", "Skip"), self.card)
        self.skip_btn.clicked.connect(self._skip)
        self.prev_btn = QPushButton(tr("Guide", "Back"), self.card)
        self.prev_btn.clicked.connect(self._prev)
        self.next_btn = QPushButton(tr("Guide", "Next"), self.card)
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._next)
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        row.addWidget(self.prev_btn)
        row.addWidget(self.next_btn)
        lay.addLayout(row)

        self._target_rect = QRect()
        self._apply_step()

    # ---------------------------------------------------------------- 步骤
    def _apply_step(self) -> None:
        step = self.steps[self.index]
        self.step_label.setText(tr("Guide", "Step {i} / {n}").format(
            i=self.index + 1, n=len(self.steps)))
        self.title_label.setText(step.title)
        self.body_label.setText(step.body)
        self.hint_label.setText(step.hint)
        self.hint_label.setVisible(bool(step.hint))
        self.prev_btn.setEnabled(self.index > 0)
        self.next_btn.setText(tr("Guide", "Done") if self.index == len(self.steps) - 1
                              else tr("Guide", "Next"))
        self._locate()

    def _locate(self) -> None:
        """算出目标控件在浮层坐标系里的位置，并把卡片摆在它旁边。"""
        step = self.steps[self.index]
        widget = step.target() if step.target else None
        self._target_rect = QRect()
        if widget is not None:
            try:
                # 用全局坐标换算，比 mapTo 跨 splitter 时可靠
                top_left = self.mapFromGlobal(widget.mapToGlobal(QPoint(0, 0)))
                rect = QRect(top_left, widget.size())
                # 只高亮浮层里看得见的部分（目标可能是滚动区里的大画布）
                self._target_rect = rect.intersected(self.rect().adjusted(4, 4, -4, -4))
            except Exception:
                self._target_rect = QRect()

        self.card.setFixedWidth(self.CARD_W)
        self.card.adjustSize()
        h = self.card.sizeHint().height()
        self.card.setFixedHeight(h)

        if self._target_rect.isValid() and self._target_rect.height() > 0:
            below = self._target_rect.bottom() + 14
            above = self._target_rect.top() - h - 14
            y = below if below + h <= self.height() - 10 else (above if above >= 10 else
                                                               self.height() - h - 12)
            x = min(max(10, self._target_rect.left()), self.width() - self.CARD_W - 10)
        else:
            x = (self.width() - self.CARD_W) // 2
            y = (self.height() - h) // 2

        card_rect = QRect(int(x), int(y), self.CARD_W, int(h))
        # 卡片别压在「聚光灯」上，否则用户看不全被指的控件
        if self._target_rect.isValid() and card_rect.intersects(self._target_rect):
            fallback_y = self.height() - h - 12
            if not QRect(int(x), int(fallback_y), self.CARD_W, int(h)).intersects(self._target_rect):
                card_rect.moveTop(int(fallback_y))
            else:
                card_rect.moveLeft(10)
                card_rect.moveTop(int(fallback_y))
        self.card.setGeometry(card_rect)
        self.card.raise_()
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._locate()

    # ---------------------------------------------------------------- 控制
    def _next(self) -> None:
        if self.index >= len(self.steps) - 1:
            self._finish(True)
            return
        self.index += 1
        self._apply_step()

    def _prev(self) -> None:
        if self.index > 0:
            self.index -= 1
            self._apply_step()

    def _skip(self) -> None:
        self._finish(False)

    def _finish(self, completed: bool) -> None:
        self.finished.emit(completed)
        self.close()
        self.deleteLater()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Right, Qt.Key_Space, Qt.Key_Return):
            self._next()
        elif e.key() == Qt.Key_Left:
            self._prev()
        elif e.key() == Qt.Key_Escape:
            self._skip()
        else:
            super().keyPressEvent(e)

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        fill = QColor(0, 0, 0, 170)
        if self._target_rect.isValid():
            # 整屏压暗，再把目标区域「挖」出来
            p.fillRect(self.rect(), fill)
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(self._target_rect, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(theme.qcolor(theme.SELECT), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self._target_rect, 6, 6)
        else:
            p.fillRect(self.rect(), fill)
