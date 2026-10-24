"""分离对话框（§3-P1-9）：选源 → 预估 → 跑 worker → 进度/ETA/取消 → 一键建轨。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ...app.i18n import tr
from ...sep import client as sep_client
from ...sep import models as sep_models
from .. import theme

log = logging.getLogger("ab.ui.sep")

DISPLAY = {
    "vocals": ("Vocals", "人声"),
    "drums": ("Drums", "鼓"),
    "bass": ("Bass", "贝斯"),
    "guitar": ("Guitar", "吉他"),
    "piano": ("Piano", "钢琴"),
    "other": ("Other (incl. strings)", "其他（含弦乐）"),
}


def stem_label(stem: str) -> str:
    """分轨的中文名（小白用户看不懂 Drums/Bass 这类英文）。

    注意每个 tr() 的第一个参数必须是字面量，翻译提取脚本才收得到。
    """
    labels = {
        "vocals": tr("Stem", "Vocals"),
        "drums": tr("Stem", "Drums"),
        "bass": tr("Stem", "Bass"),
        "guitar": tr("Stem", "Guitar"),
        "piano": tr("Stem", "Piano"),
        "other": tr("Stem", "Other (incl. strings)"),
    }
    return labels.get(stem, stem)


class SeparationDialog(QDialog):
    stemsCreated = Signal(dict)          # {"dir":..., "stems":[{name,path}], "source": path}

    def __init__(self, state, parent=None, *, track_index: int | None = None):
        super().__init__(parent)
        self.state = state
        self.job: sep_client.SeparationJob | None = None
        self.result: dict | None = None
        self._t0 = 0.0
        self.setWindowTitle(tr("Sep", "Separate stems"))
        self.setMinimumWidth(520)

        lay = QVBoxLayout(self)

        # 模型选择：4 轨（重建≈原曲）vs 6 轨（含吉他/钢琴，但重建损失约 18%）
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel(tr("Sep", "Model"), self))
        self.model_combo = QComboBox(self)
        for mid, spec in sep_models.MODELS.items():
            info = sep_models.describe(mid)
            zh = spec.get("label_zh") or spec["label"]
            text = f"{zh} · {info['size_mb']:.0f} MB" if info["present"] else f"{zh} · ×"
            self.model_combo.addItem(text, mid)
            if not info["present"]:
                idx = self.model_combo.count() - 1
                self.model_combo.setItemData(idx, f"missing: {info['path']}", Qt.ToolTipRole)
        want = str(state.config.get("sep.model", sep_models.DEFAULT_MODEL))
        pos = self.model_combo.findData(want)
        self.model_combo.setCurrentIndex(pos if pos >= 0 else 0)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        mrow.addWidget(self.model_combo, 1)
        lay.addLayout(mrow)

        self.model_label = QLabel("", self)
        self.model_label.setStyleSheet(f"color:{theme.TEXT_DIM};")
        self.model_label.setWordWrap(True)
        lay.addWidget(self.model_label)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Sep", "Source track"), self))
        self.source = QComboBox(self)
        self._fill_sources(track_index)
        row.addWidget(self.source, 1)
        browse = QPushButton(tr("Sep", "Choose file…"), self)
        browse.clicked.connect(self._pick_file)
        row.addWidget(browse)
        lay.addLayout(row)

        stem_box = QWidget(self)
        self._stem_grid = QGridLayout(stem_box)
        self._stem_grid.setContentsMargins(0, 0, 0, 0)
        self._stem_grid.addWidget(QLabel(tr("Sep", "Stems"), stem_box), 0, 0, 1, 3)
        self.stem_boxes: dict[str, QCheckBox] = {}
        lay.addWidget(stem_box)
        self._rebuild_stems()
        self._refresh_model_label()

        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr("Sep", "CPU threads"), self))
        self.threads = QSpinBox(self)
        self.threads.setRange(1, 32)
        self.threads.setValue(self._default_threads())
        row2.addWidget(self.threads)
        self.estimate = QLabel("", self)
        self.estimate.setStyleSheet(f"color:{theme.TEXT_DIM};")
        row2.addWidget(self.estimate, 1)
        lay.addLayout(row2)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1000)
        lay.addWidget(self.progress)

        self.status = QLabel(tr("Sep", "Ready"), self)
        self.status.setStyleSheet(f"color:{theme.TEXT_DIM};")
        lay.addWidget(self.status)

        row3 = QHBoxLayout()
        self.start_btn = QPushButton(tr("Sep", "Start"), self)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton(tr("Sep", "Cancel"), self)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        self.create_btn = QPushButton(tr("Sep", "Create tracks"), self)
        self.create_btn.setEnabled(False)
        self.create_btn.clicked.connect(self._create)
        close_btn = QPushButton(tr("Sep", "Close"), self)
        close_btn.clicked.connect(self.reject)
        for b in (self.start_btn, self.cancel_btn, self.create_btn):
            row3.addWidget(b)
        row3.addStretch(1)
        row3.addWidget(close_btn)
        lay.addLayout(row3)

        self.source.currentIndexChanged.connect(self._update_estimate)
        self._update_estimate()

    # ---------------------------------------------------------------- 源
    def _fill_sources(self, track_index: int | None) -> None:
        self.source.clear()
        for i, t in enumerate(self.state.project.tracks):
            if not t.clips:
                continue
            # data = (音频路径, 轨道序号或 None)：建轨时要用序号静音源轨、用名字命名新轨
            self.source.addItem(f"{i + 1:02d}  {t.name}", (t.clips[0].path, i))
        if track_index is not None and 0 <= track_index < self.source.count():
            self.source.setCurrentIndex(track_index)

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Sep", "Choose file"),
                                              str(Path.home()),
                                              "Audio (*.wav *.mp3 *.flac *.m4a *.ogg)")
        if path:
            self.source.addItem(f"{Path(path).name}", (path, None))
            self.source.setCurrentIndex(self.source.count() - 1)
            self._update_estimate()

    def _selected_source(self) -> tuple[str, int | None]:
        data = self.source.currentData()
        if isinstance(data, tuple):
            return data[0], data[1]
        return (data or ""), None

    def _default_threads(self) -> int:
        try:
            import os

            cores = os.cpu_count() or 8
            return max(2, min(8, cores - 2))
        except Exception:
            return 8

    def _update_estimate(self) -> None:
        path, _idx = self._selected_source()
        if not path:
            self.estimate.setText(tr("Sep", "No audio selected"))
            self.start_btn.setEnabled(False)
            return
        dur = self._duration_of(path)
        eta = sep_client.estimate_seconds(dur, threads=self.threads.value())
        self.estimate.setText(tr("Sep", "~{eta:.0f}s for {dur:.0f}s audio").format(
            eta=eta, dur=dur))
        self.start_btn.setEnabled(self.job is None)

    def _duration_of(self, path: str) -> float:
        try:
            from ...io.decoder import DecodeService

            dec = self.state.decode.lookup(path, self.state.project.sample_rate)
            if dec is not None:
                return dec.frames / max(1, dec.sample_rate)
            from ...io import ffmpeg

            return ffmpeg.probe(path).duration_s
        except Exception:
            return 180.0

    def _current_model(self) -> str:
        return str(self.model_combo.currentData() or sep_models.DEFAULT_MODEL)

    def _rebuild_stems(self) -> None:
        """切换模型时重建勾选项：4 轨模型没有 guitar/piano。"""
        for box in self.stem_boxes.values():
            self._stem_grid.removeWidget(box)
            box.deleteLater()
        self.stem_boxes.clear()
        for i, s in enumerate(sep_models.stems_for(self._current_model())):
            label = stem_label(s)
            box = QCheckBox(label, self)
            box.setChecked(True)
            box.toggled.connect(self._update_estimate)
            self.stem_boxes[s] = box
            self._stem_grid.addWidget(box, 1 + i // 3, i % 3)

    def _on_model_changed(self, _idx: int) -> None:
        mid = self._current_model()
        self.state.config.set("sep.model", mid, save=True)
        self._rebuild_stems()
        self._refresh_model_label()

    def _refresh_model_label(self) -> None:
        mid = self._current_model()
        info = sep_models.describe(mid)
        runtime_ok = bool(info.get("runtime", {}).get("available"))
        if not runtime_ok:
            self.model_label.setText(tr("Sep", "Separation runtime missing — reinstall AudioBench"))
            self.model_label.setStyleSheet(f"color:{theme.ERR};")
            if hasattr(self, "start_btn"):
                self.start_btn.setEnabled(False)
            return
        if info.get("present"):
            self.model_label.setText(tr("Sep", "Model: {name} ({mb:.0f} MB)").format(
                name=Path(info["path"]).name, mb=info.get("size_mb", 0)))
            self.model_label.setStyleSheet(f"color:{theme.TEXT_DIM};")
            if hasattr(self, "start_btn"):        # __init__ 早期还没有按钮
                self.start_btn.setEnabled(self.job is None)
        else:
            self.model_label.setText(tr("Sep", "Model not installed — see docs/models.md"))
            self.model_label.setStyleSheet(f"color:{theme.ERR};")
            if hasattr(self, "start_btn"):
                self.start_btn.setEnabled(False)

    # ---------------------------------------------------------------- 运行
    def _selected_stems(self) -> list[str]:
        return [s for s, b in self.stem_boxes.items() if b.isChecked()]

    def start(self) -> None:
        path, track_index = self._selected_source()
        if not path:
            return
        stems = self._selected_stems()
        if not stems:
            QMessageBox.information(self, tr("Sep", "Separate stems"),
                                    tr("Sep", "Select at least one stem"))
            return
        self.progress.setValue(0)
        self.create_btn.setEnabled(False)
        self.status.setText(tr("Sep", "Starting worker…"))
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        # 建轨时要用：源轨序号（静音用）、曲名（命名用）
        self._source_path = path
        self._source_track = track_index
        if track_index is not None and 0 <= track_index < len(self.state.project.tracks):
            self._source_name = self.state.project.tracks[track_index].name
        else:
            self._source_name = Path(path).stem
        # stem 落盘到缓存目录（不是临时目录）：建轨后工程要长期引用这些 wav
        # 目录名由父进程算好并留存：子进程回传的路径可能被平台编码污染（0.5.0 的 bug）
        self._out_dir = Path(self.state.paths.cache_dir) / "stems" / f"{Path(path).stem}_{int(time.time())}"
        out_dir = self._out_dir
        job = sep_client.SeparationJob(path, out_dir=str(out_dir), stems=stems,
                                       threads=self.threads.value(),
                                       model_id=self._current_model(), parent=self)
        self.job = job
        self._t0 = time.time()
        job.started.connect(self._on_started)
        job.stage.connect(self._on_stage)
        job.progress.connect(self._on_progress)
        job.stemReady.connect(self._on_stem)
        job.finished.connect(self._on_finished)
        job.failed.connect(self._on_failed)
        self._stem_count = {"n": 0, "total": len(stems)}
        self.result = None
        job.start()

    def cancel(self) -> None:
        if self.job is not None:
            self.job.cancel()
            self.status.setText(tr("Sep", "Cancelled — no tracks were created"))
        self._reset_buttons()

    def _on_started(self, evt: dict) -> None:
        self.status.setText(tr("Sep", "Model {name} on {threads} threads").format(
            name=evt.get("model", ""), threads=evt.get("threads", 0)))

    def _on_stage(self, stage: str, evt: dict) -> None:
        if stage == "decoded":
            self.status.setText(tr("Sep", "Decoded {sec:.0f}s, starting inference…").format(
                sec=evt.get("seconds", 0)))
        elif stage == "model_loaded":
            self.status.setText(tr("Sep", "Model loaded in {t:.1f}s").format(t=evt.get("t", 0)))

    def _on_progress(self, evt: dict) -> None:
        pct = float(evt.get("percent", 0)) * 1000
        self.progress.setValue(int(pct))
        self.status.setText(tr("Sep", "Chunk {i}/{n} · ETA {eta:.0f}s · RSS {rss:.0f} MB").format(
            i=evt.get("chunk", 0), n=evt.get("chunks", 0),
            eta=evt.get("eta_s", 0), rss=evt.get("rss_mb", 0)))

    def _on_stem(self, evt: dict) -> None:
        self._stem_count["n"] += 1

    def _on_finished(self, evt: dict) -> None:
        self.result = dict(evt)
        # 建轨需要知道「源是谁」：没有这几个字段，源轨静音和轨道命名都会落空
        self.result["source"] = getattr(self, "_source_path", "")
        self.result["source_track"] = getattr(self, "_source_track", None)
        self.result["source_name"] = getattr(self, "_source_name", "Stems")
        # 父进程自己算出来的目录，不经过子进程 stdout，不会被编码问题污染
        self.result["out_dir"] = str(getattr(self, "_out_dir", ""))
        self.progress.setValue(1000)
        self.status.setText(tr("Sep", "Done in {sec:.0f}s (RTF {rtf:.2f})").format(
            sec=evt.get("seconds", 0), rtf=evt.get("rtf", 0)))
        self.create_btn.setEnabled(True)
        self._reset_buttons()

    def _on_failed(self, code: str, message: str) -> None:
        self.status.setText(f"{code}: {message}")
        if code != "cancelled":
            QMessageBox.critical(self, tr("Sep", "Separation failed"), f"{code}\n{message}")
        self._reset_buttons()

    def _reset_buttons(self) -> None:
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if self.job is not None and not self.job.busy:
            self.job = None

    def _create(self) -> None:
        if not self.result:
            return
        self.stemsCreated.emit(self.result)
        self.accept()

    def closeEvent(self, e):
        if self.job is not None and self.job.busy:
            self.job.cancel()
        super().closeEvent(e)

    def reject(self) -> None:
        if self.job is not None and self.job.busy:
            self.job.cancel()
        super().reject()


_ = Qt
