"""§7-4：连续 200 次编辑后撤销/重做正确；保存重开后参数一致。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from ab.model.clip import Clip
from ab.model.project import Project
from ab.model.track import Track
from ab.model.undo import UndoStack


class Harness:
    """模拟 AppState.edit 的最小实现（不依赖 Qt）。"""

    def __init__(self, project: Project):
        self.project = project
        self.undo = UndoStack(self._snapshot, self._restore)

    def _snapshot(self) -> dict:
        return json.loads(json.dumps(self.project.to_dict()))

    def _restore(self, data: dict) -> None:
        self.project = Project.from_dict(data)

    @contextmanager
    def edit(self, label: str):
        self.undo.begin(label)
        try:
            yield self.project
        finally:
            self.undo.commit()


def _make_project(tmp: Path) -> Project:
    p = Project(sample_rate=48000, name="edit-test")
    for i in range(3):
        t = p.add_track(Track(name=f"T{i}", instrument="other", gain_db=-6.0 + i))
        a = Clip(path=str(tmp / f"a{i}.wav"), name=f"a{i}", src_start=0, src_end=48000 * 4,
                 timeline_start=0, fade_in=1000, fade_out=1000)
        b = Clip(path=str(tmp / f"b{i}.wav"), name=f"b{i}", src_start=0, src_end=48000 * 2,
                 timeline_start=48000 * 5, fade_in=500, fade_out=500)
        t.clips += [a, b]
    return p


def test_two_hundred_edits_undo_redo(tmp_path):
    p = _make_project(tmp_path)
    h = Harness(p)
    initial = h._snapshot()

    labels = ["Move clip", "Trim clip", "Clip fade in", "Fade out", "Volume", "Split clip"]
    for i in range(200):
        with h.edit(labels[i % len(labels)]):
            t = h.project.tracks[i % 3]
            c = t.clips[i % len(t.clips)]
            k = i % 6
            if k == 0:
                c.timeline_start = (c.timeline_start + 480) % 48000
                c.clamp()
            elif k == 1:
                c.src_end = max(c.src_start + 1000, c.src_end - 240)
                c.clamp()
            elif k == 2:
                c.fade_in = (c.fade_in + 240) % 4000
                c.clamp()
            elif k == 3:
                t.fade_out_s = (i % 50) / 10.0
            elif k == 4:
                t.gain_db = -30.0 + (i % 40)
            else:
                c.speed = 0.5 + (i % 4) * 0.5
                c.clamp()

    final = h._snapshot()
    assert final != initial
    depth, _ = h.undo.depth()
    # 有些编辑产生的是同样的快照（例如 fade 取模后回到原值），不会入栈，所以按实际深度撤销
    assert depth >= 150, f"200 次编辑只留下 {depth} 条撤销，太少了"

    for _ in range(depth):
        h.undo.undo()
    assert h._snapshot() == initial, "撤销到底后必须回到初始状态"

    for _ in range(depth):
        h.undo.redo()
    assert h._snapshot() == final, "重做到底后必须回到最终状态"


def test_project_roundtrip_keeps_fades_and_gain(tmp_path):
    p = _make_project(tmp_path)
    for t in p.tracks:
        t.eq_low_db = 3.5
        t.eq_high_db = -2.5
        t.pan = 0.25
        t.fade_in_s = 1.2
        t.fade_out_s = 0.8
    p.tracks[0].clips[0].speed = 1.5
    p.tracks[0].clips[0].pitch = -3.0
    p.tracks[0].clips[0].fade_shape = "exp"
    p.tracks[0].clips[0].clamp()

    path = tmp_path / "roundtrip.abproj"
    p.save(path)
    p2, _missing = Project.load(path)

    assert len(p2.tracks) == 3
    t0 = p2.tracks[0]
    assert (t0.eq_low_db, t0.eq_high_db, t0.pan) == (3.5, -2.5, 0.25)
    assert (t0.fade_in_s, t0.fade_out_s) == (1.2, 0.8)
    c0 = t0.clips[0]
    assert (c0.speed, c0.pitch, c0.fade_shape) == (1.5, -3.0, "exp")
    assert c0.length == round((c0.src_end - c0.src_start) / 1.5)
    assert c0.fade_in <= c0.length and c0.fade_out <= c0.length - c0.fade_in


def test_split_preserves_total_length(tmp_path):
    p = _make_project(tmp_path)
    clip = p.tracks[0].clips[0]
    total = clip.length
    cut_at = clip.timeline_start + total // 3
    offset = cut_at - clip.timeline_start
    right = clip.copy()
    right.timeline_start = cut_at
    right.src_start = clip.src_start + int(round(offset * clip.speed))
    clip.src_end = right.src_start
    clip.clamp()
    right.clamp()
    assert clip.length + right.length == total
    assert right.timeline_start == clip.timeline_end
