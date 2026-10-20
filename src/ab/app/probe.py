"""`--audio-probe`：无界面跑一段播放，量化 xrun / CPU / 延迟（§7-3 的机器可读证据）。

用法：
    AudioBench.exe --audio-probe --seconds 60 --tracks 8
输出一行 JSON，退出码 0 = xrun 为 0 且 CPU < 40%。
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np

from ..audio import backend as backend_mod
from ..audio.dsp import EqPool
from ..audio.engine import Engine
from ..audio.mixer import Mixer
from ..io.decoder import DecodeService, sample_fixture
from ..model.clip import Clip
from ..model.track import Track
from .paths import AppPaths

log = logging.getLogger("ab.probe")


def run(paths: AppPaths, *, seconds: float = 10.0, tracks: int = 8,
        sample_rate: int = 44100, block_frames: int | None = None,
        backend_kind: str = "auto", stretch: float = 1.0) -> dict:
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841

    decode = DecodeService(paths.cache_dir / "probe")
    fixture = sample_fixture(30.0, sample_rate, 440.0,
                             paths.cache_dir / "probe" / f"probe_{sample_rate}.wav")
    t_imp = time.perf_counter()
    dec = decode.ensure(fixture, sample_rate)
    import_s = time.perf_counter() - t_imp
    src = decode.source(dec)
    peaks = decode.peaks(dec)

    from ..audio.renderplan import RenderPlan, RenderTrack, build_plan
    from ..model.project import Project

    proj = Project(sample_rate=sample_rate)
    for i in range(max(1, tracks)):
        freq = 220.0 * (1.0 + i * 0.25)
        track = proj.add_track(Track(name=f"probe {i + 1}", instrument="other",
                                     eq_low_db=3.0 if i % 2 else -2.0,
                                     eq_high_db=-2.0 if i % 3 else 4.0,
                                     gain_db=-18.0))
        track.clips.append(Clip(path=dec.source_path, src_start=0, src_end=dec.frames,
                                timeline_start=0, speed=float(stretch)))
        _ = freq

    plan = build_plan(proj, lambda c: src)

    be = backend_mod.create_backend(backend_kind)
    block = int(block_frames or be.__dict__.get("_block", 0) or 256)
    be.open(sample_rate=sample_rate, block_frames=block, device=None)
    info = be.info()
    dev_sr = int(info.sample_rate or sample_rate)
    block = int(info.block_frames or block)
    eq = EqPool(be, dev_sr)
    mixer = Mixer(block, dev_sr, eq)
    engine = Engine(be, project_sample_rate=sample_rate, block_frames=block, mixer=mixer)
    engine.set_plan(plan)
    engine.open()

    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    engine.play()
    peak = 0.0
    worst_x = 0
    first_x_at = None
    while time.perf_counter() - wall0 < seconds:
        time.sleep(0.02)
        x = be.info().xruns
        if x > 0 and first_x_at is None:
            first_x_at = round(time.perf_counter() - wall0, 3)
        worst_x = max(worst_x, x)
        peak = max(peak, float(np.abs(mixer.peak).max()))
    wall = time.perf_counter() - wall0
    cpu = time.process_time() - cpu0
    final = be.info()
    engine.stop()
    engine.close()

    result = {
        "backend": final.name,
        "api": final.extra.get("api"),
        "device": final.device,
        "device_rate": final.sample_rate,
        "block_frames": final.block_frames,
        "tracks": tracks,
        "seconds": round(wall, 2),
        "xruns": int(final.xruns),
        "worst_xruns": int(worst_x),
        "latency_ms": round(float(final.latency_ms), 1),
        "cpu_percent": round(cpu / max(1e-6, wall) * 100.0, 1),
        "peak_level": round(peak, 3),
        "import_s": round(import_s, 2),
        "first_xrun_at_s": first_x_at,
        "stretch": float(stretch),
        "frames_played": int(final.extra.get("frames_played", 0) or 0),
        "pass": bool(final.xruns == 0 and cpu / max(1e-6, wall) * 100.0 < 40.0),
    }
    print(json.dumps(result, ensure_ascii=False))
    return result
