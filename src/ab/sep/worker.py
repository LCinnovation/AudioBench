"""分离 worker（`AudioBench.exe --sep-worker --job job.json`）。

为什么独立进程：模型要 ~1GB 常驻、峰值 2.4GB，跑在 UI 进程里一旦崩就是整机体验崩；
独立进程还能「杀进程=取消」「退出即回收内存」。

协议：stdout 逐行 JSON（start/progress/stem/done/error），stderr 只进日志。
内存：stem 累加用 memmap 落盘，避免 3 分钟歌再叠 380MB。
"""
from __future__ import annotations

import gc
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("ab.sep.worker")

SAMPLE_RATE = 44100
SEGMENT = 343980                 # 7.8s @44.1k，模型固定 shape
STEMS = ("drums", "bass", "other", "vocals", "guitar", "piano")   # 模型输出顺序
DISPLAY = {"drums": "Drums", "bass": "Bass", "other": "Other (incl. strings)",
           "vocals": "Vocals", "guitar": "Guitar", "piano": "Piano"}
MEM_LIMIT_MB = 1800


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 钉死成 UTF-8。

    冻结版（PyInstaller）在中文 Windows 上会把 stdout 当成 GBK，父进程按 UTF-8 解码，
    回传的路径里中文变成 `����`，于是「分离成功但找不到 stem 文件」——0.5.0 踩过。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
        except Exception:
            pass


def emit(payload: dict) -> None:
    """逐行 JSON。直接写 UTF-8 字节，绕开平台默认编码。"""
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    data = line.encode("utf-8", "replace")
    try:
        buf = getattr(sys.stdout, "buffer", None)
        if buf is not None:
            buf.write(data)
            buf.flush()
        else:
            sys.stdout.write(line)
            sys.stdout.flush()
    except Exception:
        pass


def _mem_mb() -> tuple[float, float]:
    """返回 (当前工作集 MB, 峰值工作集 MB)。

    门禁看「当前工作集」——它就是任务管理器「内存」列，也是真正压系统的量；
    峰值工作集包含了 ORT 分配后又归还的瞬态，作为参考值一起报告。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        k32 = ctypes.windll.kernel32
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC),
                                                wintypes.DWORD]
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return (0.0, 0.0)
        return (float(pmc.WorkingSetSize) / 1048576.0,
                float(pmc.PeakWorkingSetSize) / 1048576.0)
    except Exception:
        return (0.0, 0.0)


def _rss_mb() -> float:
    return _mem_mb()[0]


def _window(n: int, overlap: int) -> np.ndarray:
    w = np.ones(n, dtype=np.float32)
    fade = np.linspace(0.0, 1.0, overlap, dtype=np.float32)
    w[:overlap] = fade
    w[-overlap:] = fade[::-1]
    return w


def _load_audio(path: str, tmp: Path) -> tuple[Path, int]:
    """解码成 44.1k 立体声 f32（模型要求 44.1k）。"""
    from ..io import ffmpeg

    raw = tmp / "input.f32"
    ffmpeg.decode_to_f32(path, raw, SAMPLE_RATE)
    frames = raw.stat().st_size // 8
    return raw, frames


def run_worker(job_path: str, paths) -> int:
    from ..sep import models as sep_models

    _force_utf8_stdio()
    t_start = time.time()
    try:
        job = json.loads(Path(job_path).read_text(encoding="utf-8-sig"))
    except Exception as e:
        emit({"event": "error", "code": "bad_job", "message": str(e)})
        return 2

    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    threads = int(job.get("threads") or 8)
    model_id = str(job.get("model_id") or sep_models.DEFAULT_MODEL)
    spec = sep_models.model_spec(model_id)
    model_stems = list(spec["stems"])
    want = [s for s in (job.get("stems") or model_stems) if s in model_stems] or model_stems
    model_path = Path(job.get("model") or sep_models.resolve(model_id))

    # 运行时缺失要给人话，而不是把 ModuleNotFoundError 弹在对话框里
    try:
        import onnxruntime as ort  # noqa: F401
    except Exception as e:
        emit({"event": "error", "code": "runtime_missing",
              "message": f"Separation runtime (onnxruntime) is missing from this build: {e}"})
        return 3

    try:
        if not model_path.exists():
            emit({"event": "error", "code": "model_missing",
                  "message": f"model not found: {model_path}"})
            return 3

        emit({"event": "start", "model": model_path.name, "model_id": spec["id"],
              "model_mb": round(model_path.stat().st_size / 1048576, 1),
              "threads": threads, "stems": want, "model_stems": model_stems})

        t0 = time.time()
        raw, total = _load_audio(job["input"], out_dir)
        emit({"event": "stage", "stage": "decoded", "frames": total,
              "seconds": round(total / SAMPLE_RATE, 2), "t": round(time.time() - t0, 2)})

        import onnxruntime as ort

        so = ort.SessionOptions()
        so.enable_cpu_mem_arena = False          # M0 实测：默认 arena 峰值 4.3GB
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        so.intra_op_num_threads = max(1, min(16, threads))
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        t0 = time.time()
        sess = ort.InferenceSession(str(model_path), sess_options=so,
                                    providers=["CPUExecutionProvider"])
        emit({"event": "stage", "stage": "model_loaded", "t": round(time.time() - t0, 2)})

        mix = np.memmap(raw, dtype=np.float32, mode="r", shape=(total, 2))
        overlap = SEGMENT // 4
        stride = SEGMENT - overlap
        n_chunks = max(1, (total + stride - 1) // stride)
        window = _window(SEGMENT, overlap)

        # 逐 stem 落盘累加，避免内存里再叠多路大数组
        acc = {s: np.memmap(out_dir / f"{s}.acc.f32", dtype=np.float32, mode="w+",
                            shape=(total, 2)) for s in model_stems}
        weight = np.zeros(total, dtype=np.float32)

        t1 = time.time()
        for i in range(n_chunks):
            start = i * stride
            end = min(start + SEGMENT, total)
            chunk = np.asarray(mix[start:end])
            if chunk.shape[0] < SEGMENT:
                pad = np.zeros((SEGMENT - chunk.shape[0], 2), dtype=np.float32)
                chunk = np.concatenate([chunk, pad], axis=0)
            x = chunk.T[np.newaxis, ...]          # (1, 2, SEGMENT) 模型要的是 NCHW
            stems = sess.run(["stems"], {"mix": x})[0][0]     # (n_stems, 2, SEGMENT)
            clen = end - start
            w = window[:clen]
            for k, s in enumerate(model_stems):
                if k < stems.shape[0]:
                    acc[s][start:end] += (stems[k][:, :clen] * w).T
            weight[start:end] += w

            done = i + 1
            now = time.time()
            avg = (now - t1) / done
            eta = avg * (n_chunks - done)
            cur, peak = _mem_mb()
            emit({"event": "progress", "chunk": done, "chunks": n_chunks,
                  "percent": round(done / n_chunks, 4), "eta_s": round(eta, 1),
                  "chunk_s": round(avg, 2),
                  "rss_mb": round(cur, 1), "peak_rss_mb": round(peak, 1)})
            if cur > MEM_LIMIT_MB:
                emit({"event": "error", "code": "out_of_memory",
                      "message": f"working set > {MEM_LIMIT_MB}MB"})
                return 4

        wsum = np.maximum(weight, 1e-8)
        from ..io import ffmpeg

        stems_out = []
        for s in model_stems:
            if s not in want:
                continue
            # 拷一份再写：memmap 还开着的时候 Windows 不允许删除文件
            data = np.array(acc[s], copy=True)
            data /= wsum[:, None]
            f32 = out_dir / f"{s}.f32"
            np.ascontiguousarray(data, dtype=np.float32).tofile(f32)
            wav = out_dir / f"{s}.wav"
            # 24-bit PCM 在 ±1.0 处会硬削波；真峰值超过 0dBFS 的 stem 改用 32-bit float 保住动态
            peak = float(np.abs(data).max()) if data.size else 0.0
            codec = "pcm_s24le" if peak <= 0.999 else "pcm_f32le"
            ffmpeg.encode_f32(f32, wav, sample_rate=SAMPLE_RATE,
                              extra_args=["-c:a", codec])
            rms = float(np.sqrt(np.mean(data ** 2))) if data.size else 0.0
            stems_out.append({"name": s, "path": str(wav), "rms": round(rms, 5),
                              "peak": round(peak, 4), "codec": codec})
            emit({"event": "stem", "name": s, "path": str(wav), "rms": round(rms, 5),
                  "peak": round(peak, 4), "codec": codec})
            if peak > 0.999:
                emit({"event": "warning", "code": "stem_over_0dbfs",
                      "message": f"{s} peak {peak:.3f} > 0 dBFS, written as 32-bit float"})
            try:
                f32.unlink()
            except OSError:
                pass
            del data

        # 关掉所有 memmap 再删临时文件，否则 Windows 上删不掉
        _ = gc.collect()
        try:
            mix._mmap.close()
        except Exception:
            pass
        for s in model_stems:
            try:
                if hasattr(acc[s], "_mmap"):
                    acc[s]._mmap.close()
            except Exception:
                pass
        acc.clear()
        del mix
        gc.collect()
        for suffix in [f"{s}.acc.f32" for s in model_stems] + ["input.f32"]:
            try:
                (out_dir / suffix).unlink(missing_ok=True)
            except OSError:
                pass

        elapsed = time.time() - t_start
        emit({"event": "done", "dir": str(out_dir), "stems": stems_out,
              "seconds": round(elapsed, 1),
              "rtf": round(elapsed / max(1e-6, total / SAMPLE_RATE), 3)})
        return 0
    except Exception as e:
        log.exception("sep worker failed")
        emit({"event": "error", "code": type(e).__name__, "message": str(e)})
        return 5
