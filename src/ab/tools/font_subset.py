"""字体子集：Noto Sans SC → AB Sans SC（OFL 要求改名）。

为什么自带字体：Wine 瓶里经常没有中文字体，界面会出方块（§4.6 / §9 R2）。

子集范围：ASCII + 常用标点 + GB2312 一级汉字（3755 字，覆盖「常用 3500」）。
用法：
    python -m ab.tools.font_subset <源字体.otf> <输出.ttf>
"""
from __future__ import annotations

import sys
from pathlib import Path

FAMILY = "AB Sans SC"
ASCII = "".join(chr(c) for c in range(0x20, 0x7F))
PUNCT = (
    "，。、；：？！“”‘’（）《》〈〉【】〔〕—…·～￥％＋－×÷＝"
    "　・…‥※→←↑↓■□●○◆◇★☆①②③④⑤⑥⑦⑧⑨⑩"
)
# 界面里实际用到的符号（曾经漏掉，导致中文界面出现方块）：⚠ ↔ ≈ ≥ − ∈ ├ ▶ ± 「」
SYMBOLS = "⚠↔≈≥−∈├▶▲▼△▽◆±「」『』·•✓"

ROOT = Path(__file__).resolve().parents[3]


def used_chars() -> str:
    """扫描随包发布的文本，收集真正用到的字符（源码字面量 + 中文翻译 + 教程 HTML）。"""
    chars: list[str] = []
    globs = [ROOT / "src", ROOT / "assets"]
    exts = ("*.py", "*.ts", "*.html", "*.txt")
    for base in globs:
        for ext in exts:
            for p in base.rglob(ext):
                try:
                    chars.append(p.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    continue
    text = "".join(chars)
    keep = []
    for ch in text:
        o = ord(ch)
        if o < 0x20 or o == 0x7F:
            continue
        keep.append(ch)
    return "".join(keep)


def gb2312_level1() -> str:
    chars: list[str] = []
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                ch = bytes([hi, lo]).decode("gb2312")
            except UnicodeDecodeError:
                continue
            chars.append(ch)
    return "".join(chars)


def subset(src: Path, dst: Path) -> None:
    from fontTools import subset as ft_subset
    from fontTools.ttLib import TTFont

    text = ASCII + PUNCT + SYMBOLS + gb2312_level1() + used_chars()
    text = "".join(dict.fromkeys(text))          # 去重
    dst.parent.mkdir(parents=True, exist_ok=True)

    opts = ft_subset.Options()
    opts.name_IDs = ["*"]
    opts.name_legacy = True
    opts.name_languages = ["*"]
    opts.layout_features = ["*"]
    opts.notdef_outline = True
    opts.recommended_glyphs = True
    opts.drop_tables += ["DSIG"]

    font = ft_subset.load_font(str(src), opts)
    subsetter = ft_subset.Subsetter(options=opts)
    subsetter.populate(text=text)
    subsetter.subset(font)
    font.save(str(dst))
    font.close()

    # OFL：改名（Modified Version 不得沿用保留字体名）
    tt = TTFont(str(dst))
    name = tt["name"]
    for rec in name.names:
        try:
            val = rec.toUnicode()
        except Exception:
            continue
        if rec.nameID == 1:
            rec.string = FAMILY
        elif rec.nameID == 2:
            rec.string = "Regular"
        elif rec.nameID == 4:
            rec.string = f"{FAMILY} Regular"
        elif rec.nameID == 6:
            rec.string = "ABSansSC-Regular"
        elif rec.nameID == 16:
            rec.string = FAMILY
        elif rec.nameID == 17:
            rec.string = "Regular"
        elif val.startswith("Noto Sans"):
            rec.string = val.replace("Noto Sans CJK SC", FAMILY).replace("Noto Sans SC", FAMILY)
    tt.save(str(dst))
    tt.close()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    src, dst = Path(argv[0]), Path(argv[1])
    if not src.exists():
        print(f"missing source font: {src}")
        return 2
    subset(src, dst)
    kb = dst.stat().st_size / 1024
    print(f"subset written: {dst} ({kb:.0f} KB, family={FAMILY})")
    print(f"glyph text length: {len(ASCII + PUNCT + gb2312_level1())}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
