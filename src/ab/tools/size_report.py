"""体积报告：产物里谁最占地方（M1-8 的验收材料）。"""
from __future__ import annotations

import sys
from pathlib import Path


def report(path: str | Path) -> None:
    root = Path(path)
    if not root.exists():
        print(f"not found: {root}")
        return
    files = [f for f in root.rglob("*") if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    print(f"{root.name}: {len(files)} files, {total / 1048576:.1f} MB")
    buckets: dict[str, int] = {}
    for f in files:
        rel = f.relative_to(root)
        key = "ffmpeg" if rel.parts[0] == "tools" or "ffmpeg" in str(rel).lower() else \
            ("Qt/PySide6" if rel.parts[0] == "_internal" and any(
                k in f.name for k in ("Qt6", "PySide6", "shiboken")) else
             ("Python" if rel.parts[0] == "_internal" and f.suffix in (".pyd", ".dll") else
              ("assets" if "assets" in rel.parts else "other")))
        buckets[key] = buckets.get(key, 0) + f.stat().st_size
    for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]):
        print(f"  {k:12s} {v / 1048576:8.1f} MB")
    biggest = sorted(files, key=lambda f: -f.stat().st_size)[:10]
    print("  top files:")
    for f in biggest:
        print(f"    {f.stat().st_size / 1048576:7.1f} MB  {f.relative_to(root)}")


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else "dist/AudioBench")
