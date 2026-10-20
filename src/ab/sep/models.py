"""分离模型清单。

两套模型（都是 Demucs v4 / MIT）：

  4 轨  htdemucs          drums / bass / other / vocals        329 MB  重建残差 ~1.8%
  6 轨  htdemucs_6s       + guitar / piano                     272 MB  重建残差 ~18%

实测（同一段 40s 真实歌曲）：4 轨模型「四轨相加 ≈ 原曲」，6 轨模型会丢掉约 18% 的内容
（6 轨变体是 Demucs 家族里最弱的一支）。所以默认用 4 轨，需要吉他/钢琴再切 6 轨。
"""

from __future__ import annotations

import os
from pathlib import Path

from ..app.paths import app_dir, asset_dir

# ---- 4 轨（默认，质量最好）------------------------------------------------
MODEL_4S = "htdemucs_4s.opt.onnx"
STEMS_4 = ("drums", "bass", "other", "vocals")

# ---- 6 轨（含吉他/钢琴）---------------------------------------------------
MODEL_6S = "htdemucs_6s.opt.onnx"
STEMS_6 = ("drums", "bass", "other", "vocals", "guitar", "piano")

DEFAULT_MODEL = "4s"

MODELS: dict[str, dict] = {
    "4s": {
        "id": "4s",
        "file": MODEL_4S,
        "label": "4 stems · high quality (htdemucs)",
        "label_zh": "四轨 · 高音质（htdemucs）",
        "stems": list(STEMS_4),
        "size_mb": 329,
    },
    "6s": {
        "id": "6s",
        "file": MODEL_6S,
        "label": "6 stems · incl. guitar/piano (htdemucs_6s)",
        "label_zh": "六轨 · 含吉他/钢琴（htdemucs_6s）",
        "stems": list(STEMS_6),
        "size_mb": 272,
    },
}

DISPLAY = {
    "drums": "Drums",
    "bass": "Bass",
    "other": "Other (incl. strings)",
    "vocals": "Vocals",
    "guitar": "Guitar",
    "piano": "Piano",
}

# 兼容旧代码：默认模型的名字/词表
MODEL_NAME = MODEL_4S
STEMS = STEMS_4


def model_spec(model_id: str | None = None) -> dict:
    return MODELS.get(str(model_id or DEFAULT_MODEL), MODELS[DEFAULT_MODEL])


def stems_for(model_id: str | None = None) -> list[str]:
    return list(model_spec(model_id)["stems"])


def resolve(model_id: str | None = None) -> Path:
    """模型文件路径（优先 assets/models，其次安装目录，再其次 AB_MODEL_DIR）。"""
    name = model_spec(model_id)["file"]
    for cand in (asset_dir() / "models" / name,
                 app_dir() / "assets" / "models" / name,
                 (Path(os.environ["AB_MODEL_DIR"]) / name)
                 if os.environ.get("AB_MODEL_DIR") else None):
        if cand and Path(cand).exists():
            return Path(cand)
    return asset_dir() / "models" / name


def model_path(model_id: str | None = None) -> Path:
    return resolve(model_id)


def runtime_info() -> dict:
    """推理运行时是否可用（冻结版漏打包时这里会如实说 False）。"""
    try:
        import onnxruntime as ort

        return {"available": True, "version": getattr(ort, "__version__", "?")}
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}"}


def describe(model_id: str | None = None, load: bool = False) -> dict:
    spec = model_spec(model_id)
    p = resolve(model_id)
    info = {"id": spec["id"], "path": str(p), "present": p.exists(),
            "stems": list(spec["stems"]), "loaded": False, "providers": [],
            "runtime": runtime_info(), "label": spec["label"],
            "size_mb": round(p.stat().st_size / 1048576, 1) if p.exists() else 0}
    if load and p.exists():
        try:
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.enable_cpu_mem_arena = False
            so.intra_op_num_threads = 8
            s = ort.InferenceSession(str(p), sess_options=so, providers=["CPUExecutionProvider"])
            info["loaded"] = True
            info["providers"] = s.get_providers()
            info["input"] = [i.name for i in s.get_inputs()]
            info["output"] = [o.name for o in s.get_outputs()]
        except Exception as e:
            info["error"] = str(e)
    return info


def describe_all(load: bool = False) -> dict[str, dict]:
    return {mid: describe(mid, load=load) for mid in MODELS}
