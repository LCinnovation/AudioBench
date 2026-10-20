"""波形峰值 mipmap（§4.4 / §10.2 借用 Audacity 的分级峰值思路）。

文件布局：`<key>.abpk` = np.savez 容器
    meta : JSON 字符串（版本/采样率/声道/帧数/每层 bin）
    L0..Ln : int16 数组，形状 (2, ch, count)，[0]=min [1]=max

第 0 层每 bin 256 个采样，逐层 ×2。5 分钟立体声约 0.8MB。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("ab.peakcache")

BASE_BIN = 256
MAX_LEVELS = 12
Q = 32767.0


@dataclass
class Level:
    index: int
    bin: int
    count: int
    data: np.ndarray  # (2, ch, count) int16


class PeakCache:
    def __init__(self, levels: list[Level], meta: dict):
        self.levels = levels
        self.meta = meta

    @property
    def frames(self) -> int:
        return int(self.meta.get("frames", 0))

    # ---------------------------------------------------------------- 读取
    @classmethod
    def open(cls, path: str | Path, *, load_data: bool = True) -> "PeakCache | None":
        path = Path(path)
        if not path.exists():
            return None
        try:
            z = np.load(path, allow_pickle=False)
            meta = json.loads(str(z["meta"]))
            levels: list[Level] = []
            for i, desc in enumerate(meta["levels"]):
                arr = z[f"L{i}"] if load_data else None
                levels.append(Level(index=i, bin=int(desc["bin"]), count=int(desc["count"]), data=arr))
            return cls(levels, meta)
        except Exception as e:  # 缓存坏了就当没有，重新生成
            log.warning("peak cache unreadable (%s): %s", path, e)
            return None

    def columns(self, start: int, end: int, width: int) -> tuple[np.ndarray, np.ndarray]:
        """返回 (mins, maxs)，形状 (ch, width)，范围 [-1, 1]。"""
        ch = int(self.meta.get("ch", 2))
        width = max(1, int(width))
        start = max(0, int(start))
        end = max(start + 1, int(end))
        mins = np.zeros((ch, width), dtype=np.float32)
        maxs = np.zeros((ch, width), dtype=np.float32)
        if not self.levels or self.frames <= 0:
            return mins, maxs

        samples_per_px = (end - start) / float(width)
        level = self.levels[0]
        for lv in self.levels:
            if lv.bin <= samples_per_px:
                level = lv
            else:
                break

        if level.data is None or level.count <= 0:
            return mins, maxs

        starts = (start + np.arange(width) * samples_per_px).astype(np.int64)
        ends = (start + (np.arange(width) + 1) * samples_per_px).astype(np.int64)
        b0 = np.clip(starts // level.bin, 0, level.count - 1)
        b1 = np.clip(ends // level.bin, 0, level.count - 1)
        spans = np.maximum(b1 - b0, 0)

        data = level.data  # (2, ch, count) int16
        if np.all(spans == 0):
            mins = data[0][:, b0].astype(np.float32) / Q
            maxs = data[1][:, b0].astype(np.float32) / Q
            return mins, maxs

        # 每列覆盖多个 bin：用 reduceat 一次算完（不要 per-column Python 循环，
        # 否则 1200 列 × N 个片段会把绘制拖到几十毫秒，进而饿死音频线程）
        starts = np.unique(np.concatenate([b0, [level.count]]))
        # reduceat 的下标必须 < 数组长度，所以尾部补一列当哨兵
        lo_pad = np.concatenate([data[0], data[0][:, -1:]], axis=1)
        hi_pad = np.concatenate([data[1], data[1][:, -1:]], axis=1)
        mi = np.minimum.reduceat(lo_pad, starts, axis=1)
        ma = np.maximum.reduceat(hi_pad, starts, axis=1)
        idx = np.clip(np.searchsorted(starts, b0), 0, max(0, mi.shape[1] - 1))
        mins = mi[:, idx].astype(np.float32) / Q
        maxs = ma[:, idx].astype(np.float32) / Q
        return mins, maxs

    def overview(self, width: int) -> tuple[np.ndarray, np.ndarray]:
        return self.columns(0, self.frames, width)


# -------------------------------------------------------------------- 构建

def _quantize(x: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(x * Q), -32768, 32767).astype(np.int16)


def build(f32_path: str | Path, frames: int, out_path: str | Path, *,
          sample_rate: int, channels: int = 2, progress=None) -> Path:
    mm = np.memmap(Path(f32_path), dtype=np.float32, mode="r", shape=(frames, channels))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chunk = 1 << 20  # 1M 采样
    count0 = (frames + BASE_BIN - 1) // BASE_BIN
    l0 = np.zeros((2, channels, count0), dtype=np.int16)

    done = 0
    while done < frames:
        n = min(chunk, frames - done)
        block = np.asarray(mm[done:done + n])
        pad = (-n) % BASE_BIN
        if pad:
            block = np.concatenate([block, np.zeros((pad, channels), dtype=np.float32)], axis=0)
        binned = block.reshape(-1, BASE_BIN, channels)
        mins = _quantize(binned.min(axis=1).T)   # (ch, bins)
        maxs = _quantize(binned.max(axis=1).T)
        b0 = done // BASE_BIN
        b1 = b0 + mins.shape[1]
        l0[0, :, b0:b1] = mins
        l0[1, :, b0:b1] = maxs
        done += n
        if progress:
            progress(min(0.99, done / max(1, frames)))

    levels = [l0]
    cur = l0
    while cur.shape[-1] > 1 and len(levels) < MAX_LEVELS:
        c = cur.shape[-1]
        half = (c + 1) // 2
        if c % 2:
            pad = np.repeat(cur[:, :, -1:], 1, axis=2)
            cur2 = np.concatenate([cur, pad], axis=2)
        else:
            cur2 = cur
        mins = _pair_reduce(cur2[0], half, np.minimum)
        maxs = _pair_reduce(cur2[1], half, np.maximum)
        nxt = np.stack([mins, maxs]).astype(np.int16)
        levels.append(nxt)
        cur = nxt

    meta = {
        "version": 1, "sampleRate": int(sample_rate), "ch": int(channels), "frames": int(frames),
        "levels": [{"bin": BASE_BIN << i, "count": int(lv.shape[-1])} for i, lv in enumerate(levels)],
    }
    arrays = {"meta": np.array(json.dumps(meta))}
    for i, lv in enumerate(levels):
        arrays[f"L{i}"] = lv
    # 用文件对象写入：np.savez 会给非 .npz 结尾的路径自动追加 .npz
    with open(out_path, "wb") as f:
        np.savez(f, **arrays)
    if progress:
        progress(1.0)
    return out_path


def _pair_reduce(a: np.ndarray, half: int, fn) -> np.ndarray:
    """(ch, n) → (ch, half)，两两归约（fn 是 np.minimum / np.maximum）。"""
    ch, n = a.shape
    if n % 2:
        a = np.concatenate([a, a[:, -1:]], axis=1)
        n += 1
    return fn.reduce(a.reshape(ch, n // 2, 2), axis=2)
