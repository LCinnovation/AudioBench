"""日志：滚动文件（10MB × 3）+ 控制台 + 最近操作环形缓冲。"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import time
from collections import deque
from pathlib import Path

from .paths import AppPaths

LOG_NAME = "AudioBench"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 3
FMT = "%(asctime)s.%(msecs)03d %(levelname)-5s %(name)-18s %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"


class OpLog:
    """最近 N 条用户操作，崩溃时随栈一起落盘（§6 要求「最近 200 条操作日志」）。"""

    def __init__(self, capacity: int = 200):
        self._items: deque[str] = deque(maxlen=capacity)

    def add(self, message: str) -> None:
        self._items.append(f"{time.strftime('%H:%M:%S')} {message}")

    def dump(self) -> list[str]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)


_op_log = OpLog()


def op_log() -> OpLog:
    return _op_log


def log_file(paths: AppPaths) -> Path:
    return Path(paths.log_dir) / "AudioBench.log"


def setup_logging(paths: AppPaths, *, level: int = logging.INFO, console: bool | None = None) -> logging.Logger:
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    fh = logging.handlers.RotatingFileHandler(
        log_file(paths), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(FMT, DATEFMT))
    root.addHandler(fh)

    if console is None:
        console = not getattr(sys, "frozen", False)
    if console:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter("%(levelname)-5s %(name)s: %(message)s"))
        root.addHandler(sh)

    log = logging.getLogger("ab")
    log.info("mode=%s pid=%d frozen=%s", paths.mode, os.getpid(), bool(getattr(sys, "frozen", False)))
    log.info("config=%s", paths.config_dir)
    log.info("cache=%s", paths.cache_dir)
    log.info("logs=%s", paths.log_dir)
    if paths.note:
        log.warning("path fallback: %s", paths.note)
    return log
