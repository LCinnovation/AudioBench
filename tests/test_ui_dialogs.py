"""冒烟测试：每个对话框都能构造，右键菜单能建起来。

教训（0.4.1 实测）：`NcmDialog` 少了一个方法、`contextMenuEvent` 用了
`QContextMenuEvent.position()`（不存在，只有 pos()），两处都直接让程序弹崩溃框。
凡是「只在真人点开时才走到」的代码，都必须有这种构造级测试兜着。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp(tmp_path_factory):
    os.environ["AB_DATA_ROOT"] = str(tmp_path_factory.mktemp("ui_smoke"))
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    # 关窗时若工程是脏的会弹「是否保存」，无头环境里 exec() 会永久阻塞。
    # 这里只关心「会不会崩」，不关心弹窗内容。
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Discard)
    yield app


@pytest.fixture(scope="module")
def state(qapp):
    from ab.app.config import Config
    from ab.app.paths import resolve_paths
    from ab.app.state import AppState

    paths = resolve_paths()
    return AppState(Config.load(paths.config_dir), paths)


def test_ncm_dialog_constructs(state):
    from ab.ui.dialogs.ncm import NcmDialog

    dlg = NcmDialog(state)
    assert dlg.out_dir.text()
    assert dlg.fmt.count() >= 3
    assert callable(dlg._pick_dir)
    dlg.close()


def test_export_dialog_constructs(state):
    from ab.ui.dialogs.export import ExportDialog

    dlg = ExportDialog(state)
    assert dlg.fmt.count() >= 5
    dlg.close()


def test_sep_dialog_constructs(state):
    from ab.ui.dialogs.sep import SeparationDialog

    from ab.sep import models as sep_models

    dlg = SeparationDialog(state)
    assert len(dlg.stem_boxes) == len(sep_models.stems_for(dlg._current_model()))
    assert dlg.model_combo.count() >= 2
    assert dlg.model_label.text()
    dlg.close()


def test_help_panel_constructs(state):
    from ab.ui.dialogs.help import HelpPanel

    dlg = HelpPanel(None, on_replay=lambda: None)
    assert dlg.windowTitle()
    dlg.close()


def test_timeline_ruler_menu_builds(state, qapp):
    from ab.ui.timeline import RulerWidget

    ruler = RulerWidget(state)
    ruler.resize(800, 28)
    menu = ruler._build_menu(120.0)
    assert menu.actions(), "右键菜单不能是空的"
    # 有标记时菜单要多两项
    state.project.add_marker(44100, "M1")
    menu2 = ruler._build_menu(120.0)
    assert len(menu2.actions()) >= len(menu.actions())
    state.project.markers.clear()


def test_canvas_menu_builds(state, qapp):
    from PySide6.QtCore import QPoint

    from ab.ui.timeline import TimelineCanvas

    canvas = TimelineCanvas(state)
    canvas.resize(900, 400)
    menu, acts = canvas._build_menu(QPoint(50, 20))
    assert menu.actions()
    assert "split" in acts and "clip" in acts


def test_ncm_rows_update_by_identity_not_name(state, qapp, tmp_path):
    """三个同名 ncm：状态必须各自更新到自己的行（BUG 报告 §4.3）。

    老实现按文件名匹配，三行都会命中第一行，后两行一直停在 pending。
    """
    from ab.ui.dialogs.ncm import NcmDialog

    dlg = NcmDialog(state)
    files = []
    for i in range(3):
        sub = tmp_path / f"album{i}"
        sub.mkdir()
        p = sub / "song.ncm"
        p.write_bytes(b"x")
        files.append(str(p))
    dlg.add_paths(files)
    assert dlg.table.rowCount() == 3

    for i, job in enumerate(dlg.jobs):
        job.status = "done"
        job.message = f"out{i}"
        dlg._refresh_row(job)
    statuses = [dlg.table.item(r, 1).text() for r in range(3)]
    assert statuses == ["done", "done", "done"], statuses
    messages = [dlg.table.item(r, 2).text() for r in range(3)]
    assert messages == ["out0", "out1", "out2"], messages

    dlg.jobs[1].status = "failed"
    dlg.jobs[1].message = "boom"
    dlg._refresh_row(dlg.jobs[1])
    assert [dlg.table.item(r, 1).text() for r in range(3)] == ["done", "failed", "done"]
    dlg.close()


def test_create_stem_tracks_mutes_source_and_names_by_song(state, qapp, tmp_path):
    """一键建轨：源轨必须静音，轨道名必须用曲名而不是缓存目录名。

    这两个 bug 是拿真实歌曲跑端到端时发现的（0.4.6 修复）。
    """
    from ab.io.decoder import sample_fixture
    from ab.model.clip import Clip
    from ab.model.track import Track
    from ab.ui.main_window import MainWindow

    state.new_project()
    w1 = sample_fixture(1.0, state.project.sample_rate, 440.0, tmp_path / "vocals.wav")
    w2 = sample_fixture(1.0, state.project.sample_rate, 220.0, tmp_path / "drums.wav")
    dec = state.decode.ensure(w1, state.project.sample_rate)
    state._sources[dec.source_path] = state.decode.source(dec)
    src = state.project.add_track(Track(name="MySong", instrument="other"))
    src.clips.append(Clip(path=dec.source_path, name="src", src_start=0, src_end=dec.frames))
    state.rebuild_plan()

    win = MainWindow(state, None)
    win.create_stem_tracks({
        "dir": str(tmp_path / "some_cache_dir_123"),
        "source": str(tmp_path / "MySong.mp3"),
        "source_name": "MySong",
        "source_track": 0,
        "stems": [{"name": "vocals", "path": str(w1)}, {"name": "drums", "path": str(w2)}],
    })
    qapp.processEvents()
    assert src.mute is True, "源轨没有静音"
    names = [t.name for t in state.project.tracks[1:]]
    assert names == ["MySong · Vocals", "MySong · Drums"], names
    for t in state.project.tracks[1:]:
        assert t.clips and t.clips[0].timeline_start == 0
    win.close()


def test_main_window_constructs(state, qapp):
    from ab.ui.main_window import MainWindow

    win = MainWindow(state, None)
    assert win.inspector is not None
    # 走带条上的功能按钮都接好了槽
    for key in ("convert", "separate", "export", "settings", "help"):
        btn = win.transport.feature_buttons.get(key)
        assert btn is not None and btn.isEnabled() or key in ("convert", "separate")
    win.close()


def test_track_header_shows_name_from_start(state, qapp):
    """长轨道名必须从开头显示（QLineEdit 默认滚到结尾，会把「沈腾-平凡之路」
    显示成「凡之路(15647731_2)」）。完整名走 tooltip。"""
    from ab.model.track import Track
    from ab.ui.track_header import TrackHeader

    long_name = "沈腾-平凡之路(15647731_2)"
    h = TrackHeader(state, 0, Track(name=long_name))
    h.resize(220, 72)
    assert h.name.text() == long_name
    assert h.name.cursorPosition() == 0
    assert h.name.toolTip() == long_name
    h.deleteLater()


def test_create_stem_tracks_survives_mojibake_paths(state, qapp, tmp_path):
    """回传路径被编码污染时，必须能按 out_dir 兜底建轨。

    真实事故（0.5.0）：冻结版 worker 的 stdout 用 GBK 编码，父进程按 UTF-8 解码，
    中文目录名变成 `????`，6 条 stem 全部「找不到文件」→ 界面显示「已分离出 0 条音轨」。
    """
    from ab.io.decoder import sample_fixture
    from ab.model.clip import Clip
    from ab.model.track import Track
    from ab.ui.main_window import MainWindow

    state.new_project()
    stem_dir = tmp_path / "沈腾-平凡之路(15647731_2)_1700000000"
    stem_dir.mkdir()
    wav = sample_fixture(1.0, state.project.sample_rate, 440.0, stem_dir / "vocals.wav")
    dec = state.decode.ensure(wav, state.project.sample_rate)
    state._sources[dec.source_path] = state.decode.source(dec)
    src = state.project.add_track(Track(name="MySong"))
    src.clips.append(Clip(path=dec.source_path, name="src", src_start=0, src_end=dec.frames))
    state.rebuild_plan()

    broken = tmp_path / "??-????_1700000000"          # 模拟乱码后的目录名
    win = MainWindow(state, None)
    win.create_stem_tracks({
        "dir": str(broken),
        "out_dir": str(stem_dir),                     # 父进程自己算的，没被污染
        "source": str(tmp_path / "MySong.mp3"),
        "source_name": "MySong",
        "source_track": 0,
        "stems": [{"name": "vocals", "path": f"{broken}/vocals.wav"}],
    })
    qapp.processEvents()
    names = [t.name for t in state.project.tracks[1:]]
    assert names == ["MySong · Vocals"], names
    win.close()
