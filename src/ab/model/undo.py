"""撤销栈：快照式（M1 求正确；M2 再换字段级 diff）。

容量 200（任务书要求 ≥30）。同类操作在 800ms 内可合并（拖推子不会刷爆栈）。
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable


class UndoStack:
    def __init__(self, snapshot: Callable[[], dict], restore: Callable[[dict], None],
                 capacity: int = 200, coalesce_ms: int = 800):
        self._snapshot = snapshot
        self._restore = restore
        self._capacity = capacity
        self._coalesce_ms = coalesce_ms
        self._undo: deque[tuple[str, dict, dict]] = deque(maxlen=capacity)
        self._redo: list[tuple[str, dict, dict]] = []
        self._pending: tuple[str, dict] | None = None
        self._last_commit: tuple[str, float] | None = None

    # ---------------------------------------------------------------- 事务
    def begin(self, label: str) -> None:
        self._pending = (label, self._snapshot())

    def commit(self) -> bool:
        if self._pending is None:
            return False
        label, before = self._pending
        self._pending = None
        after = self._snapshot()
        if after == before:
            return False
        now = time.monotonic()
        if (self._undo and self._last_commit
                and self._last_commit[0] == label
                and (now - self._last_commit[1]) * 1000 <= self._coalesce_ms):
            # 合并：保留最早的 before，替换 after
            prev_label, prev_before, _ = self._undo[-1]
            self._undo[-1] = (prev_label, prev_before, after)
        else:
            self._undo.append((label, before, after))
        self._last_commit = (label, now)
        self._redo.clear()
        return True

    def abort(self) -> None:
        self._pending = None

    # ---------------------------------------------------------------- 操作
    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def undo(self) -> str:
        if not self._undo:
            return ""
        label, before, after = self._undo.pop()
        self._restore(before)
        self._redo.append((label, before, after))
        # 时间线在这里断开了：后续同标签的提交绝不能再合并进更早的那条记录，
        # 否则一次撤销会跨过两条历史（实测：撤销后拖推子 → Ctrl+Z 跳步）。
        self._last_commit = None
        return label

    def redo(self) -> str:
        if not self._redo:
            return ""
        label, before, after = self._redo.pop()
        self._restore(after)
        self._undo.append((label, before, after))
        self._last_commit = None
        return label

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._pending = None
        self._last_commit = None

    def depth(self) -> tuple[int, int]:
        return len(self._undo), len(self._redo)
