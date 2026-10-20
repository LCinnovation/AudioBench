"""片段（Clip）：时间线上的一个引用，含源区间、增益与淡入淡出。

坐标一律用**采样点**（工程采样率），避免浮点漂移；界面层再换算成秒/像素。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

FADE_SHAPES = ("linear", "exp")
SPEED_MIN, SPEED_MAX = 0.5, 2.0
PITCH_MIN, PITCH_MAX = -12.0, 12.0


@dataclass
class Clip:
    path: str = ""                    # 源文件路径（工程里优先存相对路径）
    src_start: int = 0                # 源内起点（采样）
    src_end: int = 0                  # 源内终点（采样，不含）
    timeline_start: int = 0           # 时间线起点（采样）
    gain_db: float = 0.0
    fade_in: int = 0                  # 采样
    fade_out: int = 0                 # 采样
    fade_shape: str = "linear"
    speed: float = 1.0                # 0.5–2.0（1.0 = 原速）
    pitch: float = 0.0                # 半音 −12…+12
    name: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # ---------------------------------------------------------------- 几何
    @property
    def length(self) -> int:
        """时间线长度：变速后源区间会被拉长/压短。"""
        src = max(0, self.src_end - self.src_start)
        if src <= 0:
            return 0
        sp = self.speed if SPEED_MIN <= self.speed <= SPEED_MAX else 1.0
        return int(round(src / sp))

    @property
    def timeline_end(self) -> int:
        return self.timeline_start + self.length

    @property
    def is_stretched(self) -> bool:
        return abs(self.speed - 1.0) > 1e-6 or abs(self.pitch) > 1e-6

    def overlaps(self, start: int, end: int) -> bool:
        return self.timeline_start < end and self.timeline_end > start

    def clamp(self) -> None:
        self.src_start = max(0, self.src_start)
        # 至少留 1 帧：0 长度片段会在渲染/分割里变成除零或空区间
        self.src_end = max(self.src_start + 1, self.src_end)
        self.timeline_start = max(0, self.timeline_start)
        self.speed = min(SPEED_MAX, max(SPEED_MIN, float(self.speed or 1.0)))
        self.pitch = min(PITCH_MAX, max(PITCH_MIN, float(self.pitch or 0.0)))
        self.fade_in = max(0, min(self.fade_in, self.length))
        self.fade_out = max(0, min(self.fade_out, self.length - self.fade_in))
        if self.fade_shape not in FADE_SHAPES:
            self.fade_shape = "linear"

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "id": self.id, "path": self.path, "name": self.name,
            "srcStart": self.src_start, "srcEnd": self.src_end,
            "timelineStart": self.timeline_start,
            "gain": round(self.gain_db, 3),
            "fadeIn": self.fade_in, "fadeOut": self.fade_out, "fadeShape": self.fade_shape,
            "speed": round(self.speed, 4), "pitch": round(self.pitch, 2),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        c = cls(
            path=d.get("path", ""), name=d.get("name", ""),
            src_start=int(d.get("srcStart", 0)), src_end=int(d.get("srcEnd", 0)),
            timeline_start=int(d.get("timelineStart", 0)),
            gain_db=float(d.get("gain", 0.0)),
            fade_in=int(d.get("fadeIn", 0)), fade_out=int(d.get("fadeOut", 0)),
            fade_shape=d.get("fadeShape", "linear"),
            speed=float(d.get("speed", 1.0)), pitch=float(d.get("pitch", 0.0)),
        )
        c.id = d.get("id") or c.id
        c.clamp()
        return c

    def copy(self) -> "Clip":
        c = Clip.from_dict(self.to_dict())
        c.id = uuid.uuid4().hex[:12]
        return c
