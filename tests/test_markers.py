"""标记点（P2-11）：模型层的最小契约。"""

from __future__ import annotations

import json

from ab.model.project import Project


def _proj() -> Project:
    p = Project(sample_rate=48000, name="m")
    p.add_marker(48000, "Verse")
    p.add_marker(96000, "Chorus")
    return p


def test_markers_sorted_by_position():
    p = Project(sample_rate=48000)
    p.add_marker(96000, "B")
    p.add_marker(1000, "A")
    assert [m["name"] for m in p.markers] == ["A", "B"]


def test_marker_lookup_and_remove():
    p = _proj()
    assert p.marker_at(48000)["name"] == "Verse"
    assert p.marker_at(48001)["name"] == "Verse"      # 容差内
    assert p.marker_at(12345) is None
    assert p.remove_marker_at(48000) is True
    assert p.remove_marker_at(48000) is False
    assert len(p.markers) == 1


def test_markers_roundtrip(tmp_path):
    p = _proj()
    path = tmp_path / "m.abproj"
    p.save(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert len(raw["markers"]) == 2
    p2, _ = Project.load(path)
    assert [m["name"] for m in p2.markers] == ["Verse", "Chorus"]
    assert p2.markers[1]["pos"] == 96000


def test_old_project_without_markers_loads(tmp_path):
    """向后兼容：老工程没有 markers 字段也不能炸。"""
    path = tmp_path / "old.abproj"
    path.write_text(json.dumps({"version": 1, "sampleRate": 44100, "tracks": []}),
                    encoding="utf-8")
    p, missing = Project.load(path)
    assert p.markers == []
    assert missing == []
