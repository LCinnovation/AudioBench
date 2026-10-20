"""双形态数据目录（§2.3）。

规则：
  * `<exe_dir>/portable.flag` 存在 → 便携模式：全部写 `<exe_dir>/userdata/`；
  * 否则 → 安装模式：配置/日志写 `%APPDATA%\\AudioBench\\`，缓存写 `%LOCALAPPDATA%\\AudioBench\\cache\\`；
  * 两种模式启动时都做一次写测试，失败退到 `%TEMP%\\AudioBench-<pid>` 并在界面标注。

测试或特殊场景可用环境变量 `AB_DATA_ROOT` 强制指定数据根目录（只影响本进程）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR_NAME = "AudioBench"
PORTABLE_FLAG = "portable.flag"


def app_dir() -> Path:
    """exe 所在目录（冻结后是 exe 目录，源码运行时是仓库根目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def asset_dir() -> Path:
    """只读资源目录（字体/图标/.qm/模型）。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "assets"
    return app_dir() / "assets"


def is_portable() -> bool:
    return (app_dir() / PORTABLE_FLAG).exists()


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".ab_write_test_{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class AppPaths:
    mode: str                       # portable | install | temp
    root: Path                      # 数据根（便携的 userdata / 安装的 APPDATA 组合）
    config_dir: Path
    cache_dir: Path
    log_dir: Path
    crash_dir: Path
    writable: bool = True
    note: str = ""
    extra: dict = field(default_factory=dict)

    def ensure(self) -> "AppPaths":
        for d in (self.config_dir, self.cache_dir, self.log_dir, self.crash_dir,
                  self.cache_dir / "decoded", self.cache_dir / "waveforms"):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        return self

    def describe(self) -> dict:
        return {
            "mode": self.mode,
            "root": str(self.root),
            "config": str(self.config_dir),
            "cache": str(self.cache_dir),
            "logs": str(self.log_dir),
            "crash": str(self.crash_dir),
            "writable": self.writable,
            "note": self.note,
        }


def _temp_paths(pid: int, note: str) -> AppPaths:
    root = Path(tempfile.gettempdir()) / f"{APP_DIR_NAME}-{pid}"
    return AppPaths(
        mode="temp",
        root=root,
        config_dir=root,
        cache_dir=root / "cache",
        log_dir=root / "logs",
        crash_dir=root / "crash",
        writable=_writable(root),
        note=note,
    ).ensure()


def _home() -> Path:
    try:
        return Path.home()
    except Exception:
        return Path(tempfile.gettempdir())


def resolve_paths() -> AppPaths:
    override = os.environ.get("AB_DATA_ROOT")
    if override:
        root = Path(override)
        return AppPaths(
            mode="portable",
            root=root,
            config_dir=root,
            cache_dir=root / "cache",
            log_dir=root / "logs",
            crash_dir=root / "crash",
            writable=_writable(root),
            note="AB_DATA_ROOT override",
        ).ensure()

    if is_portable():
        root = app_dir() / "userdata"
        if _writable(root):
            return AppPaths(
                mode="portable", root=root, config_dir=root,
                cache_dir=root / "cache", log_dir=root / "logs",
                crash_dir=root / "crash",
            ).ensure()
        return _temp_paths(os.getpid(), "portable dir not writable")

    appdata = Path(os.environ.get("APPDATA", _home() / "AppData" / "Roaming")) / APP_DIR_NAME
    local = Path(os.environ.get("LOCALAPPDATA", _home() / "AppData" / "Local")) / APP_DIR_NAME
    if _writable(appdata) and _writable(local / "cache"):
        return AppPaths(
            mode="install", root=appdata, config_dir=appdata, cache_dir=local / "cache",
            log_dir=appdata / "logs", crash_dir=appdata / "crash",
        ).ensure()
    return _temp_paths(os.getpid(), "appdata not writable")
