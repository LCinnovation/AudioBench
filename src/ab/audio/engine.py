"""播放引擎：一个生产者线程 + 一个设备后端。

线程模型（与 M0 §1.2 一致）：
  * 本线程只做「混音 → 可选重采样 → 写后端」，不做 UI、不做文件 IO；
  * 设备回调在 C/PortAudio 内部，永不进入 Python；
  * 位置推进按**重采样前**的工程采样计，UI 用 `position` 读取。
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from .backend import AudioBackend
from .mixer import ClickTrack, Mixer
from .renderplan import RenderPlan

log = logging.getLogger("ab.engine")

SILENCE_EPS = 0.0005


def _time_begin_period() -> None:
    try:
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


def _time_end_period() -> None:
    try:
        import ctypes

        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


class Engine:
    def __init__(self, backend: AudioBackend, *, project_sample_rate: int = 44100,
                 block_frames: int = 256, mixer: Mixer | None = None):
        self.backend = backend
        self.project_sr = int(project_sample_rate)
        self.device_sr = int(getattr(backend.info(), "sample_rate", project_sample_rate) or project_sample_rate)
        self.plan: RenderPlan | None = None
        self.click = ClickTrack(project_sample_rate)
        self.mixer = mixer or Mixer(block_frames, project_sample_rate)
        self.master_gain = 1.0
        self.bpm = 120.0

        self._playing = False
        self._pos = 0
        self._seek_to: int | None = None
        self._loop_a: int | None = None
        self._loop_b: int | None = None
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._resample = self.device_sr != self.project_sr
        self._rs = None
        if self._resample:
            import soxr

            self._rs = soxr.ResampleStream(self.project_sr, self.device_sr, 2, dtype="float32")
            log.info("resampling %d -> %d Hz (soxr)", self.project_sr, self.device_sr)
        self._dev_block = int(backend.info().block_frames or block_frames)
        self._mix = np.zeros((self._dev_block * 4 + 8, 2), dtype=np.float32)
        self._pending = np.zeros((self._dev_block * 6 + 64, 2), dtype=np.float32)
        self._pending_len = 0
        self._acc = 0
        self._silence = np.zeros((self._dev_block, 2), dtype=np.float32)
        self._wakeups = 0
        self._last_pos_seen = 0
        self._device_running = False
        self._idle_since = 0.0

    # ---------------------------------------------------------------- 生命周期
    def open(self) -> None:
        """只打开设备（不启动）：真正出声要等 play()。
        这样启动阶段 UI 忙着重绘时不会去喂设备，也就不会白白产生 xrun。"""
        # 把系统定时器精度提到 1ms：否则 ab_write 里的 Sleep(1) 实际睡 15.6ms，
        # 生产者会周期性睡过头，把 23ms 的环形缓冲抽干（M1 实测 4~7 xrun/10s）。
        _time_begin_period()
        self.backend.open(sample_rate=self.project_sr, block_frames=self._dev_block)
        info = self.backend.info()
        self.device_sr = int(info.sample_rate or self.project_sr)
        if self.device_sr != self.project_sr:
            import soxr

            self._resample = True
            self._rs = soxr.ResampleStream(self.project_sr, self.device_sr, 2, dtype="float32")
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="ab-mixer", daemon=True)
        self._thread.start()
        self._device_running = False
        log.info("engine ready: device=%d Hz block=%d project=%d Hz resample=%s (device not started)",
                 self.device_sr, self._dev_block, self.project_sr, self._resample)

    def _resume_device(self) -> None:
        """从空闲状态恢复：先清缓冲再启动设备，避免残留旧数据。"""
        try:
            flush = getattr(self.backend, "flush", None)
            if callable(flush):
                flush()
            self.backend.start()
            self._device_running = True
            log.info("device resumed")
        except Exception as e:  # pragma: no cover
            log.warning("device resume failed: %s", e)

    def close(self) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            self.backend.stop()
            self.backend.close()
        except Exception as e:  # pragma: no cover
            log.warning("backend close failed: %s", e)
        _time_end_period()

    # ---------------------------------------------------------------- 控制
    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def position(self) -> int:
        return int(self._pos)

    def play(self) -> None:
        if not self._playing:
            self._tune_gc(True)
            self._playing = True
            self._idle_since = 0.0
            if not self._device_running:
                self._warmup()
                self._resume_device()

    @staticmethod
    def _tune_gc(playing: bool) -> None:
        """播放期间把 GC 压低：实测第 4–5 秒的一次 gen2 回收会造成 20–40ms 停顿，
        正好抽干 23ms 的环形缓冲（M1 实测 4~7 个 xrun 全部来自这里）。"""
        import gc

        try:
            if playing:
                gc.collect()
                gc.freeze()
                gc.set_threshold(50000, 50, 50)
            else:
                gc.unfreeze()
                gc.set_threshold(700, 10, 10)
                gc.collect()
        except Exception:
            pass

    def _warmup(self) -> None:
        """先空跑两块：把 mmap 页、EQ 系数、numpy 临时缓冲都摸一遍，
        否则第一块渲染可能花几十毫秒，直接把环形缓冲抽干（实测开头 8 个 xrun）。"""
        plan = self.plan
        if plan is None:
            return
        try:
            scratch = self._mix[: self._dev_block]
            pos = self._pos
            self._mix_into(scratch)
            self._mix_into(scratch)
            self._pos = pos
            if self.eq is not None:
                self.eq.reset()
            if self._rs is not None:
                import soxr

                self._rs = soxr.ResampleStream(self.project_sr, self.device_sr, 2, dtype="float32")
        except Exception as e:  # 预热失败不影响播放
            log.debug("warmup failed: %s", e)

    def stop(self) -> None:
        if self._playing:
            self._tune_gc(False)
        self._playing = False

    def seek(self, sample: int) -> None:
        self._seek_to = max(0, int(sample))

    def set_plan(self, plan: RenderPlan) -> None:
        self.plan = plan

    def set_loop(self, a: int | None, b: int | None) -> None:
        if a is not None and b is not None and b > a:
            self._loop_a, self._loop_b = int(a), int(b)
        else:
            self._loop_a = self._loop_b = None

    @property
    def loop(self) -> tuple[int | None, int | None]:
        return self._loop_a, self._loop_b

    def set_metronome(self, enabled: bool) -> None:
        self.click.enabled = bool(enabled)

    def set_bpm(self, bpm: float) -> None:
        self.bpm = max(20.0, min(300.0, float(bpm)))

    def set_master_gain(self, gain: float) -> None:
        self.master_gain = max(0.0, float(gain))

    def levels(self) -> tuple[float, float, float, float]:
        pk = self.mixer.peak
        rms = self.mixer.rms
        return float(pk[0]), float(pk[1]), float(rms[0]), float(rms[1])

    # ---------------------------------------------------------------- 线程
    def _run(self) -> None:
        log.info("mixer thread up")
        while not self._stop_evt.is_set():
            try:
                if self._seek_to is not None:
                    self._pos = self._seek_to
                    self._seek_to = None
                    self._pending_len = 0
                    self._acc = 0
                    if self._rs is not None:
                        import soxr

                        self._rs = soxr.ResampleStream(self.project_sr, self.device_sr, 2, dtype="float32")
                if self._playing:
                    block = self._produce()
                else:
                    # 空闲 250ms 后停设备：不空转、也不在 UI 重绘时冒 underrun 的风险
                    if self._device_running:
                        now = time.monotonic()
                        if self._idle_since == 0.0:
                            self._idle_since = now
                        elif now - self._idle_since > 0.25:
                            try:
                                self.backend.stop()
                            except Exception:
                                pass
                            self._device_running = False
                    if not self._device_running:
                        time.sleep(0.02)
                        continue
                    block = self._silence
                self.backend.write(block)
                self._wakeups += 1
            except Exception as e:  # 音频线程绝不退出
                log.exception("mixer loop error: %s", e)
                time.sleep(0.05)
        log.info("mixer thread down (wakeups=%d)", self._wakeups)

    def _produce(self) -> np.ndarray:
        dev_block = self._dev_block
        if self._pending_len < dev_block:
            self._fill_pending()
        # 必须拷贝：下面的移动会覆盖 _pending 的前半段
        out = self._pending[:dev_block].copy()
        rest = self._pending_len - dev_block
        if rest > 0:
            self._pending[:rest] = self._pending[dev_block:dev_block + rest]
        self._pending_len = max(0, rest)
        return out

    def _fill_pending(self) -> None:
        dev_block = self._dev_block
        guard = 0
        while self._pending_len < dev_block and guard < 8:
            guard += 1
            if self._resample:
                self._acc += dev_block * self.project_sr
                n_proj = self._acc // self.device_sr
                self._acc -= n_proj * self.device_sr
                n_proj = int(max(1, min(n_proj, self._mix.shape[0])))
            else:
                n_proj = dev_block
            mix = self._mix[:n_proj]
            self._mix_into(mix)
            if self._resample and self._rs is not None:
                y = self._rs.resample_chunk(np.ascontiguousarray(mix))
                m = y.shape[0]
            else:
                y = mix
                m = n_proj
            if m <= 0:
                continue
            space = self._pending.shape[0] - self._pending_len
            m = min(m, space)
            if m <= 0:
                self._pending_len = 0  # 溢出保护：丢掉旧数据
                space = self._pending.shape[0]
                m = min(y.shape[0], space)
            self._pending[self._pending_len:self._pending_len + m] = y[:m]
            self._pending_len += m
            # 位置推进 & 循环
            self._pos += n_proj
            if self._loop_b is not None and self._pos >= self._loop_b:
                self._pos = self._loop_a or 0
                self._acc = 0
                if self._rs is not None:
                    import soxr

                    self._rs = soxr.ResampleStream(self.project_sr, self.device_sr, 2, dtype="float32")

    def _mix_into(self, mix: np.ndarray) -> None:
        plan = self.plan
        if plan is None:
            mix.fill(0.0)
            return
        self.mixer.render(plan, self._pos, mix, master_gain=self.master_gain,
                          click=self.click if self.click.enabled else None, bpm=self.bpm)

    # ---------------------------------------------------------------- 信息
    def info(self) -> dict:
        bi = self.backend.info()
        return {
            "backend": bi.name,
            "device_rate": self.device_sr,
            "project_rate": self.project_sr,
            "block_frames": self._dev_block,
            "resampling": self._resample,
            "latency_ms": bi.latency_ms,
            "engine_latency_ms": (2 * self._dev_block / max(1, self.device_sr)) * 1000.0,
            "xruns": bi.xruns,
            "wakeups": self._wakeups,
            "playing": self._playing,
            "position": self._pos,
            **bi.extra,
        }
