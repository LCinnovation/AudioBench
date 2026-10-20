"""导出对话框（§3-P0-6）：格式 / 范围 / 目录 / 命名模板 / 完成打开目录。

导出在后台线程跑，UI 显示进度；取消用 threading.Event。
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from ...app.i18n import tr
from ...io import exporter
from ...io.exporter import FORMATS, ExportSettings
from .. import theme


class ExportJob(QThread):
    progress = Signal(float)
    done = Signal(object)      # ExportResult
    failed = Signal(str)

    def __init__(self, project, resolve, settings: ExportSettings, track_index, parent=None,
                 master_gain_db: float = 0.0):
        super().__init__(parent)
        self.project = project
        self.resolve = resolve
        self.settings = settings
        self.track_index = track_index
        self.master_gain_db = float(master_gain_db)
        self.cancel_evt = threading.Event()

    def cancel(self) -> None:
        self.cancel_evt.set()

    def run(self) -> None:
        try:
            res = exporter.export(self.project, self.resolve, self.settings,
                                  track_index=self.track_index,
                                  master_gain_db=self.master_gain_db,
                                  progress=lambda p: self.progress.emit(float(p)),
                                  cancel=self.cancel_evt)
            self.done.emit(res)
        except Exception as e:                       # 任何异常都变成可读失败
            self.failed.emit(str(e))


class ExportDialog(QDialog):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.job: ExportJob | None = None
        self.setWindowTitle(tr("Export", "Export audio"))
        self.setMinimumWidth(460)

        form = QFormLayout(self)

        self.fmt = QComboBox(self)
        for key, spec in FORMATS.items():
            self.fmt.addItem(spec["label"], key)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(
            state.config.get("export.fmt", "mp3_320"))))
        form.addRow(tr("Export", "Format"), self.fmt)

        self.scope = QComboBox(self)
        self.scope.addItem(tr("Export", "Mix (all tracks)"), "mix")
        self.scope.addItem(tr("Export", "Current track only"), "track")
        form.addRow(tr("Export", "Scope"), self.scope)

        row = QHBoxLayout()
        self.out_dir = QLineEdit(str(state.config.get("export.dir", "") or Path.home()), self)
        browse = QPushButton("…", self)
        browse.setFixedWidth(32)
        browse.clicked.connect(self._pick_dir)
        row.addWidget(self.out_dir, 1)
        row.addWidget(browse)
        form.addRow(tr("Export", "Folder"), row)

        self.template = QLineEdit(str(state.config.get("export.template",
                                                       "{track}_{index}_{date}")), self)
        self.template.setToolTip(tr("Export", "Available fields: {track} {index} {date}"))
        form.addRow(tr("Export", "File name template"), self.template)

        self.open_after = QCheckBox(tr("Export", "Open folder when finished"), self)
        self.open_after.setChecked(bool(state.config.get("export.open_after", True)))
        form.addRow("", self.open_after)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        form.addRow("", self.progress)

        self.status = QLabel("", self)
        self.status.setStyleSheet(f"color:{theme.TEXT_DIM};")
        form.addRow("", self.status)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("Export", "Start export"))
        self.buttons.accepted.connect(self.start)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)

    # ---------------------------------------------------------------- 交互
    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Export", "Choose folder"),
                                             self.out_dir.text() or str(Path.home()))
        if d:
            self.out_dir.setText(d)

    def _track_index(self) -> int | None:
        idx = self.state.headers_selected_track() if hasattr(self.state, "headers_selected_track") else None
        return idx

    def start(self) -> None:
        out_dir = Path(self.out_dir.text() or Path.home())
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, tr("Export", "Export failed"), str(e))
            return
        settings = ExportSettings(
            fmt=self.fmt.currentData(),
            out_dir=out_dir,
            template=self.template.text() or "{track}_{index}_{date}",
            scope=self.scope.currentData(),
        )
        self.state.config.set("export.fmt", settings.fmt)
        self.state.config.set("export.dir", str(out_dir))
        self.state.config.set("export.template", settings.template)
        self.state.config.set("export.open_after", self.open_after.isChecked(), save=True)

        track_index = None
        if settings.scope == "track":
            track_index = getattr(self.state, "selected_track_index", lambda: -1)()
            if track_index is None or track_index < 0:
                QMessageBox.information(self, tr("Export", "Export audio"),
                                        tr("Export", "Select a track first"))
                return
        if not self.state.project.tracks:
            QMessageBox.information(self, tr("Export", "Export audio"),
                                    tr("Export", "Nothing to export"))
            return

        peak = exporter.peak_scan(self.state.project, self.state.export_resolve,
                                  master_gain=exporter.gain_from_db(
                                      getattr(self.state, "master_gain_db", 0.0)))
        if peak > 1.0:
            r = QMessageBox.question(
                self, tr("Export", "Possible clipping"),
                tr("Export", "Peak level is above 0 dBFS ({peak:.2f}). Export anyway?").format(peak=peak),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return

        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.status.setText(tr("Export", "Rendering…"))
        job = ExportJob(self.state.project, self.state.export_resolve, settings, track_index, self,
                        master_gain_db=getattr(self.state, "master_gain_db", 0.0))
        self.job = job
        job.progress.connect(self._on_progress)
        job.done.connect(self._on_done)
        job.failed.connect(self._on_failed)
        job.finished.connect(self._on_finished)
        job.start()

    def _on_progress(self, p: float) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, p)) * 1000))

    def _on_done(self, res) -> None:
        self.progress.setValue(1000)
        names = ", ".join(p.name for p in res.files)
        self.status.setText(tr("Export", "Done: {name}  ({sec:.1f}s)").format(
            name=names, sec=res.seconds))
        if res.clipped:
            self.status.setText(self.status.text() + "  ⚠ " + tr("Export", "clipping detected"))
        warnings = getattr(res, "warnings", None)
        if warnings:
            self.status.setText(self.status.text() + "  ⚠ " + "; ".join(warnings))
        if self.open_after.isChecked():
            self._reveal(res.files[0] if res.files else None)

    @staticmethod
    def _reveal(path: Path | None) -> None:
        if path is None:
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", str(path)],
                                 creationflags=0x08000000)
            else:
                subprocess.Popen(["open", "-R", str(path)])
        except Exception:
            try:
                os.startfile(str(path.parent))  # type: ignore[attr-defined]
            except Exception:
                pass

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        QMessageBox.critical(self, tr("Export", "Export failed"), message)

    def _on_finished(self) -> None:
        self.job = None
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)

    def closeEvent(self, e):
        if self.job is not None:
            self.job.cancel()
        super().closeEvent(e)
