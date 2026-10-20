"""应用状态中枢：工程 + 引擎 + 解码缓存 + 撤销栈，UI 只跟它打交道。

所有会改变工程的操作都走 `state.edit("标签")` 上下文管理器：
    with state.edit("Rename track"):
        track.name = "Vocal"
它负责「快照 → 修改 → 提交撤销 → 重建渲染计划 → 广播」。
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..audio import backend as backend_mod
from ..audio.backend import DeviceInfo
from ..audio.dsp import EqPool
from ..audio.engine import Engine
from ..audio.mixer import Mixer
from ..audio.renderplan import RenderPlan, build_plan
from ..io.decoder import DecodeService, Decoded
from ..io.ffmpeg import FfmpegError
from ..io.peakcache import PeakCache
from ..model.project import Project
from ..model.track import Track
from ..model.undo import UndoStack
from ..version import VERSION
from .config import Config
from .paths import AppPaths

log = logging.getLogger("ab.state")

MAX_TRACKS = 32


class AppState(QObject):
    structureChanged = Signal()       # 轨道/片段增删、工程装载、撤销重做 → 需要重建控件
    paramsChanged = Signal()          # 增益/声像/EQ/静音等 → 只刷新数值，不重建控件
    projectChanged = Signal(str)      # 兼容层：附带 kind
    transportChanged = Signal()
    status = Signal(str)
    errorRaised = Signal(str, str)    # title, message（已翻译）
    deviceChanged = Signal()

    def __init__(self, config: Config, paths: AppPaths, i18n=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.paths = paths
        self.i18n = i18n
        self.project = Project(sample_rate=int(config.get("audio.project_sample_rate", 44100)))
        self.decode = DecodeService(paths.cache_dir)
        self.undo = UndoStack(self._snapshot, self._restore)
        self.engine: Engine | None = None
        self.backend = None
        self.mixer: Mixer | None = None
        self.eq_pool: EqPool | None = None
        self.plan: RenderPlan | None = None
        self.master_gain_db = 0.0
        self.devices: list[DeviceInfo] = []
        self._sources: dict[str, object] = {}
        self._peaks: dict[str, PeakCache] = {}
        self._dirty = False
        self._plan_stamp = 0
        self._selected_track = -1

    # ---------------------------------------------------------------- 状态
    @property
    def dirty(self) -> bool:
        return self._dirty

    def mark_clean(self) -> None:
        self._dirty = False

    def project_title(self) -> str:
        name = self.project.path.stem if self.project.path else (self.project.name or "Untitled")
        return f"{name}{' *' if self._dirty else ''}"

    # ---------------------------------------------------------------- 引擎
    def start_engine(self) -> bool:
        if self.engine is not None:
            return True
        kind = self.config.get("audio.backend", "auto")
        block = int(self.config.get("audio.buffer_frames", 256))
        device = self.config.get("audio.device", "") or None
        try:
            be = backend_mod.create_backend(kind)
            self.backend = be
            # 设备采样率由后端决定；engine 打开时再对齐
            be.open(sample_rate=self.project.sample_rate, block_frames=block, device=device)
            info = be.info()
            sr = int(info.sample_rate or self.project.sample_rate)
            self.eq_pool = EqPool(be, sr)
            self.mixer = Mixer(int(info.block_frames or block), sr, self.eq_pool)
            self.engine = Engine(be, project_sample_rate=self.project.sample_rate,
                                 block_frames=int(info.block_frames or block), mixer=self.mixer)
            self.engine.open()
            self.engine.set_master_gain(10 ** (self.master_gain_db / 20.0))
            self.devices = backend_mod.list_devices(be)
            log.info("engine up: %s", self.engine.info())
            self.deviceChanged.emit()
            return True
        except Exception as e:
            log.exception("engine start failed")
            self.engine = None
            self.status.emit(f"Audio device unavailable: {e}")
            self.deviceChanged.emit()
            return False

    def stop_engine(self) -> None:
        if self.engine is not None:
            self.engine.close()
            self.engine = None
            self.mixer = None

    def engine_info(self) -> dict:
        if self.engine is None:
            return {"backend": "none", "device_rate": 0, "xruns": 0, "latency_ms": 0.0}
        return self.engine.info()

    # ---------------------------------------------------------------- 撤销事务
    def _snapshot(self) -> dict:
        d = self.project.to_dict()
        d["__path__"] = str(self.project.path) if self.project.path else ""
        return d

    def _restore(self, d: dict) -> None:
        path = d.get("__path__", "")
        d = {k: v for k, v in d.items() if k != "__path__"}
        self.project = Project.from_dict(d)
        self.project.path = Path(path) if path else None
        self.rebuild_plan()
        self.structureChanged.emit()
        self.projectChanged.emit("all")

    @contextmanager
    def edit(self, label: str):
        self.undo.begin(label)
        try:
            yield self.project
        finally:
            changed = self.undo.commit()
            if changed:
                self._dirty = True
                self.rebuild_plan()
                self.structureChanged.emit()
                self.projectChanged.emit("all")

    # ---------------------------------------------------------------- 连续参数
    def live_begin(self, label: str) -> None:
        self.undo.begin(label)

    def live_touch(self, *, rebuild: bool = True) -> None:
        """拖动中的实时更新：不重建控件，只重建渲染计划并广播数值变化。"""
        self._dirty = True
        if rebuild:
            self.rebuild_plan()
        self.paramsChanged.emit()

    def live_end(self) -> None:
        if self.undo.commit():
            self._dirty = True
            self.rebuild_plan()
            self.paramsChanged.emit()

    def undo_last(self) -> None:
        label = self.undo.undo()
        if label:
            self._dirty = True
            self.rebuild_plan()
            self.structureChanged.emit()
            self.projectChanged.emit("all")
            self.status.emit(f"Undo: {label}")

    def redo_last(self) -> None:
        label = self.undo.redo()
        if label:
            self._dirty = True
            self.rebuild_plan()
            self.structureChanged.emit()
            self.projectChanged.emit("all")
            self.status.emit(f"Redo: {label}")

    # ---------------------------------------------------------------- 工程
    def new_project(self, sample_rate: int | None = None) -> None:
        sr = int(sample_rate or self.config.get("audio.project_sample_rate", 44100))
        self.project = Project(sample_rate=sr)
        self._sources.clear()
        self._peaks.clear()
        self.undo.clear()
        self._dirty = False
        self.rebuild_plan()
        self.structureChanged.emit()
        self.projectChanged.emit("all")

    def open_project(self, path: str | Path) -> list[str]:
        proj, missing = Project.load(path)
        self.project = proj
        self._sources.clear()
        self._peaks.clear()
        self.undo.clear()
        self._dirty = False
        # 立刻把工程里引用的音频准备好（已缓存的近乎瞬时）
        for t in proj.tracks:
            for c in t.clips:
                if c.path and Path(c.path).exists():
                    self.ensure_source(c.path)
        self.rebuild_plan()
        self.config.set("session.last_project", str(proj.path), save=True)
        self.structureChanged.emit()
        self.projectChanged.emit("all")
        return missing

    def save_project(self, path: str | Path | None = None) -> Path | None:
        target = Path(path) if path else self.project.path
        if target is None:
            return None
        saved = self.project.save(target)
        self.config.set("session.last_project", str(saved), save=True)
        self._dirty = False
        self.clear_draft()
        self.status.emit(f"Saved: {saved.name}")
        return saved

    # ---------------------------------------------------------------- 崩溃恢复
    def draft_path(self) -> Path:
        return Path(self.paths.config_dir) / "draft.abproj"

    def autosave_draft(self) -> None:
        """每 30s 落一份草稿；异常退出后下次启动可恢复（§3-P0-7）。"""
        if not self._dirty or not self.project.tracks:
            return
        try:
            import json

            draft = self.to_dict_for_draft()
            self.draft_path().write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            log.debug("draft saved (%d bytes)", self.draft_path().stat().st_size)
        except Exception as e:
            log.warning("draft autosave failed: %s", e)

    def to_dict_for_draft(self) -> dict:
        d = self.project.to_dict()
        d["__path__"] = str(self.project.path) if self.project.path else ""
        d["__saved_at__"] = time.time()
        return d

    def has_draft(self) -> bool:
        p = self.draft_path()
        if not p.exists():
            return False
        try:
            import json

            data = json.loads(p.read_text(encoding="utf-8"))
            return bool(data.get("tracks"))
        except Exception:
            return False

    def load_draft(self) -> bool:
        p = self.draft_path()
        if not p.exists():
            return False
        import json

        data = json.loads(p.read_text(encoding="utf-8"))
        path = data.pop("__path__", "")
        data.pop("__saved_at__", None)
        try:
            self.project = Project.from_dict(data)
            self.project.path = Path(path) if path else None
        except Exception:
            log.exception("draft load failed")
            return False
        for t in self.project.tracks:
            for c in t.clips:
                if c.path and Path(c.path).exists():
                    self.ensure_source(c.path)
        self.undo.clear()
        self._dirty = True
        self.rebuild_plan()
        self.structureChanged.emit()
        self.projectChanged.emit("all")
        return True

    def clear_draft(self) -> None:
        try:
            p = self.draft_path()
            if p.exists():
                p.unlink()
        except OSError:
            pass

    # ---------------------------------------------------------------- 轨道/片段
    def add_track(self, name: str = "", instrument: str = "other") -> Track:
        if len(self.project.tracks) >= MAX_TRACKS:
            self.errorRaised.emit("AudioBench", f"Track limit reached ({MAX_TRACKS})")
            return self.project.tracks[-1]
        with self.edit("Add track"):
            n = len(self.project.tracks) + 1
            t = self.project.add_track(Track(name=name or f"Track {n}",
                                             color=self.project.next_color(),
                                             instrument=instrument))
        return t

    def remove_track(self, index: int) -> None:
        with self.edit("Delete track"):
            self.project.remove_track(index)

    def move_track(self, src: int, dst: int) -> None:
        with self.edit("Reorder tracks"):
            self.project.move_track(src, dst)

    # ---------------------------------------------------------------- 音频源
    def ensure_source(self, path: str) -> object | None:
        path = str(path)
        src = self._sources.get(path)
        if src is not None:
            return src
        try:
            dec = self.decode.lookup(path, self.project.sample_rate)
            if dec is None:
                dec = self.decode.decode(path, self.project.sample_rate)
            src = self.decode.source(dec)
            self._sources[path] = src
            return src
        except FfmpegError as e:
            log.warning("cannot decode %s: %s", path, e)
            return None
        except Exception as e:
            log.exception("ensure_source failed: %s", e)
            return None

    def peaks_for(self, path: str) -> PeakCache | None:
        path = str(path)
        hit = self._peaks.get(path)
        if hit is not None:
            return hit
        dec = self.decode.lookup(path, self.project.sample_rate)
        if dec is None:
            return None
        pc = self.decode.peaks(dec)
        if pc is not None:
            self._peaks[path] = pc
        return pc

    # ---------------------------------------------------------------- 计划
    def rebuild_plan(self) -> None:
        self._plan_stamp += 1
        plan = build_plan(self.project, self._resolve_clip, stamp=self._plan_stamp)
        self.plan = plan
        if self.engine is not None:
            self.engine.set_plan(plan)

    def _resolve_clip(self, clip):
        return self._sources.get(str(clip.path))

    # ---------------------------------------------------------------- 导出/选中
    def export_resolve(self, clip):
        """导出时的解析器：缺源就先补齐（导出不能因为没预载而静音）。"""
        src = self._sources.get(str(clip.path))
        if src is None and clip.path:
            src = self.ensure_source(clip.path)
        return src

    def selected_track_index(self) -> int:
        return int(getattr(self, "_selected_track", -1))

    def set_selected_track(self, idx: int) -> None:
        self._selected_track = int(idx)

    def refresh_engine_settings(self) -> None:
        if self.engine is None:
            return
        self.engine.set_master_gain(10 ** (self.master_gain_db / 20.0))
        self.engine.set_bpm(float(self.config.get("ui.bpm", 120.0) or 120.0))

    # ---------------------------------------------------------------- 传输
    def play(self) -> None:
        if self.engine is None and not self.start_engine():
            return
        assert self.engine is not None
        if self.engine.position >= max(self.plan.end if self.plan else 0, 1):
            self.engine.seek(0)
        self.engine.play()
        self.transportChanged.emit()

    def stop(self) -> None:
        if self.engine is not None:
            self.engine.stop()
        self.transportChanged.emit()

    def toggle(self) -> None:
        if self.engine is not None and self.engine.playing:
            self.stop()
        else:
            self.play()

    def seek(self, sample: int) -> None:
        if self.engine is None:
            self.start_engine()
        if self.engine is not None:
            self.engine.seek(sample)
            self.transportChanged.emit()

    def set_loop(self, a: int | None, b: int | None) -> None:
        if self.engine is not None:
            self.engine.set_loop(a, b)
            self.transportChanged.emit()

    def set_master_db(self, db: float) -> None:
        self.master_gain_db = max(-60.0, min(12.0, float(db)))
        if self.engine is not None:
            self.engine.set_master_gain(10 ** (self.master_gain_db / 20.0))

    def set_metronome(self, on: bool) -> None:
        self.config.set("audio.metronome", bool(on))
        if self.engine is not None:
            self.engine.set_metronome(on)

    def position(self) -> int:
        return int(self.engine.position) if self.engine is not None else 0

    def duration(self) -> int:
        return max(self.project.duration, self.project.sample_rate * 10)

    # ---------------------------------------------------------------- 信息
    def summary(self) -> dict:
        return {
            "version": VERSION,
            "project": {
                "name": self.project.name,
                "path": str(self.project.path) if self.project.path else "",
                "sample_rate": self.project.sample_rate,
                "tracks": len(self.project.tracks),
                "duration_s": round(self.project.duration / max(1, self.project.sample_rate), 2),
                "dirty": self._dirty,
            },
            "engine": self.engine_info(),
            "undo": {"undo": self.undo.depth()[0], "redo": self.undo.depth()[1]},
        }


class ImportJob(QThread):
    """把解码放到后台线程，UI 只收进度（导入 5 分钟文件也不掉帧）。"""

    progress = Signal(int, int, str, float)   # index, total, name, frac
    finished_ok = Signal(list)                # list[Decoded]
    failed = Signal(list)                     # list[(path, message)]

    def __init__(self, service: DecodeService, sample_rate: int, paths: list[str], parent=None):
        super().__init__(parent)
        self.service = service
        self.sample_rate = sample_rate
        self.paths = paths
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        ok: list[Decoded] = []
        bad: list[tuple[str, str]] = []
        total = len(self.paths)
        for i, p in enumerate(self.paths):
            if self._cancel.is_set():
                break
            name = Path(p).name
            try:
                dec = self.service.ensure(
                    p, self.sample_rate,
                    on_progress=lambda frac, _stage, i=i, name=name:
                        self.progress.emit(i, total, name, float(frac)),
                    cancel=self._cancel,
                )
                ok.append(dec)
            except FfmpegError as e:
                bad.append((p, e.code))
            except Exception as e:  # 单个文件失败不影响其它
                bad.append((p, f"{type(e).__name__}: {e}"))
        self.finished_ok.emit(ok)
        if bad:
            self.failed.emit(bad)
