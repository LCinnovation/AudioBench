"""解码后的 PCM 访问层：mmap 读裸 f32le（交错 2 声道）。

为什么用 mmap 而不是把整段读进内存：5 分钟立体声 44.1k 约 106MB，
8 轨就 850MB，会顶到内存门禁；mmap 让常驻内存只跟当前播放窗口有关。
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path

import numpy as np

log = logging.getLogger("ab.waveshim")


class AudioSource:
    """一段只读 PCM：数据按 (frames, 2) float32 解释。"""

    def __init__(self, f32_path: str | Path, frames: int, channels: int = 2):
        self.path = Path(f32_path)
        self.channels = channels
        self.frames = int(frames)
        self._mm: np.memmap | None = None

    def _map(self) -> np.memmap:
        if self._mm is None:
            self._mm = np.memmap(self.path, dtype=np.float32, mode="r",
                                 shape=(self.frames, self.channels))
        return self._mm

    def read(self, start: int, count: int) -> np.ndarray:
        """返回 (count, 2)；越界部分补零（片段被拖到源尾部之外时也不炸）。"""
        out = np.zeros((count, self.channels), dtype=np.float32)
        self.read_into(out, start, count)
        return out

    def read_into(self, out: np.ndarray, start: int, count: int) -> None:
        """就地填充（混音线程用，避免每块新建数组）。"""
        n = min(int(count), out.shape[0])
        out[:n] = 0.0
        start = max(0, int(start))
        if n <= 0 or start >= self.frames:
            return
        avail = min(n, self.frames - start)
        out[:avail] = self._map()[start:start + avail]

    def close(self) -> None:
        self._mm = None


class SourceRegistry:
    """最多同时打开 N 个 mmap，超出按 LRU 关闭（页面缓存仍留着，重开很便宜）。"""

    def __init__(self, capacity: int = 24):
        self.capacity = capacity
        self._items: OrderedDict[str, AudioSource] = OrderedDict()

    def get(self, path: str, frames: int) -> AudioSource:
        key = str(path)
        src = self._items.get(key)
        if src is not None:
            self._items.move_to_end(key)
            return src
        src = AudioSource(path, frames)
        self._items[key] = src
        while len(self._items) > self.capacity:
            _, old = self._items.popitem(last=False)
            old.close()
        return src

    def clear(self) -> None:
        for s in self._items.values():
            s.close()
        self._items.clear()
