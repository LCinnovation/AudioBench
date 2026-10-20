"""帮助面板（§3-P2-12）：四段实操 + 快捷键表，可随时重开。

注意：文案必须写成字面量传给 `tr()`（不要经过变量），否则翻译提取脚本收不到。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from ...app.i18n import tr
from .. import theme


class HelpPanel(QDialog):
    def __init__(self, parent=None, on_replay=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Guide", "Help"))
        self.setMinimumSize(620, 500)
        self._on_replay = on_replay

        lay = QVBoxLayout(self)
        title = QLabel(tr("Guide", "AudioBench in four steps"), self)
        f = title.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        tabs = QTabWidget(self)
        pages = [
            (tr("Guide", "Import audio"),
             tr("Guide",
                "1. Drag audio files onto the timeline, or press Ctrl+I.\n"
                "2. Supported: wav / mp3 / flac / ogg / m4a / aac / wma.\n"
                "3. Each file becomes a track; the colour bar and icon identify it.\n"
                "4. Double-click a track name to rename it.")),
            (tr("Guide", "Loop and slow down"),
             tr("Guide",
                "1. Shift-drag on the ruler to set an A-B loop; double-click to clear.\n"
                "2. Turn on the metronome for a steady click.\n"
                "3. Select a clip and set speed 50–200% (pitch kept) or pitch ±12 semitones "
                "(tempo kept) in the inspector.\n"
                "4. Solo a track (S) to practise along.")),
            (tr("Guide", "Separate stems"),
             tr("Guide",
                "1. Press the stem button in the transport bar.\n"
                "2. Pick the source track (or a file) and choose the stems you need.\n"
                "3. Watch progress and ETA; cancel at any time — no tracks are created.\n"
                "4. On finish press Create tracks: six tracks appear and the source is muted.\n"
                "   The 'other' stem also contains strings.")),
            (tr("Guide", "Export"),
             tr("Guide",
                "1. Press Ctrl+E (or the export button).\n"
                "2. Choose format (mp3 320/192/128, wav 16/24, flac), scope and folder.\n"
                "3. File names follow {track}_{index}_{date}; illegal characters are replaced.\n"
                "4. When it finishes the folder opens with the file selected.")),
        ]
        for name, body in pages:
            page = QWidget()
            pl = QVBoxLayout(page)
            head = QLabel(name, page)
            fh = head.font()
            fh.setBold(True)
            head.setFont(fh)
            pl.addWidget(head)
            text = QTextBrowser(page)
            text.setPlainText(body)
            text.setStyleSheet(
                f"QTextBrowser{{background:{theme.PANEL};border:1px solid {theme.LINE};"
                f"border-radius:4px;color:{theme.TEXT};padding:8px;}}")
            pl.addWidget(text, 1)
            tabs.addTab(page, name)
        lay.addWidget(tabs, 1)

        keys_title = QLabel(tr("Guide", "Keyboard shortcuts"), self)
        fk = keys_title.font()
        fk.setBold(True)
        keys_title.setFont(fk)
        lay.addWidget(keys_title)

        shortcuts = [
            ("Space", tr("Guide", "Play / pause")),
            ("Home", tr("Guide", "Back to start")),
            ("Ctrl+I", tr("Guide", "Import audio")),
            ("Ctrl+S / Ctrl+Shift+S", tr("Guide", "Save / save as")),
            ("Ctrl+Z / Ctrl+Shift+Z", tr("Guide", "Undo / redo")),
            ("Ctrl+E", tr("Guide", "Export audio")),
            ("Del", tr("Guide", "Delete selected clip")),
            ("S", tr("Guide", "Split at playhead")),
            ("Ctrl+D", tr("Guide", "Duplicate clip")),
            ("Ctrl+wheel", tr("Guide", "Zoom timeline")),
            ("Ctrl+M", tr("Guide", "Show / hide mixer")),
            ("Shift+drag on ruler", tr("Guide", "Set A-B loop")),
            ("M", tr("Guide", "Add marker at playhead")),
            ("F1", tr("Guide", "Open this help")),
        ]
        table = QTableWidget(len(shortcuts), 2, self)
        table.setHorizontalHeaderLabels([tr("Guide", "Key"), tr("Guide", "Action")])
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.setMaximumHeight(210)
        for r, (key, action) in enumerate(shortcuts):
            table.setItem(r, 0, QTableWidgetItem(key))
            table.setItem(r, 1, QTableWidgetItem(action))
        lay.addWidget(table)

        row = QHBoxLayout()
        self.replay_btn = QPushButton(tr("Guide", "Replay the guided tour"), self)
        self.replay_btn.clicked.connect(self._replay)
        row.addWidget(self.replay_btn)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(self.reject)
        row.addWidget(buttons)
        lay.addLayout(row)

        hint = QLabel(tr("Guide", "Shortcut customisation is planned; the table above is read-only "
                                 "for now."), self)
        hint.setStyleSheet(f"color:{theme.TEXT_FAINT}; font-size:10px;")
        lay.addWidget(hint)

    def _replay(self) -> None:
        self.accept()
        if callable(self._on_replay):
            self._on_replay()
