"""导出：离线渲染 → ffmpeg 编码（§3-P0-6）。

设计要点：
  * 离线渲染走**同一套 Mixer**（块 4096），所以实时听到什么、导出就是什么；
  * EQ 用 `EqDsp`（同一个 C 内核，但不打开声卡）；
  * 先写裸 f32 临时文件再交给 ffmpeg，取消/失败都不会留半个 mp3；
  * 命名模板 `{track}_{index}_{date}`，文件名做文件系统安全处理。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..audio.dsp import EqDsp, EqPool
from ..audio.mixer import Mixer
from ..audio.renderplan import RenderPlan, build_plan
from ..io import ffmpeg
from ..model.project import Project

log = logging.getLogger("ab.exporter")

BLOCK = 4096
FORMATS = {
    "mp3_320": {"ext": "mp3", "args": ["-c:a", "libmp3lame", "-b:a", "320k"], "label": "MP3 320k"},
    "mp3_192": {"ext": "mp3", "args": ["-c:a", "libmp3lame", "-b:a", "192k"], "label": "MP3 192k"},
    "mp3_128": {"ext": "mp3", "args": ["-c:a", "libmp3lame", "-b:a", "128k"], "label": "MP3 128k"},
    "wav16": {"ext": "wav", "args": ["-c:a", "pcm_s16le"], "label": "WAV 16bit"},
    "wav24": {"ext": "wav", "args": ["-c:a", "pcm_s24le"], "label": "WAV 24bit"},
    "flac": {"ext": "flac", "args": ["-c:a", "flac", "-compression_level", "5"], "label": "FLAC"},
}

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class ExportSettings:
    fmt: str = "mp3_320"
    out_dir: Path = field(default_factory=lambda: Path.home())
    template: str = "{track}_{index}_{date}"
    scope: str = "mix"          # mix | track | selected
    sample_rate: int = 44100
    normalize_peak: bool = False   # 预留：不做自动限幅，避免改变听感


@dataclass
class ExportResult:
    files: list[Path] = field(default_factory=list)
    peak: float = 0.0
    clipped: bool = False
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def safe_name(text: str, *, fallback: str = "AudioBench", limit: int = 120) -> str:
    name = _UNSAFE.sub("_", str(text or "")).strip(" .")
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = fallback
    return name[:limit]


def render_name(template: str, *, track: str, index: int, date: str | None = None) -> str:
    date = date or time.strftime("%Y%m%d")
    try:
        raw = template.format(track=track, index=f"{index:02d}", date=date)
    except (KeyError, IndexError, ValueError):
        raw = f"{track}_{index:02d}_{date}"
    return safe_name(raw)


def render_to_f32(project: Project, plan: RenderPlan, out_path: Path, *,
                  master_gain: float = 1.0, block: int = BLOCK,
                  progress: Callable[[float], None] | None = None,
                  cancel: threading.Event | None = None) -> tuple[int, float]:
    """把 plan 渲染成裸 f32le 文件；返回 (帧数, 峰值)。"""
    sr = plan.sample_rate
    total = max(plan.end, 1)
    eq = EqPool(EqDsp(), sr)          # 不连声卡，只借 C 内核
    mixer = Mixer(block, sr, eq)
    buf = np.zeros((block, 2), dtype=np.float32)
    peak = 0.0
    written = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        pos = 0
        while pos < plan.end:
            n = min(block, plan.end - pos)
            view = buf[:n]
            mixer.render(plan, pos, view, master_gain=master_gain)
            if n:
                peak = max(peak, float(np.abs(view).max()))
                f.write(np.ascontiguousarray(view).tobytes())
                written += n
            pos += n
            if cancel is not None and cancel.is_set():
                raise ffmpeg.FfmpegError("cancelled")
            if progress and plan.end:
                progress(min(0.99, pos / plan.end))
    _ = total
    if progress:
        progress(1.0)
    return written, peak


def export_plan(project: Project, plan: RenderPlan, filename: str, settings: ExportSettings, *,
                master_gain: float = 1.0,
                progress: Callable[[float], None] | None = None,
                cancel: threading.Event | None = None) -> tuple[Path, float]:
    """渲染并编码，返回 (输出路径, 真实峰值)。峰值必须回传给调用方做削波判断。"""
    spec = FORMATS.get(settings.fmt) or FORMATS["mp3_320"]
    out = Path(settings.out_dir) / f"{safe_name(filename)}.{spec['ext']}"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_f32 = out.with_suffix(".f32.tmp")
    try:
        frames, peak = render_to_f32(project, plan, tmp_f32, master_gain=master_gain,
                                     progress=(lambda p: progress(p * 0.7)) if progress else None,
                                     cancel=cancel)
        if frames <= 0:
            raise ffmpeg.FfmpegError("export_empty")
        ffmpeg.encode_f32(tmp_f32, out, sample_rate=project.sample_rate,
                          extra_args=spec["args"],
                          on_progress=(lambda p: progress(0.7 + p * 0.3)) if progress else None,
                          cancel=cancel, duration_s=frames / max(1, project.sample_rate))
        log.info("exported %s (peak=%.3f)", out.name, peak)
        return out, peak
    finally:
        try:
            if tmp_f32.exists():
                tmp_f32.unlink()
        except OSError:
            pass


def gain_from_db(master_gain_db: float) -> float:
    """母带推子（dB）→ 线性增益；-60dB 视为静音（与轨道推子口径一致）。"""
    db = float(master_gain_db)
    return 0.0 if db <= -60.0 else 10.0 ** (db / 20.0)


def export(project: Project, resolve, settings: ExportSettings, *,
           track_index: int | None = None,
           master_gain_db: float = 0.0,
           progress: Callable[[float], None] | None = None,
           cancel: threading.Event | None = None,
           open_after: bool = False) -> ExportResult:
    """按 scope 导出。resolve(clip) 返回 AudioSource（与状态层一致）。

    `master_gain_db` 必须由调用方传入当前主推子值，否则「导出」与「试听」不一致。

    约定：**任何可预期的失败都写进 result.errors 并返回，不向上抛异常**，
    这样 UI 和脚本拿到的都是一份可读的结果（以前空工程会抛 export_empty）。
    """
    result = ExportResult()
    t0 = time.perf_counter()
    master_gain = gain_from_db(master_gain_db)

    try:
        if settings.scope == "mix":
            target_project = project
            label, index = project.name or "AudioBench", 1
        else:
            if track_index is None or not (0 <= track_index < len(project.tracks)):
                # 之前这里会偷偷导成混音，用户以为拿到的是单轨
                result.errors.append("no track selected")
                return result
            single = Project(sample_rate=project.sample_rate, name=project.name)
            single.tracks = [project.tracks[track_index]]
            target_project = single
            label, index = project.tracks[track_index].name or "track", track_index + 1

        # 源文件缺失：全缺就不出文件，部分缺就给警告（别再静默导出一段静音）
        def _has_source(c) -> bool:
            p = getattr(c, "path", "")
            if not p:
                return False
            # 先看文件在不在，避免对不存在的路径白跑一次 ffmpeg 探测；
            # 文件被移走但已解码进缓存的情况，靠 resolve() 兜底
            return Path(p).exists() or resolve(c) is not None

        clips = [c for t in target_project.tracks for c in t.clips]
        if not clips:
            result.errors.append("empty project — nothing to export")
            return result
        missing = [c for c in clips if not _has_source(c)]
        if len(missing) == len(clips):
            result.errors.append("all source audio is missing")
            return result
        if missing:
            result.warnings.append(
                f"{len(missing)} of {len(clips)} clips are missing their source file")

        plan = build_plan(target_project, resolve, stamp=int(time.time()))
        name = render_name(settings.template, track=label, index=index)
        path, peak = export_plan(target_project, plan, name, settings,
                                 master_gain=master_gain, progress=progress, cancel=cancel)
        result.files.append(path)
        result.peak = float(peak)
        result.clipped = result.peak > 1.0
    except ffmpeg.FfmpegError as e:
        code = str(e)
        friendly = {
            "export_empty": "nothing to render (empty timeline)",
            "cancelled": "cancelled",
        }.get(code, code)
        result.errors.append(friendly)
        result.files.clear()
    except Exception as e:                    # 兜底：导出失败不该带崩界面
        log.exception("export failed")
        result.errors.append(f"{type(e).__name__}: {e}")
        result.files.clear()

    result.seconds = time.perf_counter() - t0
    if result.files and open_after:
        reveal_path(result.files[0])
    return result


def reveal_path(path: Path) -> None:
    """在资源管理器里定位文件；Wine 下退化为打开目录。"""
    import subprocess
    import sys

    try:
        if sys.platform.startswith("win"):
            subprocess.run(["explorer", "/select,", str(path)], check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.run(["xdg-open", str(Path(path).parent)], check=False)
    except Exception:
        log.debug("reveal failed", exc_info=True)


def peak_scan(project: Project, resolve, *, master_gain: float = 1.0) -> float:
    """导出前扫一遍真峰值：>1.0 提示可能削波（不自动限幅）。"""
    plan = build_plan(project, resolve)
    if plan.end <= 0:
        return 0.0
    sr = plan.sample_rate
    eq = EqPool(EqDsp(), sr)
    mixer = Mixer(4096, sr, eq)
    buf = np.zeros((4096, 2), dtype=np.float32)
    peak = 0.0
    pos = 0
    while pos < plan.end:
        n = min(4096, plan.end - pos)
        mixer.render(plan, pos, buf[:n], master_gain=master_gain)
        if n:
            peak = max(peak, float(np.abs(buf[:n]).max()))
        pos += n
    return peak
