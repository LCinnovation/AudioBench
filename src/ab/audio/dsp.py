"""DSP：EQ 系数（RBJ 低/高架）与后端派发。

EQ 的逐采样递归部分放在 `ab_audio.dll` 的 C 里（§2.4 要求音频线程不做 Python 操作）；
没有 DLL 时降级为旁路，并在自检里如实标注 `eq=bypass`。
"""
from __future__ import annotations

import math

import numpy as np


def low_shelf(freq: float, gain_db: float, sr: float, q: float = 0.707) -> tuple[np.ndarray, np.ndarray]:
    """RBJ low-shelf（b, a），a 归一化为 [1, a1, a2]。"""
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * max(10.0, min(freq, sr * 0.49)) / sr
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2.0 * q)
    sq = 2.0 * math.sqrt(A) * alpha
    b0 = A * ((A + 1) - (A - 1) * cw + sq)
    b1 = 2 * A * ((A - 1) - (A + 1) * cw)
    b2 = A * ((A + 1) - (A - 1) * cw - sq)
    a0 = (A + 1) + (A - 1) * cw + sq
    a1 = -2 * ((A - 1) + (A + 1) * cw)
    a2 = (A + 1) + (A - 1) * cw - sq
    return (np.array([b0, b1, b2], dtype=np.float32) / a0,
            np.array([1.0, a1 / a0, a2 / a0], dtype=np.float32))


def high_shelf(freq: float, gain_db: float, sr: float, q: float = 0.707) -> tuple[np.ndarray, np.ndarray]:
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * max(10.0, min(freq, sr * 0.49)) / sr
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2.0 * q)
    sq = 2.0 * math.sqrt(A) * alpha
    b0 = A * ((A + 1) + (A - 1) * cw + sq)
    b1 = -2 * A * ((A - 1) + (A + 1) * cw)
    b2 = A * ((A + 1) + (A - 1) * cw - sq)
    a0 = (A + 1) - (A - 1) * cw + sq
    a1 = 2 * ((A - 1) - (A + 1) * cw)
    a2 = (A + 1) - (A - 1) * cw - sq
    return (np.array([b0, b1, b2], dtype=np.float32) / a0,
            np.array([1.0, a1 / a0, a2 / a0], dtype=np.float32))


LOW_FREQ = 120.0
HIGH_FREQ = 6000.0
MAX_TRACKS = 32


class EqDsp:
    """不依赖设备的 EQ：离线导出用（同一个 C 内核，不开声卡）。"""

    def __init__(self, dll_dir: str | None = None, max_tracks: int = MAX_TRACKS):
        from .shim import ShimApi, find_dll

        path = find_dll(dll_dir)
        self.api = ShimApi(path) if path else None
        self.max_tracks = max_tracks

    def eq_available(self) -> bool:
        return self.api is not None

    def eq_process(self, slot: int, buf: np.ndarray, coeffs_b: np.ndarray, coeffs_a: np.ndarray) -> None:
        if self.api is None:
            return
        import ctypes

        coef = np.empty(5, dtype=np.float32)
        coef[0:3] = coeffs_b[:3]
        coef[3:5] = coeffs_a[1:3]
        self.api.lib.ab_eq_process(int(slot), buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                                   int(buf.shape[0]), int(buf.shape[1]),
                                   coef.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))

    def eq_reset(self, slot: int) -> None:
        if self.api is not None:
            self.api.lib.ab_eq_reset(int(slot))


class EqPool:
    """每个 (轨道, 段) 一个状态槽；系数变了才下发，避免每块都算三角函数。"""

    def __init__(self, backend, sample_rate: int, max_tracks: int = MAX_TRACKS):
        self.backend = backend
        self.sr = sample_rate
        self.max_tracks = max_tracks
        self.available = bool(getattr(backend, "eq_available", lambda: False)())
        self._cache: dict[int, tuple[tuple[float, float], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]] = {}

    def _coeffs(self, low_db: float, high_db: float):
        b_l, a_l = low_shelf(LOW_FREQ, low_db, self.sr)
        b_h, a_h = high_shelf(HIGH_FREQ, high_db, self.sr)
        return (b_l, a_l, b_h, a_h)

    def process(self, track: int, buf: np.ndarray, low_db: float, high_db: float, enabled: bool) -> None:
        if not self.available or not enabled or track >= self.max_tracks:
            return
        if abs(low_db) < 0.01 and abs(high_db) < 0.01:
            return
        key = (round(low_db, 2), round(high_db, 2))
        cached = self._cache.get(track)
        if cached is None or cached[0] != key:
            b_l, a_l, b_h, a_h = self._coeffs(low_db, high_db)
            self._cache[track] = (key, (b_l, a_l, b_h, a_h))
        else:
            b_l, a_l, b_h, a_h = cached[1]
        self.backend.eq_process(track * 2 + 0, buf, b_l, a_l)
        self.backend.eq_process(track * 2 + 1, buf, b_h, a_h)

    def reset(self, track: int | None = None) -> None:
        if not self.available:
            return
        if track is None:
            for i in range(self.max_tracks * 2):
                self.backend.eq_reset(i)
        else:
            self.backend.eq_reset(track * 2 + 0)
            self.backend.eq_reset(track * 2 + 1)


def db_to_lin(db: float) -> float:
    return 0.0 if db <= -60.0 else 10.0 ** (db / 20.0)
