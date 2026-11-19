"""转码 + 封面的健壮性（真实事故：封面临时文件没扩展名，被 ffmpeg 当 mpegts 解析）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import ncm_fixture as fx  # noqa: E402

from ab.io import ffmpeg  # noqa: E402
from ab.io.decoder import sample_fixture  # noqa: E402

pytestmark = pytest.mark.skipif(ffmpeg.ffmpeg_path() is None, reason="ffmpeg not available")


def _probe_streams(path: Path) -> str:
    probe = Path(ffmpeg.ffmpeg_path()).with_name("ffprobe.exe")
    r = subprocess.run([str(probe), "-v", "error", "-show_entries",
                        "stream=codec_name,codec_type", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return r.stdout.strip()


def test_cover_suffix_by_magic():
    assert ffmpeg.cover_suffix(b"\xff\xd8\xff\xe0\x00\x10JFIF") == ".jpg"
    assert ffmpeg.cover_suffix(b"\x89PNG\r\n\x1a\n" + b"0" * 8) == ".png"
    assert ffmpeg.cover_suffix(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ".webp"
    assert ffmpeg.cover_suffix(b"GIF89a") == ".gif"
    assert ffmpeg.cover_suffix(b"\x00\x01\x02") == ".img"


def test_transcode_embeds_valid_cover(tmp_path):
    src = sample_fixture(0.5, 44100, 440.0, tmp_path / "a.wav")
    out = tmp_path / "a.mp3"
    ffmpeg.transcode(src, out, extra_args=["-c:a", "libmp3lame", "-b:a", "192k"],
                     cover=fx.make_cover(2000))
    assert out.exists() and out.stat().st_size > 1000
    streams = _probe_streams(out)
    assert "audio" in streams
    assert "video" in streams, f"封面没写进去: {streams}"


def test_transcode_survives_broken_cover(tmp_path):
    """封面坏掉不能让整首歌失败——去掉封面重试一次仍出音频。"""
    src = sample_fixture(0.5, 44100, 440.0, tmp_path / "b.wav")
    out = tmp_path / "b.mp3"
    ffmpeg.transcode(src, out, extra_args=["-c:a", "libmp3lame", "-b:a", "192k"],
                     cover=b"\x00\x01not-an-image" * 20)
    assert out.exists() and out.stat().st_size > 1000
    assert "audio" in _probe_streams(out)


def test_cover_temp_file_is_cleaned_up(tmp_path):
    src = sample_fixture(0.4, 44100, 440.0, tmp_path / "c.wav")
    out = tmp_path / "c.mp3"
    ffmpeg.transcode(src, out, extra_args=["-c:a", "libmp3lame"],
                     cover=fx.make_cover(1500))
    leftovers = [p.name for p in tmp_path.glob("*.cover*")]
    assert leftovers == [], leftovers
