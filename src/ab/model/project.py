"""工程（Project）：`.abproj` 的读写与轨道集合操作。

格式见任务书 §6；额外字段（createdWith / ui）只做前向兼容，旧版本读到时忽略。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from pathlib import Path

from ..version import PROJECT_EXT, TRACK_COLORS, VERSION
from .track import Track

log = logging.getLogger("ab.project")

FILE_VERSION = 1


class Project:
    def __init__(self, sample_rate: int = 44100, name: str = ""):
        self.sample_rate = int(sample_rate)
        self.name = name
        self.tracks: list[Track] = []
        self.markers: list[dict] = []          # [{"pos": int, "name": str}]
        self.path: Path | None = None
        self.ui: dict = {}
        self.id = uuid.uuid4().hex[:12]

    # ---------------------------------------------------------------- 查询
    @property
    def duration(self) -> int:
        return max((t.clips_end() for t in self.tracks), default=0)

    @property
    def any_solo(self) -> bool:
        return any(t.solo for t in self.tracks)

    def next_color(self) -> str:
        return TRACK_COLORS[len(self.tracks) % len(TRACK_COLORS)]

    def track_names(self) -> list[str]:
        return [t.name for t in self.tracks]

    # ---------------------------------------------------------------- 结构修改
    def add_track(self, track: Track | None = None, index: int | None = None) -> Track:
        if track is None:
            n = len(self.tracks) + 1
            track = Track(name=f"Track {n}", color=self.next_color(),
                          instrument="other")
        if index is None:
            self.tracks.append(track)
        else:
            self.tracks.insert(max(0, min(index, len(self.tracks))), track)
        track.normalize()
        return track

    def remove_track(self, index: int) -> Track | None:
        if 0 <= index < len(self.tracks):
            return self.tracks.pop(index)
        return None

    def move_track(self, src: int, dst: int) -> None:
        if src == dst or not (0 <= src < len(self.tracks)):
            return
        t = self.tracks.pop(src)
        dst = max(0, min(dst, len(self.tracks)))
        self.tracks.insert(dst, t)

    def clear(self) -> None:
        self.tracks.clear()
        self.markers.clear()
        self.path = None
        self.ui = {}

    # ---------------------------------------------------------------- 标记点
    def add_marker(self, pos: int, name: str = "") -> dict:
        marker = {"pos": max(0, int(pos)), "name": name or f"M{len(self.markers) + 1}"}
        self.markers.append(marker)
        self.markers.sort(key=lambda m: m["pos"])
        return marker

    def remove_marker_at(self, pos: int, tolerance: int = 1) -> bool:
        for m in list(self.markers):
            if abs(m["pos"] - pos) <= tolerance:
                self.markers.remove(m)
                return True
        return False

    def marker_at(self, pos: int, tolerance: int = 1) -> dict | None:
        for m in self.markers:
            if abs(m["pos"] - pos) <= tolerance:
                return m
        return None

    # ---------------------------------------------------------------- 序列化
    def to_dict(self, *, base_dir: str | None = None) -> dict:
        if base_dir is None and self.path is not None:
            base_dir = str(self.path.parent)
        return {
            "version": FILE_VERSION,
            "createdWith": VERSION,
            "id": self.id,
            "name": self.name,
            "sampleRate": self.sample_rate,
            "ui": self.ui,
            "markers": list(self.markers),
            "tracks": [t.to_dict(base_dir=base_dir) for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, d: dict, *, base_dir: str | None = None) -> "Project":
        p = cls(sample_rate=int(d.get("sampleRate", 44100)), name=d.get("name", ""))
        p.id = d.get("id") or p.id
        p.ui = d.get("ui", {}) or {}
        p.markers = [dict(m) for m in (d.get("markers") or []) if "pos" in m]
        for td in d.get("tracks", []):
            p.tracks.append(Track.from_dict(td, base_dir=base_dir))
        return p

    # ---------------------------------------------------------------- 磁盘
    def save(self, path: str | Path) -> Path:
        path = Path(path)
        if path.suffix.lower() != f".{PROJECT_EXT}":
            path = path.with_suffix(f".{PROJECT_EXT}")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict(base_dir=str(path.parent))
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".abproj-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        self.path = path
        return path

    @classmethod
    def load(cls, path: str | Path) -> tuple["Project", list[str]]:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("version", 0)) > FILE_VERSION:
            log.warning("project version %s is newer than supported %s", data.get("version"), FILE_VERSION)
        p = cls.from_dict(data, base_dir=str(path.parent))
        p.path = path
        p.name = p.name or path.stem
        missing = sorted({c.path for t in p.tracks for c in t.clips
                          if c.path and not Path(c.path).exists()})
        return p, missing
