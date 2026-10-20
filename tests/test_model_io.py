"""工程读写、撤销栈、峰值缓存、时间格式的单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ab.io import peakcache
from ab.model.clip import Clip
from ab.model.project import Project
from ab.model.track import Track
from ab.model.undo import UndoStack
from ab.ui.timefmt import samples_to_bars, samples_to_time, time_to_samples


def _project(tmp: Path) -> Project:
    p = Project(sample_rate=48000)
    t = p.add_track(Track(name="Vocal", instrument="vocal", gain_db=-3.0, pan=-0.3))
    t.clips.append(Clip(path="a.wav", src_start=0, src_end=48000, timeline_start=0))
    t2 = p.add_track(Track(name="Bass", instrument="bass", fade_in_s=1.5))
    t2.clips.append(Clip(path="b.wav", src_start=100, src_end=20000, timeline_start=48000))
    for name in ("a.wav", "b.wav"):          # 占位文件：让「缺失检查」有东西可比
        (tmp / name).write_bytes(b"RIFF0000WAVE")
    return p


def test_project_roundtrip(tmp_path):
    p = _project(tmp_path)
    path = tmp_path / "song.abproj"
    p.save(path)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["sampleRate"] == 48000
    assert len(data["tracks"]) == 2
    p2, missing = Project.load(path)
    assert missing == []
    assert [t.name for t in p2.tracks] == ["Vocal", "Bass"]
    assert p2.tracks[1].fade_in_s == 1.5
    assert p2.tracks[0].clips[0].src_end == 48000
    assert p2.duration == 48000 + 19900


def test_project_relative_paths(tmp_path):
    p = _project(tmp_path)
    path = tmp_path / "song.abproj"
    p.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tracks"][0]["clips"][0]["path"] == "a.wav"   # 相对路径优先


def test_missing_audio_reported(tmp_path):
    p = _project(tmp_path)
    path = tmp_path / "song.abproj"
    p.save(path)
    for name in ("a.wav", "b.wav"):
        (tmp_path / name).unlink()
    _, missing = Project.load(path)
    assert sorted(Path(m).name for m in missing) == ["a.wav", "b.wav"]


def test_undo_redo_and_coalesce():
    state = {"v": 0}
    stack = UndoStack(lambda: dict(state), lambda d: state.update(d))
    stack.begin("gain")
    state["v"] = 1
    assert stack.commit()
    assert stack.depth() == (1, 0)
    stack.undo()
    assert state["v"] == 0
    stack.redo()
    assert state["v"] == 1

    # 撤销/重做会断开合并链：此后同标签的改动必须自成一条，
    # 否则一次 Ctrl+Z 会连红线一起回退（QA-03 修复）。
    stack.begin("gain")
    state["v"] = 2
    stack.commit()
    assert stack.depth()[0] == 2
    stack.undo()
    assert state["v"] == 1
    stack.redo()
    assert state["v"] == 2

    # 同一标签 800ms 内的连续拖动仍然合并成一条
    stack.clear()
    for v in (3, 4, 5):
        stack.begin("gain")
        state["v"] = v
        stack.commit()
    assert stack.depth()[0] == 1
    assert state["v"] == 5
    stack.undo()
    assert state["v"] == 2          # 回到这一串拖动之前
    stack.redo()
    # 换标签则不合并
    stack.begin("mute")
    state["v"] = 9
    stack.commit()
    assert stack.depth()[0] == 2
    stack.begin("mute")
    state["v"] = 9
    stack.commit()
    assert stack.depth()[0] == 2


def test_undo_capacity():
    state = {"v": 0}
    stack = UndoStack(lambda: dict(state), lambda d: state.update(d), capacity=5)
    for i in range(20):
        stack.begin(f"op{i}")
        state["v"] = i + 1
        stack.commit()
    assert stack.depth()[0] == 5


def test_peakcache_roundtrip(tmp_path):
    sr = 48000
    frames = sr * 2
    t = np.arange(frames, dtype=np.float32) / sr
    stereo = np.stack([0.5 * np.sin(2 * np.pi * 440 * t)] * 2, axis=1)
    f32 = tmp_path / "x.f32"
    stereo.tofile(f32)
    out = tmp_path / "x.abpk"
    peakcache.build(f32, frames, out, sample_rate=sr)
    pc = peakcache.PeakCache.open(out)
    assert pc is not None and pc.frames == frames
    assert [lv.bin for lv in pc.levels[:3]] == [256, 512, 1024]
    mins, maxs = pc.columns(0, frames, 400)
    assert mins.shape == (2, 400) and maxs.shape == (2, 400)
    assert maxs.max() > 0.4 and mins.min() < -0.4


def test_timefmt():
    assert samples_to_time(0, 44100) == "00:00:00.000"
    assert samples_to_time(44100, 44100) == "00:00:01.000"
    assert samples_to_time(44100 * 3661, 44100) == "01:01:01.000"
    assert time_to_samples("00:00:01.500", 44100) == 66150
    assert time_to_samples("bad", 44100) is None
    assert samples_to_bars(0, 48000, 120.0) == "001.01.000"
