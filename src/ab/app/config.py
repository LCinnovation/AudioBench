"""配置读写：单文件 JSON、原子替换、缺字段用默认值、点号访问。"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

CONFIG_NAME = "config.json"

DEFAULTS: dict[str, Any] = {
    "schema": 1,
    "language": "auto",              # auto | zh_CN | en_US
    "audio": {
        "backend": "auto",           # auto | shim | sounddevice
        "device": "",                # 空 = 系统默认
        "buffer_frames": 256,        # 128/256/512/1024（§2.4；默认取 256 见 M0 §6.1）
        "project_sample_rate": 44100,
        "metronome": False,
    },
    "ui": {
        "mixer_open": True,
        "zoom": 160.0,               # 每秒像素
        "window": [1280, 800],
        "last_dir": "",
    },
    "session": {
        "last_project": "",
        "last_open_ts": 0,
    },
    "compliance": {
        "ncm_ack_ts": 0,             # 7 天节流（§8）
    },
    "onboarding": {
        "done": False,
        "skipped": False,
    },
    "diagnostics": {
        "gc_tuning": True,           # 播放期冻结/关闭 GC（§6.1）
        "op_log_len": 200,
    },
}


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """跨线程只读、UI 线程写；保存走临时文件 + os.replace。"""

    def __init__(self, path: Path, data: dict | None = None):
        self.path = Path(path)
        self.data = _deep_merge(DEFAULTS, data or {})
        self._dirty = False

    # ---------------------------------------------------------------- io
    @classmethod
    def load(cls, config_dir: Path) -> "Config":
        path = Path(config_dir) / CONFIG_NAME
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                # 损坏的配置不阻塞启动：备份后重建
                try:
                    path.replace(path.with_suffix(f".broken-{int(time.time())}.json"))
                except Exception:
                    pass
                data = {}
        return cls(path, data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".config-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            self._dirty = False
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def touch(self) -> None:
        self._dirty = True

    # ---------------------------------------------------------------- access
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, dotted: str, value: Any, *, save: bool = False) -> None:
        parts = dotted.split(".")
        cur = self.data
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
        self._dirty = True
        if save:
            self.save()

    @property
    def dirty(self) -> bool:
        return self._dirty
