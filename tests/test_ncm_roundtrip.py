"""NCM 往返测试（把 M0 POC 固化成回归用例）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import ncm_fixture as fx  # noqa: E402


def test_structure():
    fx.case_structure()


def test_minimal():
    fx.case_minimal()


def test_flac_full():
    fx.case_flac_full()


def test_mp3_full():
    fx.case_mp3_full()


def test_large_payload():
    fx.case_large_payload()


def test_truncated():
    fx.case_truncated()


def test_bad_magic():
    with pytest.raises(fx.NcmFormatError):
        fx.decode_ncm(b"NOTANCMFILE" + b"\x00" * 32, name="bad.ncm")


def test_all_cases():
    assert fx.run_all() == 0
