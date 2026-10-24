"""帮助面板：直接展示随包携带的图文教程（HTML），按当前语言选文件。

把长文案放进 `assets/guide/guide_<lang>.html` 而不是写在代码里，原因：
  1. 教程有层级、列表、加粗、表格，QTextBrowser 的富文本比纯文本好读得多；
  2. 两种语言各一份文件，不经过 .ts 翻译管线，改文案不用重新生成 .qm；
  3. 同一份内容可以同时导出成 Markdown 教程给用户看。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ...app.i18n import tr
from ...app.paths import asset_dir
from .. import theme


def guide_path(lang: str) -> Path:
    base = asset_dir() / "guide"
    p = base / f"guide_{lang}.html"
    if p.exists():
        return p
    fallback = base / "guide_en_US.html"
    return fallback if fallback.exists() else p


def guide_html(lang: str) -> str:
    p = guide_path(lang)
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ("<h1>AudioBench</h1><p>Guide file is missing.</p>")


class HelpPanel(QDialog):
    def __init__(self, parent=None, on_replay=None, lang: str = "zh_CN"):
        super().__init__(parent)
        self.setWindowTitle(tr("Guide", "Help"))
        self.setMinimumSize(820, 680)
        self._on_replay = on_replay

        lay = QVBoxLayout(self)
        title = QLabel(tr("Guide", "Beginner's guide"), self)
        f = title.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        self.view = QTextBrowser(self)
        self.view.setOpenExternalLinks(True)
        self.view.setStyleSheet(
            f"QTextBrowser{{background:{theme.PANEL};border:1px solid {theme.LINE};"
            f"border-radius:6px;color:{theme.TEXT};padding:14px;}}")
        self.view.document().setDefaultStyleSheet(
            f"h1{{font-size:20px;color:{theme.TEXT};margin-bottom:6px;}}"
            f"h2{{font-size:16px;color:{theme.SELECT};margin-top:18px;}}"
            f"h3{{font-size:13px;color:{theme.TEXT};margin-top:12px;}}"
            f"p,li{{font-size:12px;line-height:150%;}}"
            f"code{{background:{theme.PANEL_ALT};padding:1px 4px;}}"
            f"table{{border-collapse:collapse;}}"
            f"td,th{{border:1px solid {theme.LINE};padding:4px 8px;font-size:12px;}}"
            f".note{{color:{theme.TEXT_DIM};}}"
            f".warn{{color:{theme.ERR};}}"
            f".ok{{color:{theme.OK};}}")
        self.view.setHtml(guide_html(lang))
        self.view.setSearchPaths([str(guide_path(lang).parent)])
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        self.replay_btn = QPushButton(tr("Guide", "Replay the guided tour"), self)
        self.replay_btn.clicked.connect(self._replay)
        row.addWidget(self.replay_btn)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        # Qt 自带的中文翻译没随包发布，标准按钮会显示英文，这里手动命名
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.setText(tr("Guide", "Close"))
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(self.reject)
        row.addWidget(buttons)
        lay.addLayout(row)
        _ = QUrl, QWidget

    def _replay(self) -> None:
        self.accept()
        if callable(self._on_replay):
            self._on_replay()
