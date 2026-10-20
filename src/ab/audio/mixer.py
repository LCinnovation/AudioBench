"""块混音器：**全预分配**，每个块只做 numpy 运算与一次 mmap 读。

信号链（每轨）：片段读入 → 片段增益/淡入淡出 → 累加 → 轨道 EQ（C）
→ 轨道级渐入渐出 → 声像 → 轨道增益 → 求和到总线。

约定：本类的所有缓冲在 `resize()` 里一次性分配；`render()` 里不新建数组
（`np.arange` 之类也走预分配缓冲），避免给 GC 制造呼吸。
"""
from __future__ import annotations

import logging

import numpy as np

from ..io.waveshim import AudioSource
from .dsp import EqPool
from .renderplan import RenderClip, RenderPlan, RenderTrack

log = logging.getLogger("ab.mixer")

MAX_TRACKS = 32


class Mixer:
    def __init__(self, block_frames: int, sample_rate: int, eq_pool: EqPool | None = None):
        self.sample_rate = int(sample_rate)
        self.eq = eq_pool
        self.peak = np.zeros(2, dtype=np.float32)
        self.rms = np.zeros(2, dtype=np.float32)
        self.block = 0
        self._alloc(int(block_frames))

    # ---------------------------------------------------------------- 缓冲
    def _alloc(self, block: int) -> None:
        self.block = int(block)
        self._tb = np.zeros((MAX_TRACKS, self.block, 2), dtype=np.float32)
        self._clip = np.zeros((self.block, 2), dtype=np.float32)
        self._idx = np.arange(self.block, dtype=np.int64)
        self._loc = np.zeros(self.block, dtype=np.int64)
        self._g = np.ones(self.block, dtype=np.float32)
        self._gain_cache: dict[tuple[int, str], np.ndarray] = {}
        self._stretch_in = np.zeros((self.block * 2 + 16, 2), dtype=np.float32)
        self._stretch_out = np.zeros((self.block + 16, 2), dtype=np.float32)
        self._stretchers = {}
        self._stretch_engine = None

    def resize(self, block_frames: int) -> None:
        if int(block_frames) != self.block:
            self._alloc(int(block_frames))

    def _ramp(self, n: int, shape: str) -> np.ndarray:
        key = (n, shape)
        arr = self._gain_cache.get(key)
        if arr is None:
            x = np.linspace(0.0, 1.0, n, dtype=np.float32)
            arr = (x * x).astype(np.float32) if shape == "exp" else x
            if len(self._gain_cache) > 512:
                self._gain_cache.clear()
            self._gain_cache[key] = arr
        return arr

    # ---------------------------------------------------------------- 主入口
    def render(self, plan: RenderPlan, pos: int, out: np.ndarray,
               *, master_gain: float = 1.0, click=None, bpm: float = 120.0) -> None:
        n = out.shape[0]
        out.fill(0.0)
        if n == 0:
            return
        if n != self.block:
            self.resize(n)
        if plan is None or not plan.tracks:
            if click is not None:
                click.render(out, pos, n, bpm)
            self._meter(out, master_gain)
            return

        block_end = pos + n
        for ti, tr in enumerate(plan.tracks[:MAX_TRACKS]):
            if tr.mute or (plan.any_solo and not tr.solo):
                continue
            if tr.last <= pos or tr.first >= block_end:
                continue
            tb = self._tb[ti]
            tb.fill(0.0)
            self._render_track_clips(tr, pos, n, tb)
            if self.eq is not None:
                self.eq.process(ti, tb, tr.eq_low, tr.eq_high, tr.eq_on)
            self._track_fade(tr, pos, n, tb)
            tb[:, 0] *= tr.gain * tr.pan_l
            tb[:, 1] *= tr.gain * tr.pan_r
            out += tb

        if click is not None:
            click.render(out, pos, n, bpm)
        if master_gain != 1.0:
            out *= master_gain
        self._meter(out, 1.0)

    # ---------------------------------------------------------------- 片段
    def _render_track_clips(self, tr: RenderTrack, pos: int, n: int, tb: np.ndarray) -> None:
        for rc in tr.clips:
            cs = rc.timeline_start if rc.timeline_start > pos else pos
            ce = rc.timeline_end if rc.timeline_end < pos + n else pos + n
            if cs >= ce:
                continue
            count = ce - cs
            dst = cs - pos
            local = cs - rc.timeline_start          # 片段内时间偏移（时间线采样）
            if rc.speed == 1.0 and rc.pitch == 0.0:
                src_off = rc.src_start + local
                buf = self._clip[:count]
                rc.source.read_into(buf, src_off, count)
            else:
                st = self._stretcher(rc)
                start_src = rc.src_start + local * rc.speed
                buf = st.pull(rc.source, start_src, count, rc.speed, rc.pitch,
                              self._stretch_in, self._stretch_out)
            if rc.gain != 1.0:
                buf *= rc.gain
            self._clip_fade(buf, local, rc)
            tb[dst:dst + count] += buf

    def _stretcher(self, rc: RenderClip):
        from .stretch import ClipStretcher, StretchEngine

        st = self._stretchers.get(rc.clip_id)
        if st is None:
            if self._stretch_engine is None:
                self._stretch_engine = StretchEngine.instance()
            st = ClipStretcher(self._stretch_engine, self.sample_rate)
            self._stretchers[rc.clip_id] = st
        return st

    def _clip_fade(self, buf: np.ndarray, local0: int, rc: RenderClip) -> None:
        n = buf.shape[0]
        if rc.fade_in <= 0 and rc.fade_out <= 0:
            return
        length = max(1, rc.timeline_end - rc.timeline_start)
        loc = self._loc[:n]
        np.add(self._idx[:n], local0, out=loc)
        g = self._g[:n]
        g.fill(1.0)
        exp = rc.fade_shape == "exp"
        if rc.fade_in > 0 and local0 < rc.fade_in:
            t = loc / float(rc.fade_in)
            np.clip(t, 0.0, 1.0, out=t)
            if exp:
                np.multiply(t, t, out=t)
            g *= t.astype(np.float32)
        if rc.fade_out > 0 and local0 + n > length - rc.fade_out:
            t = (length - loc) / float(rc.fade_out)
            np.clip(t, 0.0, 1.0, out=t)
            if exp:
                np.multiply(t, t, out=t)
            g *= t.astype(np.float32)
        buf *= g[:, None]

    # ---------------------------------------------------------------- 轨道
    def _track_fade(self, tr: RenderTrack, pos: int, n: int, tb: np.ndarray) -> None:
        if tr.fade_in <= 0 and tr.fade_out <= 0:
            return
        if tr.fade_in > 0:
            start = tr.first - pos
            if start < n and start + tr.fade_in > 0:
                lo = max(0, start)
                hi = min(n, start + tr.fade_in)
                if hi > lo:
                    ramp = self._ramp(tr.fade_in, "linear")
                    a = lo - start
                    tb[lo:hi] *= ramp[a:a + (hi - lo)][:, None]
        if tr.fade_out > 0:
            fade_start = tr.last - tr.fade_out - pos
            if fade_start < n and fade_start + tr.fade_out > 0:
                lo = max(0, fade_start)
                hi = min(n, fade_start + tr.fade_out)
                if hi > lo:
                    ramp = self._ramp(tr.fade_out, "linear")[::-1]
                    a = lo - fade_start
                    tb[lo:hi] *= ramp[a:a + (hi - lo)][:, None]

    # ---------------------------------------------------------------- 电平
    def _meter(self, out: np.ndarray, gain: float) -> None:
        if out.shape[0] == 0:
            return
        np.max(np.abs(out), axis=0, out=self.peak)
        if gain != 1.0:
            self.peak *= gain
        np.mean(out * out, axis=0, out=self.rms)
        np.sqrt(self.rms, out=self.rms)
        if gain != 1.0:
            self.rms *= abs(gain)


class ClickTrack:
    """节拍器：预生成 click 缓冲，按拍点叠加（§4.2 走带条）。"""

    def __init__(self, sample_rate: int, freq: float = 1000.0, dur_s: float = 0.045):
        self.sr = int(sample_rate)
        n = int(dur_s * self.sr)
        t = np.arange(n, dtype=np.float32) / self.sr
        env = np.exp(-t * 60.0).astype(np.float32)
        self._click = (0.35 * np.sin(2 * np.pi * freq * t) * env).astype(np.float32)
        self.enabled = False

    def render(self, out: np.ndarray, pos: int, n: int, bpm: float) -> None:
        if not self.enabled or bpm <= 0:
            return
        spb = self.sr * 60.0 / float(bpm)
        beat = int(pos // spb) + 1
        limit = pos + n
        while True:
            at = int(beat * spb)
            if at >= limit:
                break
            k = at - pos
            m = min(len(self._click), n - k)
            if m > 0:
                out[k:k + m, 0] += self._click[:m]
                out[k:k + m, 1] += self._click[:m]
            beat += 1
