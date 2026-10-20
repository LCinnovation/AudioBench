"""ffmpeg / ffprobe 子进程封装（§3-P0-1、§5）。

规矩：
  * 只走 CLI，不链接 ffmpeg 库（LGPL 合规）；
  * 进度走 `-progress pipe:1`，取消走 terminate + 清半成品；
  * 错误信息必须能翻译成用户可读的双语（用固定错误码，不用 stderr 原文当文案）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..app.paths import app_dir

log = logging.getLogger("ab.ffmpeg")

CREATE_NO_WINDOW = 0x08000000
ProgressFn = Callable[[float], None]  # 0..1


class FfmpegError(RuntimeError):
    """带错误码的可读错误：UI 用 code 查翻译，detail 只进日志。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _candidates(name: str) -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("AB_FFMPEG_DIR")
    if env:
        out.append(Path(env) / f"{name}.exe")
    bases = [app_dir() / "tools" / "ffmpeg"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass) / "tools" / "ffmpeg")
    for base in bases:
        out.append(base / "bin" / f"{name}.exe")
        out.append(base / f"{name}.exe")
    return out


def find_tool(name: str) -> Path | None:
    for p in _candidates(name):
        if p.exists():
            return p
    which = shutil.which(name)
    return Path(which) if which else None


def ffmpeg_path() -> Path | None:
    return find_tool("ffmpeg")


def ffprobe_path() -> Path | None:
    return find_tool("ffprobe")


def version_info() -> dict:
    exe = ffmpeg_path()
    if not exe:
        return {"path": None, "version": None, "has_libmp3lame": False, "gpl": False}
    try:
        out = subprocess.run([str(exe), "-hide_banner", "-version"], capture_output=True,
                             text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
        first = (out.stdout or "").splitlines()[0] if out.stdout else ""
        cfg = ""
        for line in (out.stdout or "").splitlines():
            if line.startswith("configuration:"):
                cfg = line
                break
        return {
            "path": str(exe),
            "version": first.split(" Copyright")[0].replace("ffmpeg version ", "").strip(),
            "has_libmp3lame": "--enable-libmp3lame" in cfg,
            "gpl": "--enable-gpl" in cfg,
            "configuration": cfg,
        }
    except Exception as e:  # pragma: no cover
        log.warning("ffmpeg -version failed: %s", e)
        return {"path": str(exe), "version": None, "has_libmp3lame": False, "gpl": False}


@dataclass
class MediaInfo:
    duration_s: float
    sample_rate: int
    channels: int
    codec: str
    format_name: str


def probe(path: str | Path) -> MediaInfo:
    exe = ffprobe_path()
    if not exe:
        raise FfmpegError("ffmpeg_missing")
    cmd = [str(exe), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                             creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        raise FfmpegError("probe_failed", str(e)) from None
    if out.returncode != 0:
        raise FfmpegError("probe_failed", (out.stderr or "")[-600:])
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        raise FfmpegError("probe_failed", "invalid json") from None
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise FfmpegError("no_audio_stream")
    s = streams[0]
    fmt = data.get("format", {})
    try:
        dur = float(fmt.get("duration") or s.get("duration") or 0.0)
    except ValueError:
        dur = 0.0
    return MediaInfo(
        duration_s=dur,
        sample_rate=int(s.get("sample_rate") or 0),
        channels=int(s.get("channels") or 0),
        codec=str(s.get("codec_name") or ""),
        format_name=str(fmt.get("format_name") or ""),
    )


def _progress_loop(cmd: list[str], tmp: Path, dst: Path, *,
                   on_progress: ProgressFn | None, cancel: threading.Event | None,
                   duration_s: float | None, code: str) -> Path:
    """跑一次 ffmpeg：解析 -progress，结束后原子替换。"""
    log.info("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=CREATE_NO_WINDOW)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if cancel is not None and cancel.is_set():
                proc.terminate()
                raise FfmpegError("cancelled")
            if line.startswith("out_time_us=") and on_progress and duration_s:
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                on_progress(min(0.999, max(0.0, (us / 1_000_000.0) / duration_s)))
        rc = proc.wait()
        if rc != 0:
            raise FfmpegError(code, (proc.stderr.read() if proc.stderr else "")[-600:])
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise FfmpegError(code, "no output")
    os.replace(tmp, dst)
    if on_progress:
        on_progress(1.0)
    return dst


def transcode(src: str | Path, dst: str | Path, *, extra_args: list[str],
              meta: dict | None = None, cover: bytes | None = None,
              on_progress: ProgressFn | None = None,
              cancel: threading.Event | None = None,
              duration_s: float | None = None) -> Path:
    """任意输入 → 目标格式（可带元数据与封面）。用于 ncm 转换与通用重编码。"""
    exe = ffmpeg_path()
    if not exe:
        raise FfmpegError("ffmpeg_missing")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    cmd = [str(exe), "-nostdin", "-v", "error", "-y", "-i", str(src)]
    cover_path = None
    if cover:
        cover_path = dst.with_name(dst.stem + ".cover.tmp")
        cover_path.write_bytes(cover)
        cmd += ["-i", str(cover_path)]
    cmd += ["-map", "0:a:0"]
    if cover:
        cmd += ["-map", "1:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    cmd += ["-map_metadata", "-1"]
    for k, v in (meta or {}).items():
        if v not in (None, ""):
            cmd += ["-metadata", f"{k}={v}"]
    if dst.suffix.lower() == ".mp3":
        cmd += ["-id3v2_version", "3"]
    cmd += list(extra_args)
    cmd += ["-progress", "pipe:1", "-nostats", str(tmp)]
    try:
        return _progress_loop(cmd, tmp, dst, on_progress=on_progress, cancel=cancel,
                              duration_s=duration_s, code="transcode_failed")
    finally:
        if cover_path and cover_path.exists():
            try:
                cover_path.unlink()
            except OSError:
                pass


def encode_f32(src_f32: str | Path, dst: str | Path, *, sample_rate: int,
               extra_args: list[str], meta: dict | None = None,
               cover: bytes | None = None,
               on_progress: ProgressFn | None = None,
               cancel: threading.Event | None = None,
               duration_s: float | None = None) -> Path:
    """把裸 f32le 编成目标格式；可选写入元数据与封面（ncm 转换复用）。"""
    exe = ffmpeg_path()
    if not exe:
        raise FfmpegError("ffmpeg_missing")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件必须保留真实扩展名：ffmpeg 靠扩展名选 muxer（`.part` 结尾会报错）
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    cmd = [str(exe), "-nostdin", "-v", "error", "-y",
           "-f", "f32le", "-ar", str(int(sample_rate)), "-ac", "2", "-i", str(src_f32)]
    cover_path = None
    if cover:
        cover_path = dst.with_name(dst.name + ".cover.tmp")
        cover_path.write_bytes(cover)
        cmd += ["-i", str(cover_path)]
    cmd += ["-map", "0:a:0"]
    if cover:
        cmd += ["-map", "1:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    cmd += ["-map_metadata", "-1"]
    for k, v in (meta or {}).items():
        if v not in (None, ""):
            cmd += ["-metadata", f"{k}={v}"]
    cmd += list(extra_args)
    cmd += ["-progress", "pipe:1", "-nostats", str(tmp)]
    log.info("encode: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=CREATE_NO_WINDOW)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if cancel is not None and cancel.is_set():
                proc.terminate()
                raise FfmpegError("cancelled")
            if line.startswith("out_time_us=") and on_progress and duration_s:
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                on_progress(min(0.999, max(0.0, (us / 1_000_000.0) / duration_s)))
        rc = proc.wait()
        if rc != 0:
            raise FfmpegError("encode_failed", (proc.stderr.read() if proc.stderr else "")[-600:])
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass
        if cover_path and cover_path.exists():
            try:
                cover_path.unlink()
            except OSError:
                pass
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise FfmpegError("encode_failed", "no output")
    os.replace(tmp, dst)
    if on_progress:
        on_progress(1.0)
    return dst


def decode_to_f32(src: str | Path, dst: str | Path, sample_rate: int, *,
                  on_progress: ProgressFn | None = None,
                  cancel: threading.Event | None = None,
                  duration_s: float | None = None) -> Path:
    """解码成裸 f32le（交错 2 声道），返回输出路径。失败抛 FfmpegError。"""
    exe = ffmpeg_path()
    if not exe:
        raise FfmpegError("ffmpeg_missing")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    cmd = [
        str(exe), "-nostdin", "-v", "error", "-y",
        "-i", str(src), "-map", "0:a:0", "-vn",
        "-ac", "2", "-ar", str(int(sample_rate)),
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-progress", "pipe:1", "-nostats", str(tmp),
    ]
    log.info("decode: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=CREATE_NO_WINDOW)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if cancel is not None and cancel.is_set():
                proc.terminate()
                raise FfmpegError("cancelled")
            if line.startswith("out_time_us=") and on_progress and duration_s:
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                frac = min(0.999, max(0.0, (us / 1_000_000.0) / duration_s))
                on_progress(frac)
        rc = proc.wait()
        if rc != 0:
            err = (proc.stderr.read() if proc.stderr else "")[-800:]
            raise FfmpegError("decode_failed", err)
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass
    if not tmp.exists():
        raise FfmpegError("decode_failed", "no output")
    os.replace(tmp, dst)
    if on_progress:
        on_progress(1.0)
    return dst
