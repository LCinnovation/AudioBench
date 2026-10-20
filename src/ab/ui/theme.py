"""视觉 token（§4.1）：深色、无渐变/投影/模糊，全部颜色只从这里取。"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

# 基础色板
BG = "#1E1E22"
PANEL = "#26262B"
PANEL_ALT = "#2B2B31"
LINE = "#33333A"
LINE_SOFT = "#2A2A30"
TEXT = "#E6E6EA"
TEXT_DIM = "#9A9AA5"
TEXT_FAINT = "#6E6E78"
WAVE = "#4EA1B8"
PLAYHEAD = "#E5484D"
SELECT = "#F5A524"
OK = "#5FB878"
WARN = "#F5A524"
ERR = "#E5484D"

RADIUS = 4

# 浅色主题（P2-11 只要求「留接口」）：色板在这里备好，切换逻辑在下一版接上。
# 用法约定：所有控件都通过 theme.* 常量取色，接入时只需按主题选一套常量。
LIGHT = {
    "BG": "#F4F4F7",
    "PANEL": "#FFFFFF",
    "PANEL_ALT": "#ECECF1",
    "LINE": "#D7D7DE",
    "LINE_SOFT": "#E6E6EC",
    "TEXT": "#1E1E22",
    "TEXT_DIM": "#5A5A66",
    "TEXT_FAINT": "#8A8A96",
    "WAVE": "#2F7F97",
    "PLAYHEAD": "#D13438",
    "SELECT": "#C77700",
}


def qcolor(hex_str: str, alpha: int | None = None) -> QColor:
    c = QColor(hex_str)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def apply_palette(app) -> None:
    pal = QPalette()
    pal.setColor(QPalette.Window, qcolor(BG))
    pal.setColor(QPalette.WindowText, qcolor(TEXT))
    pal.setColor(QPalette.Base, qcolor(PANEL))
    pal.setColor(QPalette.AlternateBase, qcolor(PANEL_ALT))
    pal.setColor(QPalette.Text, qcolor(TEXT))
    pal.setColor(QPalette.Button, qcolor(PANEL))
    pal.setColor(QPalette.ButtonText, qcolor(TEXT))
    pal.setColor(QPalette.Highlight, qcolor(SELECT))
    pal.setColor(QPalette.HighlightedText, qcolor(BG))
    pal.setColor(QPalette.ToolTipBase, qcolor(PANEL_ALT))
    pal.setColor(QPalette.ToolTipText, qcolor(TEXT))
    pal.setColor(QPalette.Disabled, QPalette.Text, qcolor(TEXT_FAINT))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, qcolor(TEXT_FAINT))
    app.setPalette(pal)


def ui_font(size: int = 10, *, bold: bool = False) -> QFont:
    f = QFont(app_family(), size)
    f.setBold(bold)
    return f


def mono_font(size: int = 10) -> QFont:
    f = QFont("Consolas", size)
    f.setStyleHint(QFont.Monospace)
    return f


_family_cache: str | None = None


def app_family() -> str:
    """优先自带字体（Wine 下没有中文字体会出方块），否则回退系统字体。"""
    global _family_cache
    if _family_cache:
        return _family_cache
    families = set(QFontDatabase.families())
    for cand in ("AB Sans SC", "Segoe UI", "Noto Sans SC", "Microsoft YaHei UI", "DejaVu Sans"):
        if cand in families:
            _family_cache = cand
            return cand
    _family_cache = QFontDatabase.families()[0] if families else "Sans Serif"
    return _family_cache


STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; }}QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {PANEL_ALT}; color: {TEXT}; border: 1px solid {LINE}; padding: 3px; }}
QPushButton {{
    background: {PANEL}; border: 1px solid {LINE}; border-radius: {RADIUS}px;
    padding: 4px 10px; min-height: 20px;
}}
QPushButton:hover {{ background: {PANEL_ALT}; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:checked {{ background: {SELECT}; color: {BG}; border-color: {SELECT}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {LINE_SOFT}; }}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {PANEL}; border: 1px solid {LINE}; border-radius: {RADIUS}px; padding: 2px 6px;
}}
QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: {SELECT}; }}
QMenu {{ background: {PANEL}; border: 1px solid {LINE}; }}
QMenu::item:selected {{ background: {LINE}; }}
QScrollBar:horizontal, QScrollBar:vertical {{ background: {BG}; width: 12px; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal, QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 5px; min-width: 24px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; margin: -5px 0; border-radius: 6px; background: {TEXT_DIM}; }}
QSplitter::handle {{ background: {LINE_SOFT}; }}
QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; }}
"""
