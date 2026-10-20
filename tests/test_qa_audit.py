"""QA 审计测试（本轮自主测试新增）。

原则：断言写的是**规格要求**，不是当前实现。因此预期失败的用例，就是缺陷清单的证据。
分组：A 工程模型  B 撤销栈  C 片段几何  D 混音渲染  E 导出
      F 峰值缓存  G NCM 转换  H 配置  I 双语完整性
"""
from __future__ import annotations

import json
import re
import wave
from pathlib import Path

import numpy as np
import pytest

from ab.audio.mixer import Mixer
from ab.audio.renderplan import RenderClip, RenderPlan, RenderTrack
from ab.io import ffmpeg, exporter, peakcache
from ab.io.waveshim import AudioSource
from ab.model.clip import Clip
from ab.model.project import Project
from ab.model.track import Track
from ab.model.undo import UndoStack

SR = 44100


# --------------------------------------------------------------------- 工具
def _f32(tmp_path: Path, data: np.ndarray, name: str = "s.f32") -> Path:
    p = tmp_path / name
    np.ascontiguousarray(data, dtype=np.float32).tofile(str(p))
    return p


def _src(tmp_path: Path, data: np.ndarray, name: str = "s.f32") -> AudioSource:
    return AudioSource(_f32(tmp_path, data, name), data.shape[0])


def _const(n: int, v: float = 1.0) -> np.ndarray:
    return np.full((n, 2), v, dtype=np.float32)


def _sine(n: int, amp: float = 0.5, freq: float = 440.0) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / SR
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([x, x], axis=1)


def _clip(source: AudioSource, *, start=0, length=None, **kw) -> RenderClip:
    length = source.frames if length is None else length
    base = dict(source=source, clip_id=f"c{id(source)}", timeline_start=0,
                timeline_end=length, src_start=start, gain=1.0,
                fade_in=0, fade_out=0, fade_shape="linear")
    base.update(kw)
    return RenderClip(**base)


def _track(clips, *, pan=1.0, **kw) -> RenderTrack:
    """pan=1.0 表示左右都用 1.0，方便断言绝对幅度。"""
    base = dict(index=0, gain=1.0, pan_l=pan, pan_r=pan, mute=False, solo=False,
                eq_on=False, eq_low=0.0, eq_high=0.0,
                fade_in=0, fade_out=0, first=0, last=clips[0].timeline_end if clips else 0,
                clips=list(clips))
    base.update(kw)
    return RenderTrack(**base)


def _plan(tracks, end=None) -> RenderPlan:
    end = end if end is not None else max((t.last for t in tracks), default=0)
    return RenderPlan(sample_rate=SR, tracks=list(tracks), end=end,
                      any_solo=any(t.solo for t in tracks))


def _read_wav_peak(path: Path) -> tuple[int, float]:
    with wave.open(str(path), "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return n, float(np.abs(a).max())


# =============================================================== A 工程模型
def test_project_roundtrip_with_unicode_and_spaces(tmp_path):
    """中文+空格目录下的工程应能无损保存/打开（§6）。"""
    d = tmp_path / "工程 目录"
    d.mkdir()
    p = Project(sample_rate=48000, name="扒带 练习")
    t = p.add_track(Track(name="主唱", instrument="vocal"))
    t.gain_db = -6.5
    t.eq_high_db = 3.25
    t.fade_in_s = 1.5
    t.clips.append(Clip(path=str(d / "素材.wav"), src_start=100, src_end=1000,
                        timeline_start=200, fade_in=50, speed=1.25))
    saved = p.save(d / "练习 工程.abproj")
    assert saved.exists()

    loaded, missing = Project.load(saved)
    assert loaded.name == "扒带 练习"
    assert loaded.sample_rate == 48000
    assert loaded.tracks[0].name == "主唱"
    assert loaded.tracks[0].gain_db == pytest.approx(-6.5)
    assert loaded.tracks[0].clips[0].speed == pytest.approx(1.25)
    # 工程内用相对路径，换机器仍能定位
    raw = json.loads(saved.read_text(encoding="utf-8"))
    assert not Path(raw["tracks"][0]["clips"][0]["path"]).is_absolute()
    assert missing and missing[0].endswith("素材.wav")


def test_project_load_missing_file_raises_readable(tmp_path):
    """损坏的工程文件必须报错，而不是静默返回半个工程（§3-P0-7）。"""
    bad = tmp_path / "bad.abproj"
    bad.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(Exception) as ei:
        Project.load(bad)
    assert "bad.abproj" in str(ei.value) or isinstance(ei.value, json.JSONDecodeError)


def test_track_normalize_clamps_all_ranges(tmp_path):
    """越界参数必须被钳制，避免坏工程把音频算成 inf/nan（§4.3）。"""
    t = Track(gain_db=99, pan=-5, eq_low_db=40, eq_high_db=-40,
              fade_in_s=-3, fade_out_s=99, instrument="kazoo")
    t.normalize()
    assert t.gain_db == 12.0
    assert t.pan == -1.0
    assert t.eq_low_db == 12.0
    assert t.eq_high_db == -12.0
    assert t.fade_in_s == 0.0 and t.fade_out_s == 10.0
    assert t.instrument == "other"


def test_track_volume_floor_is_silence(tmp_path):
    """-60dB 必须是静音而不是 -60dB 的残响（§4.3 推子下限）。"""
    t = Track(gain_db=-60)
    assert t.gain_linear == 0.0


# =============================================================== B 撤销栈
def test_undo_redo_50_edits_are_consistent():
    """连续 50 次编辑后逐步撤销/重做必须回到相同状态（§7-4）。"""
    box = [0]

    def snap():
        return {"v": box[0]}

    def restore(d):
        box[0] = d["v"]

    st = UndoStack(snap, restore)
    for i in range(1, 51):
        st.begin(f"edit{i}")
        box[0] = i
        assert st.commit()
    assert st.depth()[0] == 50
    for _ in range(50):
        st.undo()
    assert box[0] == 0
    for _ in range(50):
        st.redo()
    assert box[0] == 50


def test_undo_capacity_drops_oldest():
    """容量上限生效，且不因溢出而错乱（要求 ≥30）。"""
    box = {"v": 0}
    st = UndoStack(lambda: dict(box), lambda d: box.update(d), capacity=5)
    for i in range(1, 9):
        st.begin(f"e{i}")
        box["v"] = i
        st.commit()
    assert st.depth()[0] == 5
    for _ in range(5):
        st.undo()
    assert box["v"] == 3          # 最早三条已被丢弃


def test_commit_after_undo_does_not_merge_into_older_entry():
    """撤销后立刻做同类操作，不能把新改动并进「上一条不相关的记录」。

    否则一次 Ctrl+Z 会跨过两条历史，用户看到的是「撤销跳步」。
    """
    box = {"v": 0}
    st = UndoStack(lambda: dict(box), lambda d: box.update(d))
    for label, val in (("A", 1), ("B", 2)):
        st.begin(label)
        box["v"] = val
        st.commit()
    st.undo()
    assert box["v"] == 1
    st.begin("B")            # 与上一条同标签，且间隔 <800ms
    box["v"] = 3
    st.commit()
    st.undo()
    assert box["v"] == 1, "撤销跳过了 B 的历史，合并到了 A 上"


# =============================================================== C 片段几何
def test_clip_speed_scales_timeline_length():
    c = Clip(src_start=0, src_end=44100, speed=2.0)
    assert c.length == 22050
    c.speed = 0.5
    assert c.length == 88200


def test_clip_fades_never_exceed_length():
    """淡入+淡出之和不得超过片段长度，否则增益曲线会算出负值（§3-P0-4）。"""
    c = Clip(src_start=0, src_end=1000, fade_in=800, fade_out=800)
    c.clamp()
    assert c.fade_in + c.fade_out <= c.length
    assert 0 <= c.fade_in <= 1000 and 0 <= c.fade_out <= 1000


def test_clip_speed_out_of_range_is_clamped():
    c = Clip(src_start=0, src_end=1000, speed=99.0)
    c.clamp()
    assert c.speed == 2.0


def test_clip_read_past_source_end_is_zero(tmp_path):
    """片段被拖到素材尾部之外时应补零，而不是读越界（§4.4）。"""
    s = _src(tmp_path, _const(100, 1.0))
    out = s.read(90, 30)
    assert out.shape == (30, 2)
    assert np.allclose(out[:10], 1.0)
    assert np.allclose(out[10:], 0.0)


# =============================================================== D 混音渲染
def test_mute_produces_silence(tmp_path):
    s = _src(tmp_path, _const(512, 1.0))
    plan = _plan([_track([_clip(s, length=512)], mute=True, first=0, last=512)])
    out = np.zeros((512, 2), dtype=np.float32)
    Mixer(512, SR).render(plan, 0, out)
    assert np.allclose(out, 0.0)


def test_solo_isolates_track(tmp_path):
    a = _src(tmp_path, _const(512, 0.25), "a.f32")
    b = _src(tmp_path, _const(512, 0.5), "b.f32")
    t0 = _track([_clip(a, length=512)], first=0, last=512, index=0)
    t1 = _track([_clip(b, length=512)], first=0, last=512, index=1, solo=True)
    plan = _plan([t0, t1])
    out = np.zeros((512, 2), dtype=np.float32)
    Mixer(512, SR).render(plan, 0, out)
    assert np.allclose(out, 0.5, atol=1e-6)


def test_clip_fade_in_is_linear(tmp_path):
    """片段淡入：起点 0、中点 0.5、淡入结束后 1.0（§3-P0-4）。"""
    s = _src(tmp_path, _const(1000, 1.0))
    plan = _plan([_track([_clip(s, length=1000, fade_in=100)], first=0, last=1000)])
    out = np.zeros((1000, 2), dtype=np.float32)
    Mixer(1000, SR).render(plan, 0, out)
    assert out[0, 0] == pytest.approx(0.0, abs=1e-6)
    assert out[50, 0] == pytest.approx(0.5, abs=2e-3)
    assert out[150, 0] == pytest.approx(1.0, abs=1e-6)


def test_clip_fade_out_is_linear(tmp_path):
    s = _src(tmp_path, _const(1000, 1.0))
    plan = _plan([_track([_clip(s, length=1000, fade_out=100)], first=0, last=1000)])
    out = np.zeros((1000, 2), dtype=np.float32)
    Mixer(1000, SR).render(plan, 0, out)
    assert out[899, 0] == pytest.approx(1.0, abs=2e-2)
    assert out[950, 0] == pytest.approx(0.5, abs=2e-2)
    assert out[999, 0] == pytest.approx(0.01, abs=2e-2)


def test_track_fade_in_ramp(tmp_path):
    """轨道级渐入从轨道首个片段起点开始（§3-P0-4）。"""
    s = _src(tmp_path, _const(1000, 1.0))
    plan = _plan([_track([_clip(s, length=1000)], first=0, last=1000, fade_in=100)])
    out = np.zeros((1000, 2), dtype=np.float32)
    Mixer(1000, SR).render(plan, 0, out)
    assert out[0, 0] == pytest.approx(0.0, abs=1e-6)
    assert out[99, 0] == pytest.approx(1.0, abs=2e-2)


def test_partial_block_render_is_correct(tmp_path):
    """尾部不满一块时（导出最后一块）不能崩、也不能错位。"""
    s = _src(tmp_path, _const(1000, 0.4))
    plan = _plan([_track([_clip(s, length=1000)])])
    mixer = Mixer(512, SR)
    out = np.zeros((200, 2), dtype=np.float32)
    mixer.render(plan, 800, out)
    assert np.allclose(out, 0.4, atol=1e-6)


def test_master_gain_scales_output(tmp_path):
    s = _src(tmp_path, _const(256, 1.0))
    plan = _plan([_track([_clip(s, length=256)])])
    out = np.zeros((256, 2), dtype=np.float32)
    Mixer(256, SR).render(plan, 0, out, master_gain=0.5)
    assert np.allclose(out, 0.5, atol=1e-6)


def test_stretched_clip_keeps_block_length(tmp_path):
    """变速片段必须返回与请求完全一致的帧数，否则总线累加会错位（§3-P0-5）。"""
    s = _src(tmp_path, _sine(44100))
    plan = _plan([_track([_clip(s, length=20000, speed=1.5)], last=20000)])
    out = np.zeros((4096, 2), dtype=np.float32)
    Mixer(4096, SR).render(plan, 0, out)
    assert out.shape == (4096, 2)
    assert np.isfinite(out).all()
    assert np.abs(out).max() > 0.01


# =============================================================== E 导出
def _make_project(tmp_path, *, amp=0.5, seconds=1.0, gain_db=0.0, pan=0.0):
    frames = int(SR * seconds)
    path = tmp_path / "素材.wav"
    data = _sine(frames, amp=amp)
    p16 = np.clip(data * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(p16.tobytes())

    proj = Project(sample_rate=SR, name="QA导出")
    tr = proj.add_track(Track(name="T1", instrument="other"))
    tr.gain_db = gain_db
    tr.pan = pan
    tr.clips.append(Clip(path=str(path), src_start=0, src_end=frames, timeline_start=0))
    src = AudioSource(_f32(tmp_path, data, "qa.f32"), frames)
    return proj, (lambda c: src), frames


def test_export_wav_length_and_peak(tmp_path):
    proj, resolve, frames = _make_project(tmp_path)
    settings = exporter.ExportSettings(fmt="wav16", out_dir=tmp_path / "out",
                                       template="{track}")
    res = exporter.export(proj, resolve, settings)
    assert len(res.files) == 1 and res.files[0].exists()
    n, peak = _read_wav_peak(res.files[0])
    assert abs(n - frames) <= 2
    # 中央声像 -3dB：0.5 * cos(45°) ≈ 0.3536
    assert peak == pytest.approx(0.3536, abs=0.02)


def test_export_empty_project_raises_readable(tmp_path):
    """空工程导出必须给出可读失败，且不留半成品。

    0.4.3 起约定改为「可预期失败写进 result.errors 并返回，不向上抛」，
    这样 UI 与脚本拿到的是同一份结果（以前会抛 export_empty 字符串给用户看）。
    """
    proj = Project(sample_rate=SR, name="空")
    proj.add_track(Track(name="T1"))
    settings = exporter.ExportSettings(fmt="wav16", out_dir=tmp_path / "out")
    res = exporter.export(proj, lambda c: None, settings)
    assert res.errors, "空工程导出没有报错"
    assert not res.files
    # 失败不留半成品
    assert not list((tmp_path / "out").glob("*.wav")) if (tmp_path / "out").exists() else True


def test_export_reports_true_peak_and_clipping(tmp_path):
    """导出结果必须回报真实峰值，>1.0 要标 clipped（§7-5 削波检查）。"""
    proj, resolve, _ = _make_project(tmp_path, amp=0.9, gain_db=6.0)
    settings = exporter.ExportSettings(fmt="wav16", out_dir=tmp_path / "out", template="{track}")
    res = exporter.export(proj, resolve, settings)
    assert res.peak == pytest.approx(0.9 * 0.7071 * 1.995, rel=0.05)
    assert res.clipped is True


def test_export_respects_master_gain(tmp_path):
    """导出必须与试听一致：母带推子 -6dB 时，导出峰值也应是 -6dB（§7-5）。"""
    proj, resolve, _ = _make_project(tmp_path)
    settings = exporter.ExportSettings(fmt="wav16", out_dir=tmp_path / "out", template="{track}")
    try:
        res = exporter.export(proj, resolve, settings, master_gain_db=-6.0)
    except TypeError:
        pytest.fail("export() 没有母带增益入口，导出结果与试听不一致")
    _, peak = _read_wav_peak(res.files[0])
    assert peak == pytest.approx(0.3536 * 0.5012, abs=0.02)


def test_safe_name_strips_illegal_chars():
    """文件名模板/轨道名里的非法字符必须被替换（Windows 上否则无法落盘）。"""
    assert exporter.safe_name('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert exporter.safe_name("   ...   ") == "AudioBench"
    assert len(exporter.safe_name("x" * 500)) == 120


def test_render_name_bad_template_falls_back():
    out = exporter.render_name("{nope}", track="T", index=3)
    assert "T" in out and "03" in out


# =============================================================== F 峰值缓存
def test_peak_columns_match_signal_shape(tmp_path):
    """波形概览必须忠实反映静音段与满幅段（§4.4）。"""
    data = np.zeros((SR, 2), dtype=np.float32)
    data[: SR // 2] = 0.8
    f32 = _f32(tmp_path, data)
    pk = peakcache.build(f32, data.shape[0], tmp_path / "x.abpk",
                         sample_rate=SR, channels=2)
    cache = peakcache.PeakCache.open(pk)
    assert cache is not None
    mins, maxs = cache.overview(100)
    assert maxs.shape == (2, 100) and mins.shape == (2, 100)
    assert maxs[:, :45].max() == pytest.approx(0.8, abs=0.02)
    assert abs(maxs[:, 55:]).max() < 0.02


def test_peak_columns_handle_width_larger_than_bins(tmp_path):
    """缩放到「一像素不到一个 bin」时不能越界（§4.4）。"""
    data = _sine(1000, amp=0.5)
    f32 = _f32(tmp_path, data)
    pk = peakcache.build(f32, 1000, tmp_path / "y.abpk", sample_rate=SR, channels=2)
    cache = peakcache.PeakCache.open(pk)
    mins, maxs = cache.columns(0, 1000, 4000)
    assert maxs.shape == (2, 4000)
    assert np.isfinite(maxs).all() and np.isfinite(mins).all()


def test_corrupt_peak_cache_returns_none(tmp_path):
    bad = tmp_path / "bad.abpk"
    bad.write_bytes(b"not a npz")
    assert peakcache.PeakCache.open(bad) is None


# =============================================================== G NCM
def test_ncm_header_truncated_reports_filename(tmp_path):
    """头部被截断（下载中断在元数据段）必须报错并带文件名（§3-P0-8）。"""
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    import ncm_fixture as fx

    blob = fx.encode_ncm(fx.make_flac(0.2))
    p = tmp_path / "trunc.ncm"
    p.write_bytes(blob[:40])
    from ab.ncm import decoder as ncm

    with pytest.raises(ncm.NcmFormatError) as ei:
        ncm.read_file(p)
    assert "trunc.ncm" in str(ei.value)


@pytest.mark.xfail(strict=True,
                   reason="read_file 只解容器不转码，无法判断载荷完整性；"
                          "截断检测已下移到 convert 层（STREAMINFO 声明样本数 vs 实际解码样本数）")
def test_ncm_payload_truncated_is_detected(tmp_path):
    """载荷截断在 decoder 层仍不可判断（NCM 头没有载荷长度字段）。

    convert() 现在会做交叉校验：FLAC STREAMINFO 声明的样本数 vs 转码后的实际样本数，
    差超过 1% 就判失败并删掉输出（见 qa_suite 的 G3 用例）。
    """
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    import ncm_fixture as fx

    blob = fx.encode_ncm(fx.make_flac(0.2))
    p = tmp_path / "half.ncm"
    p.write_bytes(blob[: len(blob) // 2])
    from ab.ncm import decoder as ncm

    with pytest.raises(ncm.NcmFormatError):
        ncm.read_file(p)


def test_ncm_bad_magic_reports_reason():
    from ab.ncm import decoder as ncm

    with pytest.raises(ncm.NcmFormatError) as ei:
        ncm.decode_ncm(b"NOTANCM" + b"\x00" * 64)
    assert "magic" in str(ei.value)


def test_ncm_batch_does_not_overwrite_previous_output(tmp_path):
    """同名文件连续转换 3 次必须产生 3 个文件，不能覆盖上一次结果（§3-P0-8）。"""
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    import ncm_fixture as fx

    from ab.ncm.convert import NcmJob, convert

    payload = fx.make_flac(0.2)
    src = tmp_path / "song.ncm"
    src.write_bytes(fx.encode_ncm(payload))
    out_dir = tmp_path / "out"
    for _ in range(3):
        convert(NcmJob(src=src, out_dir=out_dir, fmt="flac"))
    files = sorted(p.name for p in out_dir.glob("*.flac"))
    assert len(files) == 3, f"只生成了 {files}，第三次覆盖了 (2)"
    assert "song (3).flac" in files


# =============================================================== H 配置
def test_config_merges_defaults_and_persists(tmp_path):
    from ab.app.config import Config

    cfg = Config.load(tmp_path)
    assert cfg.get("audio.buffer_frames") == 256
    cfg.set("ui.zoom", 240.0, save=True)
    again = Config.load(tmp_path)
    assert again.get("ui.zoom") == 240.0
    assert again.get("audio.backend") == "auto"      # 缺字段回落默认值


def test_config_corrupt_file_does_not_block_startup(tmp_path):
    from ab.app.config import Config

    (tmp_path / "config.json").write_text("{broken", encoding="utf-8")
    cfg = Config.load(tmp_path)
    assert cfg.get("audio.project_sample_rate") == 44100
    assert list(tmp_path.glob("config.broken-*.json"))


# =============================================================== I 双语
def test_all_source_strings_have_chinese_translation():
    """源码里每个 tr(ctx, text) 都必须有中文译文，且 .qm 不旧于 .ts（§4.6）。

    复用项目自己的 AST 提取器：正则会在隐式字符串拼接处截断，从而误报。
    """
    from ab.tools import make_i18n

    gaps = make_i18n.missing()
    assert not gaps, f"{len(gaps)} 条文案未翻译，例如：{gaps[:5]}"

    ts = make_i18n.OUT / "ab_zh_CN.ts"
    qm = ts.with_suffix(".qm")
    assert qm.exists(), "缺少编译产物 .qm，界面会全英文"
    assert qm.stat().st_mtime >= ts.stat().st_mtime, ".qm 比 .ts 旧，运行时仍是旧翻译"
