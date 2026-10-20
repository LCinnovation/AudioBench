"""NCM 面板（§3-P0-8）：拖入 → 队列 → 转换 → 失败清单。

合规：首次打开弹一次双语提示，确认后 7 天内不再弹（§8）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from ...app.i18n import tr
from ...ncm import convert as ncm_convert
from ...ncm.convert import NcmJob
from .. import theme

ACK_KEY = "compliance.ncm_ack_ts"
ACK_TTL_S = 7 * 24 * 3600


class ConvertJob(QThread):
    itemDone = Signal(object)
    finishedAll = Signal(list)

    def __init__(self, jobs: list[NcmJob], parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.cancel_evt = threading.Event()

    def cancel(self) -> None:
        self.cancel_evt.set()

    def run(self) -> None:
        ncm_convert.convert_many(self.jobs, on_item=lambda j: self.itemDone.emit(j),
                                 cancel=self.cancel_evt)
        self.finishedAll.emit(self.jobs)


class NcmDialog(QDialog):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.jobs: list[NcmJob] = []
        self.worker: ConvertJob | None = None
        self.setWindowTitle(tr("Ncm", "NCM format conversion"))
        self.setMinimumSize(720, 460)
        self.setAcceptDrops(True)

        lay = QVBoxLayout(self)

        self.hint = QLabel(tr("Ncm", "Drop .ncm files here (local files only)."), self)
        self.hint.setStyleSheet(f"color:{theme.TEXT_DIM};")
        lay.addWidget(self.hint)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Ncm", "Output folder"), self))
        self.out_dir = QLineEdit(str(state.config.get("ncm.dir", "") or Path.home()), self)
        browse = QPushButton("…", self)
        browse.setFixedWidth(32)
        browse.clicked.connect(self._pick_dir)
        row.addWidget(self.out_dir, 1)
        row.addWidget(browse)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr("Ncm", "Output format"), self))
        self.fmt = QComboBox(self)
        for key, spec in ncm_convert.OUT_FORMATS.items():
            self.fmt.addItem(spec["label"], key)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(state.config.get("ncm.fmt", "mp3_320"))))
        row2.addWidget(self.fmt)
        self.add_btn = QPushButton(tr("Ncm", "Add files…"), self)
        self.add_btn.clicked.connect(self._add_dialog)
        row2.addWidget(self.add_btn)
        self.clear_btn = QPushButton(tr("Ncm", "Clear"), self)
        self.clear_btn.clicked.connect(self._clear)
        row2.addWidget(self.clear_btn)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels([tr("Ncm", "File"), tr("Ncm", "Status"),
                                              tr("Ncm", "Message")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.table, 1)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1)
        lay.addWidget(self.progress)

        row3 = QHBoxLayout()
        self.start_btn = QPushButton(tr("Ncm", "Start conversion"), self)
        self.start_btn.clicked.connect(self.start)
        self.csv_btn = QPushButton(tr("Ncm", "Export failures (CSV)"), self)
        self.csv_btn.clicked.connect(self._save_csv)
        close_btn = QPushButton(tr("Ncm", "Close"), self)
        close_btn.clicked.connect(self.accept)
        row3.addWidget(self.start_btn)
        row3.addWidget(self.csv_btn)
        row3.addStretch(1)
        row3.addWidget(close_btn)
        lay.addLayout(row3)

    # ---------------------------------------------------------------- 合规
    def showEvent(self, e):
        super().showEvent(e)
        last = float(self.state.config.get(ACK_KEY, 0) or 0)
        if time.time() - last > ACK_TTL_S:
            box = QMessageBox(self)
            box.setWindowTitle(tr("Ncm", "Before you start"))
            box.setText(tr("Ncm", "This tool only converts NCM files you already have locally. "
                                  "It does not download, search or bypass any paywall."))
            box.setInformativeText(tr("Ncm", "Continue?"))
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.Yes)
            if box.exec() != QMessageBox.Yes:
                self.reject()
                return
            self.state.config.set(ACK_KEY, time.time(), save=True)

    # ---------------------------------------------------------------- 队列
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self.add_paths(paths)
        e.acceptProposedAction()

    def _pick_dir(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start = self.out_dir.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, tr("Ncm", "Output folder"), start)
        if chosen:
            self.out_dir.setText(chosen)
            self.state.config.set("ncm.dir", chosen, save=True)

    def _add_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Ncm", "Add NCM files"),
                                                self.out_dir.text(), "NCM (*.ncm);;All files (*.*)")
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths: list[str]) -> None:
        out_dir = Path(self.out_dir.text() or Path.home())
        for p in paths:
            path = Path(p)
            if path.suffix.lower() != ".ncm":
                continue
            job = NcmJob(src=path, out_dir=out_dir, fmt=self.fmt.currentData())
            self.jobs.append(job)
            self._append_row(job)
        self.progress.setRange(0, max(1, len(self.jobs)))

    def _append_row(self, job: NcmJob) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        item = QTableWidgetItem(job.src.name)
        # 同名文件很常见（不同专辑都有 01.ncm）：行与 job 用对象身份绑定，
        # 不能靠文件名匹配，否则三行会一起更新到第一行
        item.setData(Qt.UserRole, job)
        item.setToolTip(str(job.src))
        self.table.setItem(r, 0, item)
        self.table.setItem(r, 1, QTableWidgetItem(job.status))
        self.table.setItem(r, 2, QTableWidgetItem(job.message))

    def _refresh_row(self, job: NcmJob) -> None:
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is None or item.data(Qt.UserRole) is not job:
                continue
            self.table.setItem(r, 1, QTableWidgetItem(job.status))
            self.table.setItem(r, 2, QTableWidgetItem(job.message or
                                                      (job.out_path.name if job.out_path else "")))
            color = theme.OK if job.status == "done" else (
                theme.ERR if job.status == "failed" else theme.TEXT_DIM)
            self.table.item(r, 1).setForeground(theme.qcolor(color))
            break

    def _clear(self) -> None:
        self.jobs.clear()
        self.table.setRowCount(0)
        self.progress.setValue(0)

    # ---------------------------------------------------------------- 执行
    def start(self) -> None:
        if self.worker is not None or not self.jobs:
            return
        out_dir = Path(self.out_dir.text() or Path.home())
        fmt = self.fmt.currentData()
        self.state.config.set("ncm.dir", str(out_dir))
        self.state.config.set("ncm.fmt", fmt, save=True)
        todo = [j for j in self.jobs if j.status in ("pending", "failed")]
        for j in todo:
            j.out_dir = out_dir
            j.fmt = fmt
            j.status = "pending"
            j.message = ""
            self._refresh_row(j)
        if not todo:
            return
        self.progress.setRange(0, len(todo))
        self.progress.setValue(0)
        self.start_btn.setEnabled(False)
        worker = ConvertJob(todo, self)
        self.worker = worker
        done = {"n": 0}

        def on_item(job) -> None:
            done["n"] += 1
            self.progress.setValue(done["n"])

        worker.itemDone.connect(on_item)
        worker.itemDone.connect(self._refresh_row)
        worker.finishedAll.connect(self._on_finished)
        worker.start()

    def _on_finished(self, jobs) -> None:
        self.worker = None
        self.start_btn.setEnabled(True)
        ok = sum(1 for j in jobs if j.status == "done")
        fail = sum(1 for j in jobs if j.status == "failed")
        self.hint.setText(tr("Ncm", "Done: {ok} succeeded, {fail} failed").format(ok=ok, fail=fail))

    def _save_csv(self) -> None:
        if not any(j.status == "failed" for j in self.jobs):
            QMessageBox.information(self, tr("Ncm", "Nothing failed"), "")
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Ncm", "Save failure list"),
                                              str(Path.home() / "ncm-failures.csv"), "CSV (*.csv)")
        if path:
            Path(path).write_text(ncm_convert.failed_csv(self.jobs), encoding="utf-8-sig")

    def closeEvent(self, e):
        if self.worker is not None:
            self.worker.cancel()
        super().closeEvent(e)


_ = Qt
