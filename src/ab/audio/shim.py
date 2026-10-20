"""`ab_audio.dll`（C / miniaudio）的 ctypes 绑定。

DLL 找不到时整个模块降级为不可用，由 `backend.create_backend` 回退到 sounddevice——
所以这个文件在「还没编译 C 代码」的阶段也能被 import。
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import POINTER, c_char_p, c_float, c_int, c_void_p
from pathlib import Path

import numpy as np

from .backend import BackendInfo, DeviceInfo

log = logging.getLogger("ab.audio.shim")

DLL_NAME = "ab_audio.dll"


class AbInfo(ctypes.Structure):
    _fields_ = [
        ("sample_rate", c_int),
        ("block_frames", c_int),
        ("backend", c_int),        # 0 none / 1 wasapi / 2 winmm / 3 other
        ("latency_ms", c_float),
        ("xruns", c_int),
        ("frames_played", ctypes.c_uint64),
    ]


def _candidates(dll_dir: str | None) -> list[Path]:
    out: list[Path] = []
    if dll_dir:
        out.append(Path(dll_dir) / DLL_NAME)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        out.append(Path(meipass) / "native" / DLL_NAME)
        out.append(Path(meipass) / DLL_NAME)
    here = Path(__file__).resolve().parents[3]
    out += [here / "native" / "build" / DLL_NAME, here / "native" / DLL_NAME]
    if getattr(sys, "frozen", False):
        out.append(Path(sys.executable).resolve().parent / DLL_NAME)
    return out


def find_dll(dll_dir: str | None = None) -> Path | None:
    for p in _candidates(dll_dir):
        if p.exists():
            return p
    return None


def shim_available(dll_dir: str | None = None) -> bool:
    return find_dll(dll_dir) is not None


class ShimApi:
    """薄封装；所有函数签名在此声明一次。"""

    def __init__(self, path: Path):
        self.path = path
        self.lib = ctypes.WinDLL(str(path))
        self._bind()
        self._name_buf = ctypes.create_string_buffer(256)

    def _bind(self) -> None:
        L = self.lib
        L.ab_open.argtypes = [c_char_p, c_int, c_int, c_int]
        L.ab_open.restype = c_int
        L.ab_start.restype = c_int
        L.ab_stop.restype = c_int
        L.ab_close.restype = None
        L.ab_write.argtypes = [POINTER(c_float), c_int]
        L.ab_write.restype = c_int
        L.ab_flush.restype = c_int
        L.ab_info.argtypes = [POINTER(AbInfo)]
        L.ab_info.restype = c_int
        L.ab_device_count.restype = c_int
        L.ab_device_name.argtypes = [c_int, c_char_p, c_int]
        L.ab_device_name.restype = c_int
        L.ab_device_default.restype = c_int
        L.ab_last_error.restype = c_char_p
        L.ab_eq_process.argtypes = [c_int, POINTER(c_float), c_int, c_int, POINTER(c_float)]
        L.ab_eq_process.restype = c_int
        L.ab_eq_reset.argtypes = [c_int]
        L.ab_eq_reset.restype = None

    def last_error(self) -> str:
        try:
            raw = self.lib.ab_last_error()
            return raw.decode("utf-8", "replace") if raw else ""
        except Exception:
            return ""


class ShimBackend:
    name = "shim"

    def __init__(self, dll_dir: str | None = None):
        path = find_dll(dll_dir)
        if path is None:
            raise RuntimeError("ab_audio.dll not found")
        self.api = ShimApi(path)
        self._block = 256
        self._sr = 48000
        self._device = ""
        self._opened = False
        self._info = AbInfo()

    # ---------------------------------------------------------------- 设备
    def devices(self) -> list[DeviceInfo]:
        api = self.api
        n = int(api.lib.ab_device_count())
        default = int(api.lib.ab_device_default())
        out: list[DeviceInfo] = []
        for i in range(n):
            buf = ctypes.create_string_buffer(256)
            api.lib.ab_device_name(i, buf, 256)
            out.append(DeviceInfo(id=str(i), name=buf.value.decode("utf-8", "replace"),
                                  is_default=(i == default), sample_rate=self._sr or 48000))
        return out

    # ---------------------------------------------------------------- 生命周期
    def open(self, *, sample_rate: int, block_frames: int, device: str | None = None) -> None:
        dev = (device or "").encode("utf-8")
        rc = int(self.api.lib.ab_open(dev, int(sample_rate), int(block_frames), 0))
        if rc != 0:
            raise RuntimeError(f"ab_open failed rc={rc}: {self.api.last_error()}")
        self._opened = True
        self._info.sample_rate = sample_rate
        self._info.block_frames = block_frames
        self.api.lib.ab_info(ctypes.byref(self._info))
        self._block = int(self._info.block_frames or block_frames)
        self._sr = int(self._info.sample_rate or sample_rate)
        log.info("shim open: backend=%d sr=%d block=%d latency=%.1fms",
                 self._info.backend, self._sr, self._block, self._info.latency_ms)

    def start(self) -> None:
        if self._opened:
            self.api.lib.ab_start()

    def stop(self) -> None:
        if self._opened:
            self.api.lib.ab_stop()

    def close(self) -> None:
        if self._opened:
            self.api.lib.ab_close()
            self._opened = False

    # ---------------------------------------------------------------- 数据
    def write(self, block: np.ndarray) -> None:
        buf = np.ascontiguousarray(block, dtype=np.float32)
        ptr = buf.ctypes.data_as(POINTER(c_float))
        self.api.lib.ab_write(ptr, int(buf.shape[0]))

    def flush(self) -> None:
        """重开播放前清空环形缓冲（否则停/开后残留半拍旧音频）。"""
        if self._opened:
            self.api.lib.ab_flush()

    # ---------------------------------------------------------------- EQ
    def eq_available(self) -> bool:
        return True

    def eq_process(self, slot: int, buf: np.ndarray, coeffs_b: np.ndarray, coeffs_a: np.ndarray) -> None:
        # C 侧只要 5 个系数：b0 b1 b2 a1 a2（a0 已被归一化为 1）
        coef = np.empty(5, dtype=np.float32)
        coef[0:3] = coeffs_b[:3]
        coef[3:5] = coeffs_a[1:3]
        ptr = buf.ctypes.data_as(POINTER(c_float))
        self.api.lib.ab_eq_process(int(slot), ptr, int(buf.shape[0]), int(buf.shape[1]),
                                   coef.ctypes.data_as(POINTER(c_float)))

    def eq_reset(self, slot: int) -> None:
        self.api.lib.ab_eq_reset(int(slot))

    # ---------------------------------------------------------------- 信息
    def info(self) -> BackendInfo:
        if self._opened:
            self.api.lib.ab_info(ctypes.byref(self._info))
        kind = {0: "none", 1: "wasapi", 2: "winmm", 3: "other"}.get(int(self._info.backend), "other")
        return BackendInfo(name=self.name, device=self._device or f"device#{0}",
                           sample_rate=int(self._info.sample_rate or self._sr),
                           block_frames=int(self._info.block_frames or self._block),
                           latency_ms=float(self._info.latency_ms),
                           xruns=int(self._info.xruns),
                           extra={"api": kind, "frames_played": int(self._info.frames_played)})
