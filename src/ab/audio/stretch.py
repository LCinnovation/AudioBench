"""变速变调：Signalsmith Stretch（MIT）的 ctypes 绑定 + 每片段状态。

语义（与模型一致）：
  * `speed` = 播放速度倍率（0.5–2.0），时间线长度 = 源长度 / speed；
  * `pitch` = 半音（±12），只改音高不改速度；
  * Signalsmith 的伸缩比是「输入帧数 : 输出帧数」，所以每块喂 `n_out * speed` 帧。

没有 DLL 时退化为「磁带模式」（读得更快/更慢，音高跟着变），并在状态栏提示一次。
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import POINTER, c_char_p, c_float, c_int, c_void_p
from pathlib import Path

import numpy as np

log = logging.getLogger("ab.stretch")

DLL_NAME = "ab_stretch.dll"


def _candidates() -> list[Path]:
    out: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        out.append(Path(meipass) / "native" / DLL_NAME)
        out.append(Path(meipass) / DLL_NAME)
    here = Path(__file__).resolve().parents[3]
    out += [here / "native" / "build" / DLL_NAME, here / "native" / DLL_NAME]
    if getattr(sys, "frozen", False):
        out.append(Path(sys.executable).resolve().parent / DLL_NAME)
    return out


def find_dll() -> Path | None:
    for p in _candidates():
        if p.exists():
            return p
    return None


class StretchEngine:
    _instance: "StretchEngine | None" = None

    def __init__(self, path: Path | None = None):
        self.path = path or find_dll()
        self.lib = None
        if self.path is None:
            log.warning("ab_stretch.dll not found: falling back to tape mode")
            return
        try:
            self.lib = ctypes.WinDLL(str(self.path))
            L = self.lib
            L.ab_stretch_create.argtypes = [c_int, c_int, c_int]
            L.ab_stretch_create.restype = c_void_p
            L.ab_stretch_destroy.argtypes = [c_void_p]
            L.ab_stretch_set_pitch.argtypes = [c_void_p, c_float]
            L.ab_stretch_reset.argtypes = [c_void_p]
            L.ab_stretch_process.argtypes = [c_void_p, POINTER(c_float), c_int,
                                             POINTER(c_float), c_int]
            L.ab_stretch_process.restype = c_int
            L.ab_stretch_input_latency.argtypes = [c_void_p]
            L.ab_stretch_output_latency.argtypes = [c_void_p]
            L.ab_stretch_version.restype = c_char_p
            log.info("stretch engine: %s (%s)", self.path.name,
                     (L.ab_stretch_version() or b"").decode("ascii", "replace"))
        except Exception as e:
            log.warning("ab_stretch load failed: %s", e)
            self.lib = None

    @classmethod
    def instance(cls) -> "StretchEngine":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def available(self) -> bool:
        return self.lib is not None

    # ---------------------------------------------------------------- 基本操作
    def create(self, channels: int = 2, sample_rate: int = 44100, max_block: int = 4096):
        if self.lib is None:
            return None
        return self.lib.ab_stretch_create(int(channels), int(sample_rate), int(max_block))

    def destroy(self, h) -> None:
        if self.lib is not None and h:
            self.lib.ab_stretch_destroy(c_void_p(h))

    def reset(self, h) -> None:
        if self.lib is not None and h:
            self.lib.ab_stretch_reset(c_void_p(h))

    def set_pitch(self, h, semitones: float) -> None:
        if self.lib is not None and h:
            self.lib.ab_stretch_set_pitch(c_void_p(h), c_float(semitones))

    def process(self, h, in_buf: np.ndarray, out_buf: np.ndarray) -> bool:
        if self.lib is None or h is None:
            return False
        rc = self.lib.ab_stretch_process(
            c_void_p(h),
            in_buf.ctypes.data_as(POINTER(c_float)), int(in_buf.shape[0]),
            out_buf.ctypes.data_as(POINTER(c_float)), int(out_buf.shape[0]))
        return rc == 0

    def latency(self, h) -> tuple[int, int]:
        if self.lib is None or h is None:
            return (0, 0)
        return (int(self.lib.ab_stretch_input_latency(c_void_p(h))),
                int(self.lib.ab_stretch_output_latency(c_void_p(h))))


class ClipStretcher:
    """一个片段的伸缩状态：源游标 + 预热标记 + 上次音高。"""

    def __init__(self, engine: StretchEngine, sample_rate: int):
        self.engine = engine
        self.sample_rate = int(sample_rate)
        self.handle = engine.create(2, self.sample_rate) if engine.available else None
        self.cursor = 0.0
        self.pitch = 0.0
        self.valid = False

    def close(self) -> None:
        if self.handle:
            self.engine.destroy(self.handle)
            self.handle = None

    def reset(self, at: float) -> None:
        self.cursor = float(at)
        self.valid = False
        if self.handle:
            self.engine.reset(self.handle)
        self.valid = True

    def pull(self, source, start_src: float, n_out: int, speed: float, pitch: float,
             in_scratch: np.ndarray, out_scratch: np.ndarray) -> np.ndarray:
        """返回 (n_out, 2)。start_src 是理想起点；偏差大就重同步。"""
        out = out_scratch[:n_out]
        out.fill(0.0)
        if n_out <= 0:
            return out
        speed = min(2.0, max(0.5, float(speed)))
        n_in = max(1, int(round(n_out * speed)))

        if self.handle is None or not self.valid or abs(self.cursor - start_src) > 2048:
            if self.handle is None:
                return self._tape(source, start_src, n_out, speed, out)
            self.reset(start_src)

        if abs(pitch - self.pitch) > 0.01:
            self.engine.set_pitch(self.handle, pitch)
            self.pitch = pitch

        in_buf = in_scratch[:n_in]
        source.read_into(in_buf, int(round(self.cursor)), n_in)
        ok = self.engine.process(self.handle, in_buf, out)
        if not ok:
            return self._tape(source, start_src, n_out, speed, out)
        self.cursor = start_src + n_in      # 以理想位置为锚，避免累积误差
        return out

    # ---------------------------------------------------------------- 退路
    def _tape(self, source, start_src: float, n_out: int, speed: float, out: np.ndarray) -> np.ndarray:
        """磁带模式：按 speed 线性插值取样（音高会跟着变，仅作 DLL 缺失时的兜底）。"""
        idx = start_src + np.arange(n_out, dtype=np.float64) * speed
        base = int(np.floor(start_src))
        span = int(np.ceil(idx[-1] - base)) + 2 if n_out else 2
        raw = source.read(base, max(1, span)).astype(np.float64)
        pos = idx - base
        i0 = np.clip(np.floor(pos).astype(int), 0, max(0, raw.shape[0] - 1))
        i1 = np.clip(i0 + 1, 0, max(0, raw.shape[0] - 1))
        frac = (pos - i0)[:, None]
        out[:] = (raw[i0] * (1.0 - frac) + raw[i1] * frac).astype(np.float32)
        return out
