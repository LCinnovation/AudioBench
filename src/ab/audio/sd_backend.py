"""sounddevice（PortAudio）后端：先落地、可无编译器运行，也是 shim 的降级路径。

RT 说明：回调线程在 PortAudio 内部；Python 只做**阻塞写**，不进入设备回调。
"""
from __future__ import annotations

import logging

import numpy as np

from .backend import BackendInfo, DeviceInfo

log = logging.getLogger("ab.audio.sd")


class SoundDeviceBackend:
    name = "sounddevice"

    def __init__(self) -> None:
        import sounddevice as sd  # 延迟导入：没有声卡/没装包时不影响其它后端

        self._sd = sd
        self._stream = None
        self._sr = 48000
        self._block = 256
        self._device = ""
        self._xruns = 0

    # ---------------------------------------------------------------- 设备
    @staticmethod
    def enumerate_devices() -> list[DeviceInfo]:
        import sounddevice as sd

        out: list[DeviceInfo] = []
        try:
            default_out = sd.default.device[1]
        except Exception:
            default_out = -1
        for idx, d in enumerate(sd.query_devices()):
            if int(d.get("max_output_channels", 0)) <= 0:
                continue
            out.append(DeviceInfo(
                id=str(idx), name=str(d.get("name", "")).strip(),
                is_default=(idx == default_out),
                sample_rate=int(d.get("default_samplerate", 48000) or 48000),
            ))
        return out

    def devices(self) -> list[DeviceInfo]:
        return self.enumerate_devices()

    # ---------------------------------------------------------------- 生命周期
    def open(self, *, sample_rate: int, block_frames: int, device: str | None = None) -> None:
        dev = None if device in (None, "", "default") else (int(device) if str(device).isdigit() else device)
        info = self._sd.query_devices(dev, kind="output")
        native = int(info.get("default_samplerate") or sample_rate)
        self._sr = native or sample_rate
        self._block = int(block_frames)
        self._device = str(info.get("name", "")).strip()
        try:
            self._stream = self._sd.RawOutputStream(
                samplerate=self._sr, channels=2, dtype="float32",
                blocksize=self._block, device=dev, latency="low",
            )
        except Exception as e:
            log.warning("RawOutputStream(low) failed (%s), retry with default latency", e)
            self._stream = self._sd.RawOutputStream(
                samplerate=self._sr, channels=2, dtype="float32",
                blocksize=self._block, device=dev,
            )
        log.info("sounddevice open: %s @%d Hz block=%d", self._device, self._sr, self._block)

    def start(self) -> None:
        if self._stream is not None and not self._stream.active:
            self._stream.start()

    def stop(self) -> None:
        if self._stream is not None and self._stream.active:
            self._stream.stop()

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ---------------------------------------------------------------- 数据
    def write(self, block: np.ndarray) -> None:
        if self._stream is None:
            return
        buf = np.ascontiguousarray(block, dtype=np.float32)
        try:
            self._stream.write(buf)
        except Exception as e:
            self._xruns += 1
            log.debug("write underflow #%d: %s", self._xruns, e)

    # ---------------------------------------------------------------- 信息
    def info(self) -> BackendInfo:
        lat = 0.0
        try:
            if self._stream is not None:
                lat = float(self._stream.latency or 0.0) * 1000.0
        except Exception:
            lat = 0.0
        return BackendInfo(name=self.name, device=self._device, sample_rate=self._sr,
                           block_frames=self._block, latency_ms=lat, xruns=self._xruns,
                           extra={"api": "portaudio"})
