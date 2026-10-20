"""ncm 转换链路（fixture 自造 → 解码 → 转码 → 标签/封面）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import ncm_fixture as fx  # noqa: E402

from ab.io import ffmpeg  # noqa: E402
from ab.ncm import convert as ncm_convert  # noqa: E402
from ab.ncm import decoder  # noqa: E402


def _make_ncm(tmp: Path, meta: dict | None = None) -> Path:
    payload = fx.make_flac(0.6)
    blob = fx.encode_ncm(payload, metadata=meta or fx.META, cover=fx.make_cover(2000))
    path = tmp / "song.ncm"
    path.write_bytes(blob)
    return path


def test_decode_matches_fixture(tmp_path):
    ncm = _make_ncm(tmp_path)
    data = decoder.read_file(ncm)
    assert data["container"] == "flac"
    assert data["crc_ok"] is True
    assert data["metadata"]["musicName"] == fx.META["musicName"]
    assert data["cover"][:8] == b"\x89PNG\r\n\x1a\n"
    friendly = decoder.friendly_meta(data["metadata"])
    assert friendly["title"] == fx.META["musicName"]
    assert friendly["artist"] == "测试歌手/第二歌手"


@pytest.mark.skipif(ffmpeg.ffmpeg_path() is None, reason="ffmpeg not available")
def test_convert_to_mp3_and_flac(tmp_path):
    ncm = _make_ncm(tmp_path)
    out_dir = tmp_path / "out"

    job = ncm_convert.NcmJob(src=ncm, out_dir=out_dir, fmt="mp3_320")
    out = ncm_convert.convert(job)
    assert out.exists() and out.suffix == ".mp3"
    info = ffmpeg.probe(out)
    assert 0.4 < info.duration_s < 0.9
    assert out.stat().st_size > 10_000

    job2 = ncm_convert.NcmJob(src=ncm, out_dir=out_dir, fmt="flac")
    out2 = ncm_convert.convert(job2)
    assert out2.suffix == ".flac"
    assert ffmpeg.probe(out2).duration_s > 0.4


@pytest.mark.skipif(ffmpeg.ffmpeg_path() is None, reason="ffmpeg not available")
def test_convert_failure_isolated(tmp_path):
    good = _make_ncm(tmp_path)
    bad = tmp_path / "broken.ncm"
    bad.write_bytes(b"NOTANCMFILE" + b"\x00" * 64)
    jobs = [
        ncm_convert.NcmJob(src=good, out_dir=tmp_path / "o", fmt="mp3_128"),
        ncm_convert.NcmJob(src=bad, out_dir=tmp_path / "o", fmt="mp3_128"),
    ]
    ncm_convert.convert_many(jobs)
    assert jobs[0].status == "done"
    assert jobs[1].status == "failed"
    csv = ncm_convert.failed_csv(jobs)
    assert "broken.ncm" in csv and "file,error" in csv


def test_unique_out_never_overwrites(tmp_path):
    """同名输出必须依次避让：name → name (2) → name (3)…（BUG 报告 §4.1）。"""
    d = tmp_path
    assert ncm_convert._unique_out(d, "song", "flac").name == "song.flac"
    (d / "song.flac").write_bytes(b"OLD-1")
    assert ncm_convert._unique_out(d, "song", "flac").name == "song (2).flac"
    (d / "song (2).flac").write_bytes(b"OLD-2")
    # 关键：目录里已有 (2) 时，新名字必须跳到 (3)，不能覆盖 OLD-2
    assert ncm_convert._unique_out(d, "song", "flac").name == "song (3).flac"
    for i in range(3, 8):
        (d / f"song ({i}).flac").write_bytes(b"x")
    assert ncm_convert._unique_out(d, "song", "flac").name == "song (8).flac"


@pytest.mark.skipif(ffmpeg.ffmpeg_path() is None, reason="ffmpeg not available")
def test_repeated_convert_keeps_all_three_files(tmp_path):
    """同一 job 连跑 3 次 → 3 个文件，谁都不许被覆盖（BUG 报告 §5.2）。"""
    ncm = _make_ncm(tmp_path)
    out_dir = tmp_path / "out3"
    outs = []
    for _ in range(3):
        outs.append(ncm_convert.convert(ncm_convert.NcmJob(src=ncm, out_dir=out_dir, fmt="flac")))
    names = sorted(p.name for p in outs)
    assert names == ["song (2).flac", "song (3).flac", "song.flac"], names
    assert all(p.exists() for p in outs)
    assert len(list(out_dir.glob("*.flac"))) == 3
    assert all(p.stat().st_size > 1000 for p in outs)


@pytest.mark.skipif(ffmpeg.ffmpeg_path() is None, reason="ffmpeg not available")
def test_preexisting_second_name_is_preserved(tmp_path):
    """预置 song.flac + song (2).flac，再转换 1 次 → 新文件落到 (3)，旧内容不变。"""
    ncm = _make_ncm(tmp_path)
    out_dir = tmp_path / "pre"
    out_dir.mkdir()
    (out_dir / "song.flac").write_bytes(b"OLD-1")
    (out_dir / "song (2).flac").write_bytes(b"OLD-2")
    out = ncm_convert.convert(ncm_convert.NcmJob(src=ncm, out_dir=out_dir, fmt="flac"))
    assert out.name == "song (3).flac", f"落到了 {out.name}"
    assert (out_dir / "song.flac").read_bytes() == b"OLD-1"
    assert (out_dir / "song (2).flac").read_bytes() == b"OLD-2"
