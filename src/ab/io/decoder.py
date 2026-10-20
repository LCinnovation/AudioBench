"""导入流水线：ffprobe → ffmpeg 解码成 f32 → 峰值金字塔 → 片段信息。

缓存键 = sha1(绝对路径 + mtime + size + 采样率)，所以「改文件/换工程采样率」都会自然失效。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..app import paths as app_paths
from . import ffmpeg, peakcache
from .waveshim import AudioSource, SourceRegistry

log = logging.getLogger("ab.decoder")

ProgressFn = Callable[[float, str], None]


@dataclass(frozen=True)
class Decoded:
    source_path: str
    f32_path: Path
    peaks_path: Path
    frames: int
    sample_rate: int
    channels: int

    @property
    def duration_s(self) -> float:
        return self.frames / float(self.sample_rate or 1)


def _sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:20]


class DecodeService:
    def __init__(self, cache_dir: str | Path, registry: SourceRegistry | None = None):
        self.cache_dir = Path(cache_dir)
        self.decoded_dir = self.cache_dir / "decoded"
        self.peak_dir = self.cache_dir / "waveforms"
        self.decoded_dir.mkdir(parents=True, exist_ok=True)
        self.peak_dir.mkdir(parents=True, exist_ok=True)
        self.registry = registry or SourceRegistry()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 键与命中
    def key_for(self, path: str | Path, sample_rate: int) -> str:
        p = Path(path).resolve()
        try:
            st = p.stat()
            fingerprint = f"{p}|{st.st_size}|{int(st.st_mtime)}|{sample_rate}"
        except OSError:
            fingerprint = f"{p}|missing|{sample_rate}"
        return _sha1_text(fingerprint)

    def _meta_path(self, key: str) -> Path:
        return self.decoded_dir / f"{key}.json"

    def lookup(self, path: str | Path, sample_rate: int) -> Decoded | None:
        key = self.key_for(path, sample_rate)
        mp = self._meta_path(key)
        if not mp.exists():
            return None
        try:
            d = json.loads(mp.read_text(encoding="utf-8"))
            f32 = self.decoded_dir / d["f32"]
            peaks = self.peak_dir / d["peaks"]
            if not f32.exists():
                return None
            frames = int(d["frames"])
            if f32.stat().st_size < frames * 2 * 4:
                return None
            return Decoded(source_path=d["source"], f32_path=f32, peaks_path=peaks,
                           frames=frames, sample_rate=int(d["sample_rate"]),
                           channels=int(d.get("channels", 2)))
        except Exception as e:
            log.warning("cache meta unreadable: %s (%s)", mp, e)
            return None

    # ---------------------------------------------------------------- 解码
    def ensure(self, path: str | Path, sample_rate: int, *,
               on_progress: ProgressFn | None = None,
               cancel: threading.Event | None = None) -> Decoded:
        hit = self.lookup(path, sample_rate)
        if hit is not None and hit.peaks_path.exists():
            if on_progress:
                on_progress(1.0, "cached")
            return hit
        return self.decode(path, sample_rate, on_progress=on_progress, cancel=cancel)

    def decode(self, path: str | Path, sample_rate: int, *,
               on_progress: ProgressFn | None = None,
               cancel: threading.Event | None = None) -> Decoded:
        src = str(Path(path).resolve())
        info = ffmpeg.probe(src)
        key = self.key_for(src, sample_rate)
        f32 = self.decoded_dir / f"{key}.f32"
        peaks = self.peak_dir / f"{key}.abpk"

        def dec_progress(frac: float) -> None:
            if on_progress:
                on_progress(frac * 0.85, "decode")

        t0 = time.perf_counter()
        with self._lock:
            ffmpeg.decode_to_f32(src, f32, sample_rate, on_progress=dec_progress,
                                 cancel=cancel, duration_s=info.duration_s or None)
        decode_s = time.perf_counter() - t0

        frames = f32.stat().st_size // (2 * 4)
        if frames <= 0:
            raise ffmpeg.FfmpegError("decode_empty")

        def peak_progress(frac: float) -> None:
            if on_progress:
                on_progress(0.85 + frac * 0.15, "peaks")

        if cancel is not None and cancel.is_set():
            raise ffmpeg.FfmpegError("cancelled")
        t1 = time.perf_counter()
        peakcache.build(f32, frames, peaks, sample_rate=sample_rate,
                        channels=2, progress=peak_progress)
        peak_s = time.perf_counter() - t1

        d = Decoded(source_path=src, f32_path=f32, peaks_path=peaks, frames=frames,
                    sample_rate=sample_rate, channels=2)
        self._meta_path(key).write_text(json.dumps({
            "source": src, "f32": f32.name, "peaks": peaks.name, "frames": frames,
            "sample_rate": sample_rate, "channels": 2,
            "source_duration": info.duration_s, "codec": info.codec,
            "decode_s": round(decode_s, 2), "peaks_s": round(peak_s, 2),
        }, ensure_ascii=False), encoding="utf-8")
        log.info("decoded %s -> %s (%d frames) decode=%.2fs peaks=%.2fs",
                 src, f32.name, frames, decode_s, peak_s)
        if on_progress:
            on_progress(1.0, "done")
        return d

    # ---------------------------------------------------------------- 访问
    def source(self, decoded: Decoded) -> AudioSource:
        return self.registry.get(str(decoded.f32_path), decoded.frames)

    def peaks(self, decoded: Decoded) -> peakcache.PeakCache | None:
        return peakcache.PeakCache.open(decoded.peaks_path)


def sample_fixture(seconds: float = 3.0, sample_rate: int = 44100, freq: float = 440.0,
                   path: str | Path | None = None) -> Path:
    """生成一个 16bit 立体声 WAV（自检 / 测试 / 首次启动示例用）。"""
    import struct
    import wave

    import numpy as np

    n = int(seconds * sample_rate)
    t = np.arange(n, dtype=np.float32) / sample_rate
    left = 0.35 * np.sin(2 * np.pi * freq * t)
    right = 0.35 * np.sin(2 * np.pi * freq * 1.5 * t)
    pcm = np.stack([left, right], axis=1)
    pcm16 = np.clip(pcm * 32767, -32768, 32767).astype("<i2")

    out = Path(path) if path else Path(app_paths.app_dir()) / "build-env" / f"fixture_{int(seconds)}s.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    log.info("fixture written: %s (%d frames)", out, n)
    return out


__all__ = ["Decoded", "DecodeService", "sample_fixture", "struct"]
