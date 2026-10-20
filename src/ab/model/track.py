"""轨道（Track）：§4.3 轨道头的全部状态都在这里。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..version import INSTRUMENTS, TRACK_COLORS
from .clip import Clip

GAIN_MIN, GAIN_MAX = -60.0, 12.0
EQ_LIMIT = 12.0
FADE_MAX_S = 10.0


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


@dataclass
class Track:
    name: str = ""
    color: str = TRACK_COLORS[0]
    instrument: str = "other"
    gain_db: float = 0.0
    pan: float = 0.0                 # -1 = L100, 0 = C, +1 = R100
    eq_low_db: float = 0.0           # 低架 ±12dB
    eq_high_db: float = 0.0          # 高架 ±12dB
    eq_enabled: bool = True
    mute: bool = False
    solo: bool = False
    fade_in_s: float = 0.0           # 轨道级渐入（秒，0–10）
    fade_out_s: float = 0.0
    height: int = 72                 # 折叠后仍是 72（§4.3 每轨 72px）
    collapsed: bool = False
    clips: list[Clip] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # ---------------------------------------------------------------- 属性
    @property
    def gain_linear(self) -> float:
        return 0.0 if self.gain_db <= GAIN_MIN else 10.0 ** (self.gain_db / 20.0)

    @property
    def pan_l(self) -> float:
        """等功率声像（-3dB 中心）。"""
        import math
        ang = (self.pan + 1.0) * math.pi / 4.0   # 0..pi/2
        return math.cos(ang)

    @property
    def pan_r(self) -> float:
        import math
        ang = (self.pan + 1.0) * math.pi / 4.0
        return math.sin(ang)

    def clips_end(self) -> int:
        return max((c.timeline_end for c in self.clips), default=0)

    # ---------------------------------------------------------------- 规整
    def normalize(self) -> None:
        self.gain_db = clamp(float(self.gain_db), GAIN_MIN, GAIN_MAX)
        self.pan = clamp(float(self.pan), -1.0, 1.0)
        self.eq_low_db = clamp(float(self.eq_low_db), -EQ_LIMIT, EQ_LIMIT)
        self.eq_high_db = clamp(float(self.eq_high_db), -EQ_LIMIT, EQ_LIMIT)
        self.fade_in_s = clamp(float(self.fade_in_s), 0.0, FADE_MAX_S)
        self.fade_out_s = clamp(float(self.fade_out_s), 0.0, FADE_MAX_S)
        if self.instrument not in INSTRUMENTS:
            self.instrument = "other"
        for c in self.clips:
            c.clamp()

    # ---------------------------------------------------------------- 序列化
    def to_dict(self, *, base_dir: str | None = None) -> dict:
        import os
        clips = []
        for c in self.clips:
            d = c.to_dict()
            if base_dir and d.get("path"):
                try:
                    rel = os.path.relpath(d["path"], base_dir)
                    if not rel.startswith(".."):
                        d["path"] = rel.replace("\\", "/")
                except ValueError:
                    pass
            clips.append(d)
        return {
            "id": self.id, "name": self.name, "color": self.color,
            "instrument": self.instrument,
            "gain": round(self.gain_db, 2), "pan": round(self.pan, 3),
            "eqLow": round(self.eq_low_db, 2), "eqHigh": round(self.eq_high_db, 2),
            "eqEnabled": self.eq_enabled,
            "mute": self.mute, "solo": self.solo,
            "fadeIn": round(self.fade_in_s, 2), "fadeOut": round(self.fade_out_s, 2),
            "collapsed": self.collapsed,
            "clips": clips,
        }

    @classmethod
    def from_dict(cls, d: dict, *, base_dir: str | None = None) -> "Track":
        import os
        t = cls(
            name=d.get("name", ""), color=d.get("color", TRACK_COLORS[0]),
            instrument=d.get("instrument", "other"),
            gain_db=float(d.get("gain", 0.0)), pan=float(d.get("pan", 0.0)),
            eq_low_db=float(d.get("eqLow", 0.0)), eq_high_db=float(d.get("eqHigh", 0.0)),
            eq_enabled=bool(d.get("eqEnabled", True)),
            mute=bool(d.get("mute", False)), solo=bool(d.get("solo", False)),
            fade_in_s=float(d.get("fadeIn", 0.0)), fade_out_s=float(d.get("fadeOut", 0.0)),
            collapsed=bool(d.get("collapsed", False)),
        )
        t.id = d.get("id") or t.id
        for cd in d.get("clips", []):
            c = Clip.from_dict(cd)
            if base_dir and c.path and not os.path.isabs(c.path):
                c.path = os.path.normpath(os.path.join(base_dir, c.path))
            t.clips.append(c)
        t.normalize()
        return t
