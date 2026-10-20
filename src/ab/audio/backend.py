"""音频后端抽象：设备 I/O 与后端降级都在这一层。

约定（很重要）：
  * `write()` 只在**生产者线程**调用，块大小固定 == `block_frames`；
  * 设备回调线程永远在 C 侧（shim）或 PortAudio 内部（sounddevice），不进 Python；
  * 所有后端都提供 `latency_ms` 与 `xruns`，UI 与自检只读这两个数。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

log = logging.getLogger("ab.audio.backend")


@dataclass
class DeviceInfo:
    id: str
    name: str
    is_default: bool = False
    sample_rate: int = 48000

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "default": self.is_default,
                "sr": self.sample_rate}


@dataclass
class BackendInfo:
    name: str = "none"
    device: str = ""
    sample_rate: int = 48000
    block_frames: int = 256
    latency_ms: float = 0.0
    xruns: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"backend": self.name, "device": self.device, "sample_rate": self.sample_rate,
                "block_frames": self.block_frames, "latency_ms": round(self.latency_ms, 1),
                "xruns": self.xruns, **self.extra}


class AudioBackend(Protocol):
    name: str

    def open(self, *, sample_rate: int, block_frames: int, device: str | None = None) -> None: ...
    def start(self) -> None: ...
    def write(self, block: np.ndarray) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
    def info(self) -> BackendInfo: ...


class NullBackend:
    """无设备后端：离线渲染、autotest、CI 用（写进来的块直接丢）。"""

    name = "null"

    def __init__(self, sample_rate: int = 48000, block_frames: int = 256):
        self._sr = sample_rate
        self._block = block_frames
        self._written = 0

    def open(self, *, sample_rate: int, block_frames: int, device: str | None = None) -> None:
        self._sr, self._block = sample_rate, block_frames

    def start(self) -> None:
        pass

    def write(self, block: np.ndarray) -> None:
        self._written += len(block)

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass

    def info(self) -> BackendInfo:
        return BackendInfo(name=self.name, device="(none)", sample_rate=self._sr,
                           block_frames=self._block, latency_ms=0.0, xruns=0,
                           extra={"written_frames": self._written})


def create_backend(kind: str = "auto", *, dll_dir: str | None = None):
    """auto → shim（若 DLL 可用）→ sounddevice → null。"""
    if kind in ("auto", "shim"):
        try:
            from .shim import ShimBackend, shim_available

            if shim_available(dll_dir):
                return ShimBackend(dll_dir=dll_dir)
            if kind == "shim":
                log.warning("shim backend requested but ab_audio.dll not found; falling back")
        except Exception as e:  # pragma: no cover
            log.warning("shim backend unavailable: %s", e)
    if kind in ("auto", "sounddevice"):
        try:
            from .sd_backend import SoundDeviceBackend

            return SoundDeviceBackend()
        except Exception as e:
            log.warning("sounddevice backend unavailable: %s", e)
    log.warning("no audio backend available, using null backend")
    return NullBackend()


def list_devices(backend: AudioBackend | None = None) -> list[DeviceInfo]:
    if backend is not None and hasattr(backend, "devices"):
        try:
            return backend.devices()  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        from .sd_backend import SoundDeviceBackend

        return SoundDeviceBackend.enumerate_devices()
    except Exception:
        return []
