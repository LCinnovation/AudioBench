"""主窗口：三段式布局（§4.1）+ 菜单 + 快捷键 + 导入/设置对话框。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressDialog, QSplitter,
    QStatusBar, QVBoxLayout, QWidget,
)

from ..app.i18n import LANG_AUTO, LANG_EN, LANG_ZH, I18n, tr
from ..app.state import AppState, ImportJob
from ..model.clip import Clip
from ..model.track import Track
from ..version import APP_NAME, VERSION
from . import theme
from .mixer import MixerPanel
from .timeline import TimelineArea
from .track_header import TrackHeaderColumn
from .transport import TransportBar

log = logging.getLogger("ab.ui")

AUDIO_FILTER = "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.wma);;All files (*.*)"
PROJECT_FILTER = "AudioBench project (*.abproj)"


def _guess_instrument(name: str) -> str:
    low = name.lower()
    table = (
        (("vocal", "vox", "人声", "唱"), "vocal"),
        (("drum", "kick", "snare", "鼓"), "drum"),
        (("bass", "贝斯", "低音"), "bass"),
        (("guitar", "gtr", "吉他"), "guitar"),
        (("piano", "keys", "钢琴"), "piano"),
    )
    for keys, inst in table:
        if any(k in low for k in keys):
            return inst
    return "other"


class MainWindow(QMainWindow):
    def __init__(self, state: AppState, i18n: I18n):
        super().__init__()
        self.state = state
        self.i18n = i18n
        self._import_job: ImportJob | None = None
        self._progress: QProgressDialog | None = None
        import time as _time

        self._t_start = _time.time()

        self.setMinimumSize(960, 600)
        self.resize(*[int(x) for x in (state.config.get("ui.window") or [1280, 800])])

        # ---- 三段式：走带条 / 编排区 / 混音器
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.transport = TransportBar(state, self)
        outer.addWidget(self.transport)

        from .clip_inspector import ClipInspector

        self.inspector = ClipInspector(state, self)
        outer.addWidget(self.inspector)

        self.vsplit = QSplitter(Qt.Vertical, self)
        self.hsplit = QSplitter(Qt.Horizontal, self)
        self.headers = TrackHeaderColumn(state, self)
        self.timeline = TimelineArea(state, self)
        self.hsplit.addWidget(self.headers)
        self.hsplit.addWidget(self.timeline)
        self.hsplit.setStretchFactor(0, 0)
        self.hsplit.setStretchFactor(1, 1)
        self.hsplit.setCollapsible(0, False)
        self.hsplit.setSizes([220, 1000])
        self.vsplit.addWidget(self.hsplit)

        self.mixer = MixerPanel(state, self)
        self.vsplit.addWidget(self.mixer)
        self.vsplit.setStretchFactor(0, 1)
        self.vsplit.setStretchFactor(1, 0)
        self.vsplit.setCollapsible(1, True)
        self.vsplit.setSizes([620, 180])
        outer.addWidget(self.vsplit, 1)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar(self))
        self._engine_label = QLabel("", self)
        self.statusBar().addPermanentWidget(self._engine_label)

        self._build_actions()
        self._connect()
        self._restore_layout()
        self.update_title()
        self._refresh_views()
        self._update_engine_label()

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(100)
        self._ui_timer.timeout.connect(self._tick)
        self._ui_timer.start()

        self._autosave = QTimer(self)
        self._autosave.setInterval(30000)

        def _do_autosave() -> None:
            if self.state.dirty:
                self.state.autosave_draft()

        self._autosave.timeout.connect(_do_autosave)
        self._autosave.start()

    # ---------------------------------------------------------------- 动作
    def _build_actions(self) -> None:
        mb = self.menuBar()

        m_file = mb.addMenu(tr("Menu", "&File"))
        self.act_new = QAction(tr("Menu", "New project"), self, shortcut=QKeySequence.New)
        self.act_open = QAction(tr("Menu", "Open project..."), self, shortcut=QKeySequence.Open)
        self.act_save = QAction(tr("Menu", "Save"), self, shortcut=QKeySequence.Save)
        self.act_save_as = QAction(tr("Menu", "Save as..."), self, shortcut=QKeySequence.SaveAs)
        self.act_import = QAction(tr("Menu", "Import audio..."), self,
                                  shortcut=QKeySequence("Ctrl+I"))
        self.act_export = QAction(tr("Menu", "Export audio..."), self,
                                  shortcut=QKeySequence("Ctrl+E"))
        self.act_convert = QAction(tr("Menu", "Format conversion (NCM)..."), self)
        self.act_quit = QAction(tr("Menu", "Exit"), self, shortcut=QKeySequence.Quit)
        for a in (self.act_new, self.act_open, self.act_save, self.act_save_as,
                  self.act_import, self.act_export, self.act_convert):
            m_file.addAction(a)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

        m_edit = mb.addMenu(tr("Menu", "&Edit"))
        self.act_undo = QAction(tr("Menu", "Undo"), self, shortcut=QKeySequence.Undo)
        self.act_redo = QAction(tr("Menu", "Redo"), self, shortcut=QKeySequence("Ctrl+Shift+Z"))
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)

        m_track = mb.addMenu(tr("Menu", "&Track"))
        self.act_add_track = QAction(tr("Menu", "Add track"), self, shortcut=QKeySequence("Ctrl+T"))
        self.act_del_track = QAction(tr("Menu", "Delete track"), self)
        self.act_add_marker = QAction(tr("Menu", "Add marker at playhead"), self,
                                      shortcut=QKeySequence("M"))
        self.act_clear_markers = QAction(tr("Menu", "Clear markers"), self)
        m_track.addAction(self.act_add_track)
        m_track.addAction(self.act_del_track)
        m_track.addSeparator()
        m_track.addAction(self.act_add_marker)
        m_track.addAction(self.act_clear_markers)

        m_view = mb.addMenu(tr("Menu", "&View"))
        self.act_mixer = QAction(tr("Menu", "Mixer"), self, shortcut=QKeySequence("Ctrl+M"))
        self.act_mixer.setCheckable(True)
        self.act_mixer.setChecked(True)
        self.act_grid = QAction(tr("Menu", "Beat grid"), self)
        self.act_grid.setCheckable(True)
        self.act_grid.setChecked(bool(self.state.config.get("ui.grid", True)))
        self.act_theme = QAction(tr("Menu", "Light theme (experimental)"), self)
        self.act_theme.setCheckable(True)
        self.act_theme.setChecked(bool(self.state.config.get("ui.light_theme", False)))
        self.act_theme.setToolTip(tr("Menu", "Records the preference; the palette ships next version"))
        self.act_zoom_in = QAction(tr("Menu", "Zoom in"), self, shortcut=QKeySequence("Ctrl+="))
        self.act_zoom_out = QAction(tr("Menu", "Zoom out"), self, shortcut=QKeySequence("Ctrl+-"))
        m_view.addAction(self.act_mixer)
        m_view.addAction(self.act_grid)
        m_view.addAction(self.act_theme)
        m_view.addSeparator()
        m_view.addAction(self.act_zoom_in)
        m_view.addAction(self.act_zoom_out)

        m_help = mb.addMenu(tr("Menu", "&Help"))
        self.act_settings = QAction(tr("Menu", "Settings..."), self)
        self.act_about = QAction(tr("Menu", "About"), self)
        self.act_diag = QAction(tr("Menu", "Diagnostics"), self)
        self.act_guide = QAction(tr("Menu", "Guided tour"), self, shortcut=QKeySequence("F1"))
        m_help.addAction(self.act_guide)
        m_help.addSeparator()
        m_help.addAction(self.act_settings)
        m_help.addAction(self.act_diag)
        m_help.addAction(self.act_about)

        # 走带条右侧按钮
        self.transport.feature_buttons["settings"].clicked.connect(
            lambda _v: self.open_settings())
        self.transport.feature_buttons["help"].clicked.connect(lambda _v: self.open_help())
        self.transport.feature_buttons["export"].setEnabled(True)
        self.transport.feature_buttons["export"].clicked.connect(
            lambda _v: self.open_export())
        self.transport.feature_buttons["convert"].setEnabled(True)
        self.transport.feature_buttons["convert"].clicked.connect(
            lambda _v: self.open_ncm())
        self.transport.feature_buttons["separate"].setEnabled(True)
        self.transport.feature_buttons["separate"].clicked.connect(
            lambda _v: self.open_sep())

    def _connect(self) -> None:
        st = self.state
        st.structureChanged.connect(self._on_structure)
        st.paramsChanged.connect(self._on_params)
        st.transportChanged.connect(self._refresh_transport)
        st.status.connect(lambda msg: self.statusBar().showMessage(msg, 4000))
        st.errorRaised.connect(self._show_error)

        self.act_new.triggered.connect(self.new_project)
        self.act_open.triggered.connect(self.open_project)
        self.act_save.triggered.connect(lambda: self.save_project(False))
        self.act_save_as.triggered.connect(lambda: self.save_project(True))
        self.act_import.triggered.connect(self.import_dialog)
        self.act_export.triggered.connect(self.open_export)
        self.act_convert.triggered.connect(self.open_ncm)
        self.act_quit.triggered.connect(self.close)
        self.act_undo.triggered.connect(st.undo_last)
        self.act_redo.triggered.connect(st.redo_last)
        self.act_add_track.triggered.connect(lambda: st.add_track())
        self.act_del_track.triggered.connect(self._delete_selected_track)
        self.act_add_marker.triggered.connect(lambda: self.add_marker_at(self.state.position()))
        self.act_clear_markers.triggered.connect(self.clear_markers)
        self.act_grid.toggled.connect(self._on_grid_toggled)
        self.act_theme.toggled.connect(self._on_theme_toggled)
        self.timeline.markerRequested.connect(self._on_marker_request)
        self.act_mixer.toggled.connect(self._set_mixer_visible)
        self.act_zoom_in.triggered.connect(lambda: self.timeline.zoom_by(1.25))
        self.act_zoom_out.triggered.connect(lambda: self.timeline.zoom_by(1 / 1.25))
        self.act_settings.triggered.connect(self.open_settings)
        self.act_about.triggered.connect(self.show_about)
        self.act_diag.triggered.connect(self.show_diagnostics)
        self.act_guide.triggered.connect(self.open_help)

        self.transport.playClicked.connect(st.toggle)
        self.transport.stopClicked.connect(self._stop_and_rewind)
        self.transport.rewindClicked.connect(lambda: st.seek(0))
        self.transport.loopToggled.connect(self._toggle_loop)
        self.transport.metronomeToggled.connect(st.set_metronome)
        self.transport.masterDbChanged.connect(self._on_master_db)
        self.transport.bpm.textChanged.connect(self._on_bpm)

        self.timeline.seekRequested.connect(st.seek)
        self.timeline.filesDropped.connect(self.import_paths)
        self.timeline.editRequested.connect(self._on_timeline_edit)
        self.timeline.clipSelected.connect(self._on_clip_selected)

        self.timeline.vertical_scrollbar().valueChanged.connect(self.headers.set_scroll)
        self.headers.selectionChanged.connect(self._on_track_selected)

        self.inspector.splitRequested.connect(self.split_at_playhead)
        self.inspector.duplicateRequested.connect(self.duplicate_selected)
        self.inspector.deleteRequested.connect(self.delete_selected_clip)

        self.mixer.master.masterChanged.connect(self._on_master_db)

        # 编辑快捷键（画布不抢焦点时也能用）
        for seq, fn in (("Del", self.delete_selected_clip),
                        ("S", self.split_at_playhead),
                        ("Ctrl+D", self.duplicate_selected)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(fn)

        # 空格播放：避开文本输入控件
        self._space = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._space.setContext(Qt.ApplicationShortcut)
        self._space.activated.connect(self._on_space)

    # ---------------------------------------------------------------- 状态
    def _on_structure(self) -> None:
        self.headers.rebuild()
        self.mixer.rebuild()
        self.timeline.refresh()
        self.inspector.set_clip(self.timeline.canvas.selected_clip)
        self.update_title()

    def _on_params(self) -> None:
        self.headers.sync_from_model()
        self.mixer.sync_from_model()
        self.timeline.canvas.update()
        self.update_title()

    def _on_track_selected(self, idx: int) -> None:
        self.state.set_selected_track(idx)
        self._refresh_views()

    def _on_clip_selected(self, clip) -> None:
        self.timeline.canvas.update()
        self.inspector.set_clip(clip)

    def _refresh_views(self) -> None:
        self.headers.sync_from_model()
        self.mixer.sync_from_model()
        self.timeline.refresh()
        self.inspector.set_clip(self.timeline.canvas.selected_clip)

    def _refresh_transport(self) -> None:
        eng = self.state.engine
        self.transport.btn_play.setChecked(bool(eng and eng.playing))

    def update_title(self) -> None:
        name = self.state.project_title()
        self.setWindowTitle(f"{name} — {APP_NAME} {VERSION}")

    def _update_engine_label(self) -> None:
        info = self.state.engine_info()
        if info.get("backend") in (None, "none", ""):
            self._engine_label.setText(tr("Status", "Audio: unavailable"))
            return
        self._engine_label.setText(
            f"{info.get('api', info.get('backend'))} · {info.get('device_rate', 0)} Hz · "
            f"{info.get('block_frames', 0)} frames · {info.get('latency_ms', 0):.0f} ms · "
            f"xruns {info.get('xruns', 0)}"
        )

    def _tick(self) -> None:
        self.mixer.tick()
        self._update_engine_label()
        self.transport.btn_play.setChecked(bool(self.state.engine and self.state.engine.playing))
        if self.state.engine is not None:
            self._dbg_ticks = getattr(self, "_dbg_ticks", 0) + 1
            if self._dbg_ticks % 60 == 0:      # ~6s 一条 debug 级快照，排查 xrun 用
                import logging as _lg
                import time as _t

                _lg.getLogger("ab.engine").debug(
                    "tick t=%.1fs: %s", _t.time() - getattr(self, "_t_start", _t.time()),
                    self.state.engine.info())

    # ---------------------------------------------------------------- 工程
    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        self.state.new_project()
        self.timeline.apply_zoom(float(self.state.config.get("ui.zoom", 160.0)))
        self._refresh_views()
        self.update_title()

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        start = self.state.config.get("ui.last_dir", "") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, tr("Dialog", "Open project"), start,
                                              PROJECT_FILTER)
        if not path:
            return
        try:
            missing = self.state.open_project(path)
        except Exception as e:
            self._show_error(tr("Dialog", "Cannot open project"),
                             f"{Path(path).name}\n{e}")
            return
        self.state.config.set("ui.last_dir", str(Path(path).parent), save=True)
        self._refresh_views()
        self.timeline.apply_zoom(float(self.state.config.get("ui.zoom", 160.0)))
        self.update_title()
        if missing:
            QMessageBox.warning(self, tr("Dialog", "Missing audio files"),
                                "\n".join(missing[:12]))

    def save_project(self, save_as: bool = False) -> bool:
        target = self.state.project.path
        if save_as or target is None:
            start = str(target) if target else str(Path(self.state.config.get("ui.last_dir", "")
                                                        or Path.home()) / "Untitled.abproj")
            path, _ = QFileDialog.getSaveFileName(self, tr("Dialog", "Save project"), start,
                                                  PROJECT_FILTER)
            if not path:
                return False
            target = Path(path)
        try:
            self.state.save_project(target)
        except Exception as e:
            self._show_error(tr("Dialog", "Cannot save project"), str(e))
            return False
        self.update_title()
        return True

    def _confirm_discard(self) -> bool:
        if not self.state.dirty:
            return True
        r = QMessageBox.question(
            self, tr("Dialog", "Unsaved changes"),
            tr("Dialog", "Discard unsaved changes?"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return r == QMessageBox.Yes

    # ---------------------------------------------------------------- 导入
    def import_dialog(self) -> None:
        start = self.state.config.get("ui.last_dir", "") or str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Dialog", "Import audio"), start,
                                                AUDIO_FILTER)
        if paths:
            self.state.config.set("ui.last_dir", str(Path(paths[0]).parent), save=True)
            self.import_paths(paths)

    def import_paths(self, paths: list[str]) -> None:
        if self._import_job is not None:
            self.statusBar().showMessage(tr("Status", "Import already running"), 3000)
            return
        audio = [p for p in paths if Path(p).suffix.lower() in
                 (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma")]
        if not audio:
            self._show_error(tr("Dialog", "Import failed"), tr("Dialog", "No supported audio file"))
            return
        self._progress = QProgressDialog(tr("Status", "Importing..."), tr("Dialog", "Cancel"),
                                         0, len(audio), self)
        self._progress.setWindowTitle(tr("Dialog", "Import audio"))
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        job = ImportJob(self.state.decode, self.state.project.sample_rate, audio, self)
        self._import_job = job
        job.progress.connect(self._on_import_progress)
        job.finished_ok.connect(self._on_import_done)
        job.failed.connect(self._on_import_failed)
        job.finished.connect(self._on_import_finished)
        self._progress.canceled.connect(job.cancel)
        job.start()

    def _on_import_progress(self, index: int, total: int, name: str, frac: float) -> None:
        if self._progress is None:
            return
        self._progress.setMaximum(total)
        self._progress.setValue(index)
        self._progress.setLabelText(f"{name}  {frac * 100:.0f}%")

    def _on_import_done(self, decoded: list) -> None:
        if not decoded:
            return
        with self.state.edit("Import audio"):
            for dec in decoded:
                name = Path(dec.source_path).stem
                track = self.state.project.add_track(Track(
                    name=name, color=self.state.project.next_color(),
                    instrument=_guess_instrument(name)))
                track.clips.append(Clip(
                    path=dec.source_path, name=name,
                    src_start=0, src_end=dec.frames, timeline_start=0,
                ))
        # 音频源与峰值缓存先准备好，再重建渲染计划
        for dec in decoded:
            self.state._sources[dec.source_path] = self.state.decode.source(dec)
            pc = self.state.decode.peaks(dec)
            if pc is not None:
                self.state._peaks[dec.source_path] = pc
        self.state.rebuild_plan()
        self.state.structureChanged.emit()
        self.state.status.emit(tr("Status", "Imported {n} file(s)").format(n=len(decoded)))

    def _on_import_failed(self, failures: list) -> None:
        lines = [f"{Path(p).name}: {code}" for p, code in failures[:10]]
        self._show_error(tr("Dialog", "Import failed"), "\n".join(lines))

    def _on_import_finished(self) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        self._import_job = None

    # ---------------------------------------------------------------- 编辑
    def _delete_selected_track(self) -> None:
        idx = self.headers.selected
        if 0 <= idx < len(self.state.project.tracks):
            self.state.remove_track(idx)

    # ---------------------------------------------------------------- 标记点 / 网格 / 主题
    def add_marker_at(self, pos: int, name: str = "") -> None:
        with self.state.edit("Add marker"):
            self.state.project.add_marker(pos, name)
        self.timeline.ruler.update()

    def clear_markers(self) -> None:
        if not self.state.project.markers:
            return
        with self.state.edit("Clear markers"):
            self.state.project.markers.clear()
        self.timeline.ruler.update()

    def _on_marker_request(self, action: str, pos: int) -> None:
        proj = self.state.project
        if action == "add":
            self.add_marker_at(pos)
        elif action == "remove":
            with self.state.edit("Delete marker"):
                proj.remove_marker_at(pos)
            self.timeline.ruler.update()
        elif action == "rename":
            marker = proj.marker_at(pos)
            if marker is None:
                return
            from PySide6.QtWidgets import QInputDialog

            text, ok = QInputDialog.getText(self, tr("Timeline", "Rename marker"),
                                            tr("Timeline", "Marker name"), text=marker.get("name", ""))
            if ok and text.strip():
                with self.state.edit("Rename marker"):
                    marker["name"] = text.strip()
                self.timeline.ruler.update()

    def _on_grid_toggled(self, on: bool) -> None:
        self.state.config.set("ui.grid", bool(on), save=True)
        self.timeline.canvas.update()

    def _on_theme_toggled(self, on: bool) -> None:
        self.state.config.set("ui.light_theme", bool(on), save=True)
        self.statusBar().showMessage(tr("Menu", "Light theme is an interface only for now"), 4000)

    def _on_clip_selected_legacy(self, clip) -> None:
        self.timeline.canvas.update()

    def _on_timeline_edit(self, kind: str, payload) -> None:
        st = self.state
        if kind == "delete_clip" and payload is not None:
            with st.edit("Delete clip"):
                for t in st.project.tracks:
                    if payload in t.clips:
                        t.clips.remove(payload)
                        break
            self.timeline.canvas.selected_clip = None
        elif kind == "duplicate_clip" and payload is not None:
            with st.edit("Duplicate clip"):
                for t in st.project.tracks:
                    if payload in t.clips:
                        copy = payload.copy()
                        copy.name = (payload.name or Path(payload.path).stem) + " copy"
                        copy.timeline_start = payload.timeline_end
                        copy.clamp()
                        t.clips.append(copy)
                        break
        elif kind == "split_at_playhead":
            clip = payload or self.timeline.canvas.selected_clip
            self.split_clip(clip, st.position())
        elif kind == "loop_start":
            st.set_loop(int(payload), int(payload) + st.project.sample_rate * 4)

    def split_clip(self, clip, pos: int) -> None:
        """在 pos 处把片段切成两段（变速片段按 speed 换算源位置）。"""
        if clip is None or not (clip.timeline_start < pos < clip.timeline_end):
            return
        st = self.state
        with st.edit("Split clip"):
            for t in st.project.tracks:
                if clip not in t.clips:
                    continue
                offset = pos - clip.timeline_start
                cut = clip.src_start + int(round(offset * clip.speed))
                if cut <= clip.src_start or cut >= clip.src_end:
                    return
                right = clip.copy()
                right.timeline_start = pos
                right.src_start = cut
                right.fade_in = 0
                clip.src_end = cut
                clip.fade_out = 0
                clip.clamp()
                right.clamp()
                t.clips.append(right)
                break

    def duplicate_selected(self) -> None:
        clip = self.timeline.canvas.selected_clip
        if clip is not None:
            self._on_timeline_edit("duplicate_clip", clip)

    def delete_selected_clip(self) -> None:
        clip = self.timeline.canvas.selected_clip
        if clip is not None:
            self._on_timeline_edit("delete_clip", clip)

    def split_at_playhead(self) -> None:
        clip = self.timeline.canvas.selected_clip
        if clip is None:
            pos = self.state.position()
            for t in self.state.project.tracks:
                for c in t.clips:
                    if c.timeline_start < pos < c.timeline_end:
                        clip = c
                        break
                if clip:
                    break
        self.split_clip(clip, self.state.position())

    def open_export(self) -> None:
        from .dialogs.export import ExportDialog

        self.state.stop()
        dlg = ExportDialog(self.state, self)
        dlg.exec()

    def open_ncm(self) -> None:
        from .dialogs.ncm import NcmDialog

        dlg = NcmDialog(self.state, self)
        dlg.exec()

    def open_sep(self) -> None:
        from .dialogs.sep import SeparationDialog

        self.state.stop()
        idx = self.state.selected_track_index()
        dlg = SeparationDialog(self.state, self, track_index=idx if idx >= 0 else None)
        dlg.stemsCreated.connect(self.create_stem_tracks)
        dlg.exec()

    def create_stem_tracks(self, result: dict) -> None:
        """一键建轨：源轨静音、六条 stem 轨各自一条 clip（同起点，方便 A/B）。"""
        st = self.state
        sr = st.project.sample_rate
        instruments = {"vocals": "vocal", "drums": "drum", "bass": "bass",
                       "guitar": "guitar", "piano": "piano", "other": "other"}
        names = {
            "vocals": tr("Stem", "Vocals"),
            "drums": tr("Stem", "Drums"),
            "bass": tr("Stem", "Bass"),
            "guitar": tr("Stem", "Guitar"),
            "piano": tr("Stem", "Piano"),
            "other": tr("Stem", "Other (incl. strings)"),
        }
        created = 0
        failed: list[str] = []
        # 曲名：优先用对话框给的 source_name，其次退回输入文件名/目录名
        base = (result.get("source_name") or "").strip()
        if not base:
            src = result.get("source") or ""
            base = Path(src).stem if src else Path(result.get("dir", "")).name or "Stems"
        base = base[:48]
        src_track = result.get("source_track")
        with st.edit("Separate stems"):
            # 静音源轨：否则 stem 与源同时播放，音量翻倍且听起来「糊」
            if isinstance(src_track, int) and 0 <= src_track < len(st.project.tracks):
                st.project.tracks[src_track].mute = True
            else:
                src = result.get("source")
                for t in st.project.tracks:
                    if src and any(Path(c.path) == Path(src) for c in t.clips):
                        t.mute = True
            # 子进程回传的路径可能被平台编码污染（中文目录变 ??????）；
            # 父进程自己知道输出目录，按 stem 名兜底拼一次，避免「分离成功却 0 条轨」
            out_dir = Path(result.get("out_dir") or result.get("dir") or "")
            for stem in result.get("stems", []):
                paths = [Path(stem.get("path") or "")]
                if out_dir and stem.get("name"):
                    paths.append(out_dir / f"{stem['name']}.wav")
                dec = None
                for cand in paths:
                    if not cand or not str(cand) or not cand.exists():
                        continue
                    try:
                        dec = st.decode.ensure(str(cand), sr)
                        break
                    except Exception:
                        log.exception("stem decode failed: %s", cand)
                if dec is None:
                    failed.append(stem.get("name", "?"))
                    continue
                st._sources[dec.source_path] = st.decode.source(dec)
                pc = st.decode.peaks(dec)
                if pc is not None:
                    st._peaks[dec.source_path] = pc
                name = f"{base} · {names.get(stem['name'], stem['name'])}"
                track = st.project.add_track(Track(
                    name=name, color=st.project.next_color(),
                    instrument=instruments.get(stem["name"], "other")))
                track.clips.append(Clip(path=dec.source_path, name=stem["name"],
                                        src_start=0, src_end=dec.frames, timeline_start=0))
                created += 1
        st.structureChanged.emit()
        self._refresh_views()
        if created == 0:
            detail = ", ".join(failed) if failed else "-"
            log.warning("no stem tracks created (failed=%s)", failed)
            self._show_error(tr("Sep", "Separation failed"),
                             tr("Sep", "No stem track could be created") + f"\n{detail}")
            return
        st.status.emit(tr("Sep", "Created {n} stem tracks").format(n=created))
        if failed:
            st.status.emit(tr("Sep", "{n} stems could not be loaded: {names}").format(
                n=len(failed), names=", ".join(failed)))

    def maybe_recover_draft(self) -> bool:
        """启动时问一次：上次没正常退出，要不要恢复草稿。"""
        if not self.state.has_draft():
            return False
        r = QMessageBox.question(
            self, tr("Dialog", "Recover last session"),
            tr("Dialog", "AudioBench did not exit cleanly last time. Recover the autosaved draft?"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            self.state.clear_draft()
            return False
        ok = self.state.load_draft()
        if ok:
            self._refresh_views()
            self.update_title()
        return ok

    def _on_clip_param(self, *_args) -> None:
        self.timeline.canvas.update()

    def _on_space(self) -> None:
        fw = QApplication.focusWidget()
        if isinstance(fw, (QLabel, QWidget)) and fw is not None:
            from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit, QTextEdit

            if isinstance(fw, (QLineEdit, QTextEdit, QAbstractSpinBox)):
                return
        self.state.toggle()

    def _stop_and_rewind(self) -> None:
        self.state.stop()
        self.state.seek(0)

    def _toggle_loop(self, on: bool) -> None:
        if on:
            pos = self.state.position()
            sec = self.state.project.sample_rate
            self.state.set_loop(pos, pos + sec * 4)
        else:
            self.state.set_loop(None, None)

    def _on_master_db(self, db: float) -> None:
        self.state.set_master_db(db)
        self.transport.vol.setValue(int(db * 10))

    def _on_bpm(self, text: str) -> None:
        try:
            bpm = float(text or 120)
        except ValueError:
            return
        bpm = max(20.0, min(300.0, bpm))
        self.state.config.set("ui.bpm", bpm)
        if self.state.engine is not None:
            self.state.engine.set_bpm(bpm)

    # ---------------------------------------------------------------- 视图
    def _set_mixer_visible(self, on: bool) -> None:
        self.mixer.setVisible(on)
        self.state.config.set("ui.mixer_open", bool(on))

    def _restore_layout(self) -> None:
        self.mixer.setVisible(bool(self.state.config.get("ui.mixer_open", True)))
        self.act_mixer.setChecked(bool(self.state.config.get("ui.mixer_open", True)))
        bpm = float(self.state.config.get("ui.bpm", 120.0) or 120.0)
        self.transport.set_bpm(bpm)
        self.state.set_metronome(bool(self.state.config.get("audio.metronome", False)))
        self.transport.btn_metro.setChecked(bool(self.state.config.get("audio.metronome", False)))

    # ---------------------------------------------------------------- 对话框
    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.state, self.i18n, self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply()
            self.retranslate()
            self._refresh_views()
            self.update_title()

    def show_about(self) -> None:
        QMessageBox.about(self, tr("Dialog", "About"),
                          f"{APP_NAME} {VERSION}\n\n"
                          + tr("Dialog", "Local multi-track audio workstation. Runs offline."))

    # ---------------------------------------------------------------- 引导 / 帮助
    def build_onboarding_steps(self):
        """新手引导：每一步都说清「怎么做 / 会看到什么 / 出问题怎么办」。"""
        from .onboarding import Step

        return [
            Step(tr("Guide", "Step 1 · Bring a song in"),
                 tr("Guide", "Hold your song with the mouse (mp3, wav or flac), drag it onto the "
                             "dark area below and let go. You can also press Ctrl+I and pick a file.\n"
                             "A long bar appears — that bar is your song."),
                 lambda: self.timeline,
                 tr("Guide", "Nothing happens? Check the file ends with mp3/wav/flac/ogg/m4a/aac/wma.")),
            Step(tr("Guide", "Step 2 · Press Space to listen"),
                 tr("Guide", "Space starts and stops playback; Home jumps back to the beginning.\n"
                             "To practise one part over and over, hold Shift and drag a short "
                             "range on the ruler — that range will loop."),
                 lambda: self.transport,
                 tr("Guide", "No sound? Look at the M (mute) button on the track first.")),
            Step(tr("Guide", "Step 3 · Meet the track panel"),
                 tr("Guide", "Left of every bar is a small panel: the song name on top "
                             "(double-click to rename), M mutes, S solos (only that track plays), "
                             "the round knob is volume, EQ shapes high and low frequencies.\n"
                             "Not sure what a button does? Hover over it for a tooltip."),
                 lambda: self.headers),
            Step(tr("Guide", "Step 4 · Select a bar to edit it"),
                 tr("Guide", "Click a bar once to select it. A row of controls appears above: "
                             "speed (slower/faster, pitch stays), pitch (key change, speed stays), "
                             "volume, fade in and fade out.\n"
                             "Drag the left or right edge of the bar to trim it. Wrong move? Ctrl+Z."),
                 lambda: self.inspector),
            Step(tr("Guide", "Step 5 · Split vocals from backing"),
                 tr("Guide", "Click the person icon at the far right of the transport bar, keep "
                             "\"4 stems · high quality\", then press Start.\n"
                             "When the progress bar finishes, press Create tracks: vocals, drums, "
                             "bass and other appear, and the original track is muted for you."),
                 lambda: self.transport.feature_buttons.get("separate"),
                 tr("Guide", "A 3-minute song takes about 1 minute. You can cancel any time.")),
            Step(tr("Guide", "Step 6 · Export an mp3"),
                 tr("Guide", "Press Ctrl+E: choose a format (mp3 320 is the usual choice), pick a "
                             "folder and press Start.\n"
                             "The folder opens with your new file selected when it is done."),
                 lambda: self.transport.feature_buttons.get("export"),
                 tr("Guide", "Press F1 any time to reopen this tutorial and the shortcut list.")),
        ]

    def start_onboarding(self) -> None:
        from .onboarding import OnboardingOverlay

        if getattr(self, "_overlay", None) is not None:
            return
        overlay = OnboardingOverlay(self, self.build_onboarding_steps())
        overlay.setGeometry(self.rect())
        overlay.finished.connect(self._on_onboarding_finished)
        overlay.show()
        overlay.raise_()
        overlay.setFocus()
        self._overlay = overlay

    def _on_onboarding_finished(self, completed: bool) -> None:
        self._overlay = None
        if completed:
            self.state.config.set("onboarding.done", True)
            self.state.config.set("onboarding.skipped", False, save=True)
            self.statusBar().showMessage(tr("Guide", "Tour finished — press F1 for help"), 5000)
        else:
            self.state.config.set("onboarding.skipped", True, save=True)
            self.statusBar().showMessage(tr("Guide", "Tour skipped — press F1 to open help"), 5000)

    def maybe_show_onboarding(self) -> None:
        if self.state.config.get("onboarding.done") or self.state.config.get("onboarding.skipped"):
            return
        from PySide6.QtCore import QTimer

        QTimer.singleShot(700, self.start_onboarding)

    def open_help(self) -> None:
        from .dialogs.help import HelpPanel

        lang = getattr(self.i18n, "language", "zh_CN")
        dlg = HelpPanel(self, on_replay=self.start_onboarding, lang=lang)
        dlg.exec()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        overlay = getattr(self, "_overlay", None)
        if overlay is not None:
            overlay.setGeometry(self.rect())

    def show_diagnostics(self) -> None:
        from ..app.selftest import collect

        data = collect(self.state, deep=False)
        text = "\n".join(f"{k}: {v}" for k, v in _flatten(data).items())
        box = QMessageBox(self)
        box.setWindowTitle(tr("Dialog", "Diagnostics"))
        box.setText(tr("Dialog", "Copy this for bug reports"))
        box.setDetailedText(text)
        box.exec()

    # ---------------------------------------------------------------- 生命周期
    def retranslate(self) -> None:
        self.transport.retranslate()
        self.headers.retranslate()
        self.mixer.retranslate()
        self.timeline.retranslate()
        self.act_new.setText(tr("Menu", "New project"))
        self.act_open.setText(tr("Menu", "Open project..."))
        self.act_save.setText(tr("Menu", "Save"))
        self.act_save_as.setText(tr("Menu", "Save as..."))
        self.act_import.setText(tr("Menu", "Import audio..."))
        self.act_export.setText(tr("Menu", "Export audio..."))
        self.act_convert.setText(tr("Menu", "Format conversion (NCM)..."))
        self.act_quit.setText(tr("Menu", "Exit"))
        self.act_undo.setText(tr("Menu", "Undo"))
        self.act_redo.setText(tr("Menu", "Redo"))
        self.act_add_track.setText(tr("Menu", "Add track"))
        self.act_del_track.setText(tr("Menu", "Delete track"))
        self.act_mixer.setText(tr("Menu", "Mixer"))
        self.act_settings.setText(tr("Menu", "Settings..."))
        self.act_about.setText(tr("Menu", "About"))
        self.act_guide.setText(tr("Menu", "Guided tour"))
        self.update_title()

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        try:
            self.state.config.set("ui.window", [self.width(), self.height()], save=True)
        except Exception:
            pass
        self.state.stop_engine()
        super().closeEvent(e)


class SettingsDialog(QDialog):
    def __init__(self, state: AppState, i18n: I18n, parent=None):
        super().__init__(parent)
        self.state = state
        self.i18n = i18n
        self.setWindowTitle(tr("Dialog", "Settings"))
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        self.lang = QComboBox(self)
        self.lang.addItem(tr("Dialog", "Follow system"), LANG_AUTO)
        self.lang.addItem("中文", LANG_ZH)
        self.lang.addItem("English", LANG_EN)
        cur = i18n.preference
        idx = max(0, [self.lang.itemData(i) for i in range(self.lang.count())].index(cur)
                  if cur in [self.lang.itemData(i) for i in range(self.lang.count())] else 0)
        self.lang.setCurrentIndex(idx)
        form.addRow(tr("Dialog", "Language"), self.lang)

        self.backend = QComboBox(self)
        for key, label in (("auto", tr("Dialog", "Auto")), ("shim", "ab_audio (WASAPI)"),
                           ("sounddevice", "sounddevice (PortAudio)")):
            self.backend.addItem(label, key)
        self.backend.setCurrentIndex(max(0, self.backend.findData(
            state.config.get("audio.backend", "auto"))))
        form.addRow(tr("Dialog", "Audio backend"), self.backend)

        self.buffer = QComboBox(self)
        for n in (128, 256, 512, 1024):
            self.buffer.addItem(f"{n} frames", n)
        self.buffer.setCurrentIndex(max(0, self.buffer.findData(
            int(state.config.get("audio.buffer_frames", 256)))))
        form.addRow(tr("Dialog", "Buffer size"), self.buffer)

        self.device = QComboBox(self)
        self.device.addItem(tr("Dialog", "System default"), "")
        try:
            for d in state.devices:
                self.device.addItem(d.name, d.name)
        except Exception:
            pass
        cur_dev = state.config.get("audio.device", "") or ""
        self.device.setCurrentIndex(max(0, self.device.findData(cur_dev)))
        form.addRow(tr("Dialog", "Output device"), self.device)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def apply(self) -> None:
        st = self.state
        pref = self.lang.currentData()
        st.config.set("language", pref, save=True)
        if self.i18n.set_preference(pref):
            pass  # 主窗口会 retranslate
        audio_changed = (
            self.backend.currentData() != st.config.get("audio.backend")
            or self.buffer.currentData() != int(st.config.get("audio.buffer_frames", 256))
            or (self.device.currentData() or "") != (st.config.get("audio.device", "") or "")
        )
        st.config.set("audio.backend", self.backend.currentData())
        st.config.set("audio.buffer_frames", int(self.buffer.currentData()))
        st.config.set("audio.device", self.device.currentData() or "")
        st.config.save()
        if audio_changed:
            st.stop_engine()
            st.start_engine()


def _flatten(d: dict, prefix: str = "") -> dict:
    out: dict[str, object] = {}
    for k, v in (d or {}).items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out
