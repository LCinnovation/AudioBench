"""分离模块的轻量测试（不加载 300MB 模型）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ab.sep import models as sep_models
from ab.sep.client import estimate_seconds


def test_two_models_are_described():
    assert set(sep_models.MODELS) == {"4s", "6s"}
    four = sep_models.model_spec("4s")
    six = sep_models.model_spec("6s")
    assert four["stems"] == ["drums", "bass", "other", "vocals"]
    assert six["stems"] == ["drums", "bass", "other", "vocals", "guitar", "piano"]
    assert sep_models.DEFAULT_MODEL == "4s"


def test_unknown_model_falls_back_to_default():
    assert sep_models.model_spec("nope")["id"] == sep_models.DEFAULT_MODEL
    assert sep_models.stems_for("nope") == sep_models.stems_for("4s")


def test_model_files_resolve_to_expected_names():
    assert sep_models.resolve("4s").name == "htdemucs_4s.opt.onnx"
    assert sep_models.resolve("6s").name == "htdemucs_6s.opt.onnx"


def test_describe_reports_presence_and_runtime():
    info = sep_models.describe("4s")
    assert "present" in info and "runtime" in info
    assert info["stems"] == ["drums", "bass", "other", "vocals"]
    if info["present"]:
        assert info["size_mb"] > 100


def test_estimate_scales_with_duration_and_threads():
    assert estimate_seconds(180.0, threads=8) < estimate_seconds(180.0, threads=4)
    assert estimate_seconds(180.0) > estimate_seconds(60.0)
    assert estimate_seconds(0.0) >= 1.0


@pytest.mark.parametrize("model_id", ["4s", "6s"])
def test_stem_display_names_cover_all_stems(model_id):
    for stem in sep_models.stems_for(model_id):
        assert stem in sep_models.DISPLAY
