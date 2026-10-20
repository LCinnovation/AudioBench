"""`--selftest`：Wine 下排查的唯一入口（任务书 §6）。

输出固定 schema 的 JSON；`--deep` 才会加载模型、编码一次 mp3（那两项慢）。
"""
from __future__ import annotations

import json
import locale
import logging
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

from ..version import BUILD_DATE, VERSION
from .paths import AppPaths, asset_dir, app_dir

log = logging.getLogger("ab.selftest")


def _dep_versions() -> dict:
    out = {"python": sys.version.split()[0]}
    for name, mod in (("numpy", "numpy"), ("pyside6", "PySide6"), ("pycryptodome", "Crypto"),
                      ("onnxruntime", "onnxruntime"), ("soxr", "soxr"),
                      ("sounddevice", "sounddevice")):
        try:
            m = __import__(mod)
            out[name] = getattr(m, "__version__", "?")
        except Exception:
            out[name] = None
    return out


def _wine_info() -> dict:
    if sys.platform != "win32":
        return {"is_wine": False, "wine_version": None}
    try:
        import ctypes

        ntdll = ctypes.WinDLL("ntdll")
        fn = getattr(ntdll, "wine_get_version", None)
        if fn is None:
            return {"is_wine": False, "wine_version": None}
        fn.restype = ctypes.c_char_p
        ver = fn().decode("ascii", "replace")
        return {"is_wine": True, "wine_version": ver}
    except Exception:
        return {"is_wine": False, "wine_version": None}


def _check_fonts() -> dict:
    info = {"bundled": [], "loaded": False, "fallback": True, "families": 0}
    try:
        from PySide6.QtGui import QFontDatabase

        fonts_dir = asset_dir() / "fonts"
        if fonts_dir.exists():
            for f in sorted(fonts_dir.glob("*.ttf")) + sorted(fonts_dir.glob("*.otf")):
                fid = QFontDatabase.addApplicationFont(str(f))
                if fid >= 0:
                    info["bundled"].append(f.name)
        fams = QFontDatabase.families()
        info["families"] = len(fams)
        info["loaded"] = "AB Sans SC" in fams
        info["fallback"] = not info["loaded"]
    except Exception as e:
        info["error"] = str(e)
    return info


def _check_audio(state=None) -> dict:
    from ..audio import backend as backend_mod

    out: dict = {"backend": "none", "device": "", "devices": [], "sample_rate": 0,
                 "period_frames": 0, "latency_ms": 0.0, "xruns": 0}
    try:
        be = backend_mod.create_backend("auto")
        out["candidate"] = be.name
        try:
            be.open(sample_rate=48000, block_frames=256, device=None)
            info = be.info()
            out.update(info.to_dict())
            out["period_frames"] = int(out.get("block_frames", 0))
            out["devices"] = [d.to_dict() for d in backend_mod.list_devices(be)]
            be.close()
            out["opened"] = True
        except Exception as e:
            out["opened"] = False
            out["open_error"] = str(e)
            try:
                out["devices"] = [d.to_dict() for d in backend_mod.list_devices(None)]
            except Exception:
                pass
    except Exception as e:
        out["error"] = str(e)
    return out


def _check_cache(paths: AppPaths) -> dict:
    ok = True
    detail = ""
    try:
        d = Path(paths.cache_dir) / "decoded"
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".probe-{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except Exception as e:
        ok = False
        detail = str(e)
    return {"ok": ok, "detail": detail}


def _check_ffmpeg(deep: bool = False) -> dict:
    from ..io import ffmpeg as ff

    info = ff.version_info()
    info["probe"] = ff.ffprobe_path() is not None
    if deep and info.get("path"):
        try:
            tmp = Path(tempfile.gettempdir()) / f"ab_selftest_{os.getpid()}.wav"
            import numpy as np
            import wave

            with wave.open(str(tmp), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                t = np.arange(4410) / 44100.0
                data = (0.2 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
                w.writeframes(np.stack([data, data], axis=1).tobytes())
            out = Path(tempfile.gettempdir()) / f"ab_selftest_{os.getpid()}.mp3"
            t0 = time.perf_counter()
            import subprocess

            ff.CREATE_NO_WINDOW
            rc = subprocess.run([info["path"], "-y", "-v", "error", "-i", str(tmp),
                                 "-c:a", "libmp3lame", "-b:a", "128k", str(out)],
                                capture_output=True, creationflags=ff.CREATE_NO_WINDOW).returncode
            info["mp3_encode_ok"] = (rc == 0 and out.exists() and out.stat().st_size > 0)
            info["mp3_encode_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            for p in (tmp, out):
                try:
                    p.unlink()
                except OSError:
                    pass
        except Exception as e:
            info["mp3_encode_ok"] = False
            info["mp3_encode_error"] = str(e)
    return info


def _check_stretch() -> dict:
    from ..audio.stretch import StretchEngine, find_dll

    path = find_dll()
    info = {"dll": str(path) if path else None, "available": False}
    try:
        eng = StretchEngine(path)
        info["available"] = eng.available
        if eng.available:
            h = eng.create(2, 44100, 1024)
            if h:
                info["latency"] = eng.latency(h)
                eng.destroy(h)
    except Exception as e:
        info["error"] = str(e)
    return info


def _check_sep(deep: bool = False) -> dict:
    from ..sep import models as sep_models

    try:
        info = sep_models.describe(load=deep)
        return {"present": info["present"], "size_mb": info.get("size_mb"),
                "path": info["path"], "runtime": info["runtime"],
                "loaded": info["loaded"], "providers": info["providers"],
                "default_model": sep_models.DEFAULT_MODEL,
                "models": {mid: {"present": d["present"], "size_mb": d["size_mb"],
                                 "stems": d["stems"]}
                           for mid, d in sep_models.describe_all(load=False).items()}}
    except Exception as e:
        return {"present": False, "error": f"{type(e).__name__}: {e}"}


def _check_ncm(deep: bool = False) -> dict:
    """同名输出避让是数据丢失级缺陷，值得在自检里验一遍（含打包产物）。

    不依赖 ffmpeg/ncm 素材：只验证 `_unique_out` 的命名规则。
    """
    import tempfile

    from ..ncm import convert as ncm_convert

    info: dict = {"unique_out": [], "ok": False}
    try:
        with tempfile.TemporaryDirectory(prefix="ab_selftest_") as td:
            d = Path(td)
            names = []
            for _ in range(3):
                p = ncm_convert._unique_out(d, "song", "flac")
                p.write_bytes(b"x")
                names.append(p.name)
            info["unique_out"] = names
            info["ok"] = names == ["song.flac", "song (2).flac", "song (3).flac"]
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
    if deep:
        info["ffmpeg"] = bool(_check_ffmpeg(deep=False).get("path"))
    return info


def collect(state=None, *, deep: bool = False, paths: AppPaths | None = None) -> dict:
    from ..app.i18n import resolve_language

    if paths is None and state is not None:
        paths = state.paths
    data: dict = {
        "schema": 1,
        "app": {
            "version": VERSION,
            "build": BUILD_DATE,
            "mode": paths.mode if paths else "unknown",
            "frozen": bool(getattr(sys, "frozen", False)),
            "argv": sys.argv[1:],
        },
        "os": {
            "name": platform.platform(),
            "build": platform.version(),
            "arch": platform.machine(),
            "locale": locale.getdefaultlocale()[0] if hasattr(locale, "getdefaultlocale") else "",
            "language": resolve_language("auto"),
            **_wine_info(),
        },
        "paths": paths.describe() if paths else {},
        "fonts": _check_fonts(),
        "ffmpeg": _check_ffmpeg(deep=deep),
        "stretch": _check_stretch(),
        "sep": _check_sep(deep=deep),
        "ncm": _check_ncm(deep=deep),
        "audio": _check_audio(state),
        "deps": _dep_versions(),
        "app_dir": str(app_dir()),
        "checks": [],
        "errors": [],
    }
    if paths is not None:
        data["checks"].append({"id": "cache_write", **_check_cache(paths)})
    if deep:
        data["model"] = _check_model(state)
        if state is not None:
            data["project"] = state.summary()
    for c in data["checks"]:
        if not c.get("ok"):
            data["errors"].append(f"{c['id']}: {c.get('detail', '')}")
    return data


def _check_model(state) -> dict:
    from ..sep import models as sep_models  # M3 才会真正用到

    return sep_models.describe(load=state is not None)


def run(out_path: str | None = None, *, deep: bool = False, paths: AppPaths | None = None,
        state=None) -> int:
    data = collect(state, deep=deep, paths=paths)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    print(text)
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
        print(f"\nwrote {out_path}")
    ok = not data["errors"]
    print("\nSELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1
