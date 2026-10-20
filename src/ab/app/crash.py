"""全局异常钩子：crash-<ts>.log（栈 + 最近 200 条操作 + 版本/路径）。"""
from __future__ import annotations

import faulthandler
import logging
import os
import platform
import sys
import time
import traceback
from pathlib import Path

from ..version import APP_NAME, BUILD_DATE, VERSION
from .logging_setup import op_log
from .paths import AppPaths

_fault_file = None


def install(paths: AppPaths) -> Path:
    """安装 excepthook + faulthandler，返回本次进程的 crash 目录。"""
    crash_dir = Path(paths.crash_dir)
    crash_dir.mkdir(parents=True, exist_ok=True)

    global _fault_file
    try:
        _fault_file = open(crash_dir / "faulthandler.log", "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except Exception:
        _fault_file = None

    def _hook(exc_type, exc, tb):  # noqa: ANN001
        path = write_crash_log(crash_dir, exc_type, exc, tb, paths)
        try:
            logging.getLogger("ab.crash").error("unhandled exception -> %s", path, exc_info=(exc_type, exc, tb))
        except Exception:
            pass
        try:
            _show_dialog(path)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook
    return crash_dir


def write_crash_log(crash_dir: Path, exc_type, exc, tb, paths: AppPaths | None = None) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = Path(crash_dir) / f"crash-{ts}.log"
    lines = [
        f"{APP_NAME} crash report",
        f"version={VERSION} build={BUILD_DATE}",
        f"time={time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"os={platform.platform()}",
        f"python={sys.version.split()[0]} frozen={bool(getattr(sys, 'frozen', False))}",
        f"exe={sys.executable}",
        f"paths={paths.describe() if paths else '-'}",
        "",
        "---- traceback ----",
    ]
    if tb is not None:
        lines.extend(traceback.format_exception(exc_type, exc, tb))
    else:
        lines.append(repr(exc))
    lines.append("")
    lines.append("---- last operations ----")
    lines.extend(op_log().dump() or ["(none)"])
    path.write_text("\n".join(str(x) for x in lines), encoding="utf-8")
    return path


def _show_dialog(path: Path) -> None:
    if os.environ.get("AB_NO_GUI") or "--selftest" in sys.argv:
        return
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except Exception:
        return
    if QApplication.instance() is None:
        return
    QMessageBox.critical(
        None,
        "AudioBench",
        f"An unexpected error occurred.\n日志已写入 / log written to:\n{path}",
    )
