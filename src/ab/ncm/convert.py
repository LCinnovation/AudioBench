"""NCM 转换（§3-P0-8）：解码 → 转码/写标签/封面 → 落盘。

只做「用户本地已有文件的格式转换」，不做任何在线解析（§8）。
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..io import ffmpeg
from . import decoder

log = logging.getLogger("ab.ncm.convert")

OUT_FORMATS = {
    "mp3_320": {"ext": "mp3", "args": ["-c:a", "libmp3lame", "-b:a", "320k"], "label": "MP3 320k"},
    "mp3_192": {"ext": "mp3", "args": ["-c:a", "libmp3lame", "-b:a", "192k"], "label": "MP3 192k"},
    "mp3_128": {"ext": "mp3", "args": ["-c:a", "libmp3lame", "-b:a", "128k"], "label": "MP3 128k"},
    "flac": {"ext": "flac", "args": ["-c:a", "flac", "-compression_level", "5"], "label": "FLAC"},
    "keep": {"ext": "", "args": [], "label": "保持原格式（仅修复标签）"},
}


@dataclass
class NcmJob:
    src: Path
    out_dir: Path
    fmt: str = "mp3_320"
    status: str = "pending"      # pending | running | done | failed
    message: str = ""
    out_path: Path | None = None


def _unique_out(out_dir: Path, stem: str, ext: str) -> Path:
    """同名冲突时依次尝试 `name (2)`、`name (3)`…，**绝不覆盖已有输出**。

    旧实现只试一次 `(2)`，第三次转换会静默覆盖第二次的结果。
    """
    out = out_dir / f"{stem}.{ext}"
    n = 2
    while out.exists():
        out = out_dir / f"{stem} ({n}).{ext}"
        n += 1
        if n > 999:
            raise ffmpeg.FfmpegError("output_name_conflict")
    return out


def convert(job: NcmJob, *, progress=None, cancel=None) -> Path:
    """执行一个转换任务；失败抛带可读信息的异常。"""
    spec = OUT_FORMATS.get(job.fmt) or OUT_FORMATS["mp3_320"]
    job.status = "running"
    data = decoder.read_file(job.src)
    payload = data["payload"]
    meta = decoder.friendly_meta(data["metadata"])
    cover = data["cover"] or None

    ext = spec["ext"] or data["container"]
    if ext not in ("mp3", "flac"):
        ext = "mp3"
    stem = job.src.stem
    job.out_dir.mkdir(parents=True, exist_ok=True)
    out = _unique_out(job.out_dir, stem, ext)

    tmp = Path(tempfile.gettempdir()) / f"ab_ncm_{abs(hash(str(job.src))) % 10_000_000}.{data['container']}"
    tmp.write_bytes(payload)
    try:
        if not spec["args"] and data["container"] == ext:
            # 原样保留：只把标签/封面写进去
            args = ["-c:a", "copy"]
        else:
            args = list(spec["args"])
        ffmpeg.transcode(tmp, out, extra_args=args, meta=meta, cover=cover,
                         on_progress=progress, cancel=cancel)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    # 截断交叉校验：FLAC 的 STREAMINFO 会声称完整长度，实际解码却更短
    expected = decoder.expected_samples(payload)
    if expected:
        try:
            info = ffmpeg.probe(out)
            got = int(round(info.duration_s * info.sample_rate)) if info.sample_rate else 0
        except Exception:
            got = 0
        if got and got < expected * 0.99:
            job.status = "failed"
            job.message = f"payload truncated ({got}/{expected} samples)"
            try:
                out.unlink()
            except OSError:
                pass
            raise ffmpeg.FfmpegError("payload_truncated")

    job.out_path = out
    job.status = "done"
    job.message = ""
    return out


def convert_many(items: list[NcmJob], *, on_item=None, cancel=None) -> list[NcmJob]:
    for job in items:
        if cancel is not None and cancel.is_set():
            job.status = "failed"
            job.message = "cancelled"
            continue
        try:
            convert(job, cancel=cancel)
            if on_item:
                on_item(job)
        except ffmpeg.FfmpegError as e:
            job.status = "failed"
            job.message = e.code or str(e)
            log.warning("ncm convert failed: %s (%s)", job.src, e)
            if on_item:
                on_item(job)
        except Exception as e:
            job.status = "failed"
            job.message = f"{type(e).__name__}: {e}"
            log.exception("ncm convert failed")
            if on_item:
                on_item(job)
    return items


def failed_csv(items: list[NcmJob]) -> str:
    lines = ["file,error"]
    for it in items:
        if it.status == "failed":
            lines.append(f'"{it.src.name}","{it.message}"')
    return "\n".join(lines)
