"""分离客户端：起 worker 子进程、读行协议、取消、清理。

父进程与 worker 之间只有 `job.json` 与 stdout 行协议，不共享内存；
取消 = terminate + 删临时目录，因此不会留「残余轨道」。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..sep import models as sep_models

log = logging.getLogger("ab.sep.client")

CREATE_NO_WINDOW = 0x08000000


class SeparationJob(QObject):
    started = Signal(dict)
    progress = Signal(dict)          # {chunk, chunks, percent, eta_s, rss_mb}
    stage = Signal(str, dict)
    stemReady = Signal(dict)
    finished = Signal(dict)          # {dir, stems, seconds, rtf}
    failed = Signal(str, str)        # code, message

    def __init__(self, input_path: str, *, out_dir: str | None = None,
                 stems: list[str] | None = None, threads: int = 8,
                 model_id: str | None = None, model: str | None = None, parent=None):
        super().__init__(parent)
        self.input_path = str(input_path)
        self.model_id = str(model_id or sep_models.DEFAULT_MODEL)
        self.stems = stems or sep_models.stems_for(self.model_id)
        self.threads = int(threads)
        self.model = model or str(sep_models.resolve(self.model_id))
        self.tmp = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="ab_sep_"))
        self.proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._cancelled = False
        self._result: dict | None = None

    # ---------------------------------------------------------------- 启动
    def _command(self, job_path: Path) -> tuple[list[str], dict]:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        if getattr(sys, "frozen", False):
            return [sys.executable, "--sep-worker", "--job", str(job_path)], env
        root = Path(__file__).resolve().parents[3]
        env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
        return [sys.executable, "-m", "ab", "--sep-worker", "--job", str(job_path)], env

    def start(self) -> None:
        self.tmp.mkdir(parents=True, exist_ok=True)
        job = {
            "input": self.input_path,
            "out_dir": str(self.tmp),
            "stems": self.stems,
            "model_id": self.model_id,
            "threads": self.threads,
            "model": self.model,
        }
        job_path = self.tmp / "job.json"
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        cmd, env = self._command(job_path)
        log.info("sep worker: %s", " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=CREATE_NO_WINDOW, env=env)
        self._reader = threading.Thread(target=self._read_loop, name="sep-reader", daemon=True)
        self._reader.start()

    # ---------------------------------------------------------------- 读取
    def _read_loop(self) -> None:
        assert self.proc is not None
        try:
            for line in self.proc.stdout:            # type: ignore[union-attr]
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("worker noise: %s", line[:200])
                    continue
                self._dispatch(evt)
            rc = self.proc.wait()
            if self._cancelled:
                # 子进程已经退出，这时才删得掉（Windows 下进程持有文件句柄时删不掉）
                self.cleanup(remove_dir=True)
                self.failed.emit("cancelled", "cancelled by user")
            elif rc != 0 and self._result is None:
                err = (self.proc.stderr.read() if self.proc.stderr else "")[-800:]
                self.failed.emit("worker_exit", f"exit code {rc}: {err}")
        except Exception as e:
            log.exception("reader failed")
            self.failed.emit("reader_error", str(e))

    def _dispatch(self, evt: dict) -> None:
        kind = evt.get("event")
        if kind == "start":
            self.started.emit(evt)
        elif kind == "progress":
            self.progress.emit(evt)
        elif kind == "stage":
            self.stage.emit(evt.get("stage", ""), evt)
        elif kind == "stem":
            self.stemReady.emit(evt)
        elif kind == "done":
            self._result = evt
            self.finished.emit(evt)
        elif kind == "error":
            self.failed.emit(evt.get("code", "error"), evt.get("message", ""))

    # ---------------------------------------------------------------- 取消
    def cancel(self) -> None:
        """请求取消：先 terminate，目录等子进程真的退出后再删（见 _read_loop）。"""
        self._cancelled = True
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        else:
            self.cleanup(remove_dir=True)

    def cleanup(self, *, remove_dir: bool = False) -> None:
        if remove_dir and self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


def estimate_seconds(duration_s: float, rtf: float = 0.30, threads: int = 8) -> float:
    """给 UI 一个预估：默认按 M0 实测 RTF 0.30（8 线程）。"""
    scale = 1.0 if threads >= 8 else (1.25 if threads >= 4 else 1.6)
    return max(1.0, duration_s * rtf * scale)
