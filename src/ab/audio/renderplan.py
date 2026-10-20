"""渲染计划：把「模型」翻译成「混音线程能直接跑的扁平结构」。

只在工程变化时重建；重建过程允许分配，混音过程不允许。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..model.clip import Clip
from ..model.project import Project
from ..model.track import Track
from ..io.waveshim import AudioSource

Resolver = Callable[[Clip], AudioSource | None]


@dataclass(frozen=True)
class RenderClip:
    source: AudioSource
    clip_id: str
    timeline_start: int
    timeline_end: int
    src_start: int
    gain: float
    fade_in: int
    fade_out: int
    fade_shape: str
    speed: float = 1.0
    pitch: float = 0.0


@dataclass
class RenderTrack:
    index: int
    gain: float
    pan_l: float
    pan_r: float
    mute: bool
    solo: bool
    eq_on: bool
    eq_low: float
    eq_high: float
    fade_in: int
    fade_out: int
    first: int
    last: int
    clips: list[RenderClip] = field(default_factory=list)


@dataclass
class RenderPlan:
    sample_rate: int
    tracks: list[RenderTrack] = field(default_factory=list)
    end: int = 0
    any_solo: bool = False
    stamp: int = 0
    missing: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.tracks or self.end <= 0


def build_plan(project: Project, resolve: Resolver, *, stamp: int = 0) -> RenderPlan:
    plan = RenderPlan(sample_rate=project.sample_rate, any_solo=project.any_solo, stamp=stamp)
    sr = project.sample_rate
    for ti, tr in enumerate(project.tracks):
        first, last = tr.clips[0].timeline_start if tr.clips else 0, tr.clips_end()
        rt = RenderTrack(
            index=ti,
            gain=tr.gain_linear,
            pan_l=tr.pan_l,
            pan_r=tr.pan_r,
            mute=tr.mute,
            solo=tr.solo,
            eq_on=tr.eq_enabled,
            eq_low=tr.eq_low_db,
            eq_high=tr.eq_high_db,
            fade_in=int(tr.fade_in_s * sr),
            fade_out=int(tr.fade_out_s * sr),
            first=first,
            last=last,
        )
        for c in tr.clips:
            src = resolve(c)
            if src is None:
                if c.path and c.path not in plan.missing:
                    plan.missing.append(c.path)
                continue
            rt.clips.append(RenderClip(
                source=src,
                clip_id=c.id,
                timeline_start=c.timeline_start,
                timeline_end=c.timeline_end,
                src_start=c.src_start,
                gain=10.0 ** (c.gain_db / 20.0) if c.gain_db > -60 else 0.0,
                fade_in=int(c.fade_in),
                fade_out=int(c.fade_out),
                fade_shape=c.fade_shape,
                speed=float(c.speed),
                pitch=float(c.pitch),
            ))
        rt.clips.sort(key=lambda rc: rc.timeline_start)
        plan.tracks.append(rt)
    plan.end = max((rt.last for rt in plan.tracks), default=0)
    plan.stamp = stamp
    return plan
