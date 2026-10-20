"""时间显示格式（§4.2）：`00:00:00.000` ↔ `001.01.000`，双击切换。"""

from __future__ import annotations


def samples_to_time(samples: int, sr: int) -> str:
    if sr <= 0:
        sr = 44100
    total_ms = int(round(samples * 1000.0 / sr))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    m = (total_s // 60) % 60
    h = total_s // 3600
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def samples_to_bars(samples: int, sr: int, bpm: float, beats_per_bar: int = 4) -> str:
    if sr <= 0:
        sr = 44100
    bpm = bpm if bpm > 1 else 120.0
    beats = samples / sr * (bpm / 60.0)
    bar = int(beats // beats_per_bar) + 1
    beat = int(beats % beats_per_bar) + 1
    ticks = int(round((beats % 1.0) * 960))
    return f"{bar:03d}.{beat:02d}.{ticks:03d}"


def time_to_samples(text: str, sr: int) -> int | None:
    """把 `mm:ss.mmm` / `hh:mm:ss.mmm` 解析回采样点（用户手输时用）。"""
    try:
        parts = text.replace(",", ".").split(":")
        if len(parts) == 1:
            secs = float(parts[0])
        elif len(parts) == 2:
            secs = int(parts[0]) * 60 + float(parts[1])
        else:
            secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        return max(0, int(secs * sr))
    except (ValueError, IndexError):
        return None


__all__ = ["samples_to_time", "samples_to_bars", "time_to_samples"]
