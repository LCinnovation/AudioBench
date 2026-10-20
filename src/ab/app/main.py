"""入口：GUI / --selftest / --audio-probe / --sep-worker。

启动顺序（每一步都写日志，Wine 下靠日志定位）：
  路径 → 日志 → 崩溃钩子 → 配置 → Qt → 字体 → i18n → 状态 → 引擎 → 主窗口
"""
from __future__ import annotations

import argparse
import json
import ctypes
import logging
import os
import sys
from pathlib import Path

from ..version import APP_NAME, APP_ID, VERSION
from . import paths as paths_mod
from .config import Config
from .i18n import I18n, tr
from .logging_setup import op_log, setup_logging

log = logging.getLogger("ab")


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog=APP_NAME, add_help=True)
    ap.add_argument("--open", metavar="PROJECT", help="open a project on startup")
    ap.add_argument("--selftest", action="store_true", help="run diagnostics and exit")
    ap.add_argument("--deep", action="store_true", help="deep selftest (loads model, encodes mp3)")
    ap.add_argument("--out", metavar="FILE", help="selftest json output path")
    ap.add_argument("--audio-probe", action="store_true", help="headless playback probe")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--tracks", type=int, default=8)
    ap.add_argument("--buffer", type=int, default=0, help="override block frames")
    ap.add_argument("--stretch", type=float, default=1.0, help="probe: clip speed factor")
    ap.add_argument("--backend", default="", help="shim | sounddevice | auto")
    ap.add_argument("--scale", type=float, default=0.0, help="UI scale factor (e.g. 1.0)")
    ap.add_argument("--lang", default="", help="zh_CN | en_US | auto")
    ap.add_argument("--sep-worker", action="store_true", help="internal: separation worker")
    ap.add_argument("--job", default="", help="internal: worker job json path")
    ap.add_argument("--ncm-batch", nargs="+", metavar="FILE",
                    help="batch convert .ncm files headlessly, print JSON result")
    ap.add_argument("--ncm-out", default="", help="output folder for --ncm-batch")
    ap.add_argument("--ncm-fmt", default="mp3_320",
                    help="mp3_320 | mp3_192 | mp3_128 | flac | keep")
    ap.add_argument("--data-root", default="", help="override data directory (testing)")
    ap.add_argument("--capture", default="", help="internal: save a window screenshot then exit")
    ap.add_argument("--capture-after", type=int, default=1200, help="ms before capture")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    return ap.parse_args(argv)


def _apply_env(args: argparse.Namespace) -> None:
    if args.data_root:
        os.environ["AB_DATA_ROOT"] = args.data_root
    if args.scale and args.scale > 0:
        os.environ["QT_SCALE_FACTOR"] = f"{args.scale:g}"
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    # Wine 下强制 raster，不碰 OpenGL/D3D（§9 R2）
    os.environ.setdefault("QT_OPENGL", "software")


def _set_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.{VERSION}")
    except Exception:
        pass


def _load_fonts() -> str:
    from PySide6.QtGui import QFontDatabase

    from .paths import asset_dir

    fonts_dir = asset_dir() / "fonts"
    loaded = []
    if fonts_dir.exists():
        for f in sorted(fonts_dir.glob("*.ttf")) + sorted(fonts_dir.glob("*.otf")):
            if QFontDatabase.addApplicationFont(str(f)) >= 0:
                loaded.append(f.name)
    family = "AB Sans SC" if "AB Sans SC" in QFontDatabase.families() else ""
    log.info("fonts loaded: %s (family=%s)", loaded or "none", family or "system")
    return family


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    _apply_env(args)
    _set_app_id()

    paths = paths_mod.resolve_paths()
    setup_logging(paths)
    # 音频生产者需要在 UI 重绘时也能及时拿到 GIL：把切换间隔压到 1ms
    sys.setswitchinterval(0.001)
    from . import crash

    crash.install(paths)
    config = Config.load(paths.config_dir)
    log.info("%s %s mode=%s", APP_NAME, VERSION, paths.mode)
    op_log().add(f"start {VERSION} mode={paths.mode}")

    if args.sep_worker:
        from ..sep.worker import run_worker

        return run_worker(args.job, paths)

    if args.audio_probe:
        from PySide6.QtWidgets import QApplication

        _ = QApplication.instance() or QApplication([APP_NAME])
        from .probe import run as run_probe

        try:
            res = run_probe(paths, seconds=args.seconds, tracks=args.tracks,
                            block_frames=args.buffer or None,
                            backend_kind=args.backend or "auto",
                            stretch=args.stretch)
        except Exception as e:
            log.exception("probe failed")
            print(f'{{"error": "{e}"}}')
            return 2
        return 0 if res.get("pass") else 1

    if args.ncm_batch:
        from pathlib import Path as _Path

        from ..ncm import convert as ncm_convert

        out_dir = _Path(args.ncm_out) if args.ncm_out else _Path(args.ncm_batch[0]).parent
        jobs = [ncm_convert.NcmJob(src=_Path(p), out_dir=out_dir, fmt=args.ncm_fmt)
                for p in args.ncm_batch]
        ncm_convert.convert_many(jobs)
        report = [{"src": str(j.src), "status": j.status, "out": str(j.out_path or ""),
                   "message": j.message} for j in jobs]
        print(json.dumps({"out_dir": str(out_dir), "jobs": report}, ensure_ascii=False))
        return 0 if all(j.status == "done" for j in jobs) else 1

    if args.selftest:
        from PySide6.QtWidgets import QApplication

        _ = QApplication.instance() or QApplication([APP_NAME])
        from .selftest import run as run_selftest

        return run_selftest(args.out, deep=args.deep, paths=paths)

    return _run_gui(args, paths, config)


def _run_gui(args: argparse.Namespace, paths, config: Config) -> int:
    # Qt 必须在设置 DPI 环境变量之后、创建窗口之前导入
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, False) if hasattr(
        Qt, "AA_ShareOpenGLContexts") else None
    app = QApplication.instance() or QApplication([APP_NAME])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName(APP_NAME)

    from ..ui import theme as _theme  # 延迟导入：避免 selftest 也付 Qt 主题初始化成本

    _theme.apply_palette(app)
    family = _load_fonts()
    base = app.font()
    if family:
        base.setFamily(family)
    base.setPointSize(10)
    app.setFont(base)
    app.setStyleSheet(_theme.STYLESHEET)

    pref = args.lang or config.get("language", "auto")
    i18n = I18n(pref, app)
    i18n.install()
    log.info("i18n: %s", i18n.describe())

    from PySide6.QtCore import QTimer

    from .state import AppState
    from ..ui.main_window import MainWindow

    state = AppState(config, paths, i18n)
    state.set_master_db(float(config.get("ui.master_db", 0.0) or 0.0))
    win = MainWindow(state, i18n)
    op_log().add("main window ready")

    # 恢复上次工程 / --open（有草稿时先问恢复）
    # --capture 是非交互模式：不能弹模态框，否则截图/自动化会卡在对话框上。
    recovered = (win.maybe_recover_draft()
                 if not (args.open or getattr(args, "capture", "")) else False)
    target = args.open or config.get("session.last_project", "")
    if target and Path(target).exists() and not recovered:
        try:
            missing = state.open_project(target)
            state.mark_clean()
            if missing:
                log.warning("missing audio: %s", missing)
            win._refresh_views()
            win.update_title()
        except Exception as e:
            log.exception("open project failed")
            win._show_error(tr("Dialog", "Cannot open project"), f"{target}\n{e}")

    if not state.start_engine():
        win.statusBar().showMessage(tr("Status", "Audio device unavailable — see log"), 8000)

    # 语言切换即时生效
    def _retranslate_all(_lang: str) -> None:
        win.retranslate()

    i18n.languageChanged.connect(_retranslate_all)

    win.show()
    log.info("window shown")
    op_log().add("window shown")

    if getattr(args, "capture", ""):
        def _grab() -> None:
            log.info("capture: start")
            try:
                Path(args.capture).parent.mkdir(parents=True, exist_ok=True)
                win.grab().save(args.capture, "PNG")
                log.info("captured %s", args.capture)
            except Exception:
                log.exception("capture failed")
            log.info("capture: marking clean + closing window")
            try:
                state.mark_clean()
                win.close()
            except Exception:
                log.exception("close failed")
            log.info("capture: app.quit()")
            app.quit()

        QTimer.singleShot(int(getattr(args, "capture_after", 1200)), _grab)

    # 首次启动给一个可听见的示例轨道（空工程时），方便验收「导入/播放」
    if not state.project.tracks and not target:
        QTimer.singleShot(300, lambda: _seed_demo(state, win))

    # 新手引导：没走完也没跳过才自动弹（§7-11）
    win.maybe_show_onboarding()

    rc = app.exec()
    log.info("event loop exited rc=%d", rc)
    state.stop_engine()
    config.save()
    log.info("exit rc=%d", rc)
    return rc


def _seed_demo(state, win) -> None:
    """空工程时生成一个 6 秒的示例音轨（不是内置版权素材，是程序合成的正弦）。"""
    try:
        from ..io.decoder import sample_fixture

        wav = sample_fixture(6.0, state.project.sample_rate, 440.0,
                             Path(state.paths.cache_dir) / "demo" / "demo_tone.wav")
        dec = state.decode.ensure(wav, state.project.sample_rate)
        state._sources[dec.source_path] = state.decode.source(dec)
        pc = state.decode.peaks(dec)
        if pc is not None:
            state._peaks[dec.source_path] = pc
        from ..model.clip import Clip
        from ..model.track import Track

        with state.edit("Demo track"):
            t = state.project.add_track(Track(name="Demo tone", color=state.project.next_color(),
                                              instrument="other"))
            t.clips.append(Clip(path=dec.source_path, name="demo_tone",
                                src_start=0, src_end=dec.frames, timeline_start=0))
        state.structureChanged.emit()
        win._refresh_views()
    except Exception:
        log.exception("demo seed failed")


if __name__ == "__main__":       # 允许 `python -m ab.app.main --selftest`
    sys.exit(main(sys.argv[1:]))
