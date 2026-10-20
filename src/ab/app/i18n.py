"""中英双语（§4.6）：跟随系统、可手动切换、不重启。

约定：
  * 源码里的字符串统一用英文（作为 source language），中文走 `assets/i18n/ab_zh_CN.qm`；
  * 所有用户可见文案必须 `self.tr(...)` 或 `ab.app.i18n.tr(...)`，禁止裸字面量（`tools/i18n_lint.py` 检查）；
  * 切换语言 = 换 QTranslator + 广播，各控件在 `retranslate()` 里重设文案。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLocale, QObject, QTranslator, Signal

from .paths import asset_dir

log = logging.getLogger("ab.i18n")

LANG_AUTO = "auto"
LANG_ZH = "zh_CN"
LANG_EN = "en_US"
SUPPORTED = (LANG_ZH, LANG_EN)


def tr(context: str, text: str, *args) -> str:
    """非 QObject 场景的翻译入口。"""
    s = QCoreApplication.translate(context, text)
    return s.format(*args) if args else s


def resolve_language(pref: str) -> str:
    if pref in SUPPORTED:
        return pref
    name = QLocale.system().name()  # 形如 zh_CN / en_US
    return LANG_ZH if name.lower().startswith("zh") else LANG_EN


def qm_path(lang: str) -> Path:
    return asset_dir() / "i18n" / f"ab_{lang}.qm"


class I18n(QObject):
    languageChanged = Signal(str)

    def __init__(self, pref: str = LANG_AUTO, parent: QObject | None = None):
        super().__init__(parent)
        self._pref = pref
        self._lang = resolve_language(pref)
        self._translator = QTranslator(self)

    # ---------------------------------------------------------------- state
    @property
    def preference(self) -> str:
        return self._pref

    @property
    def language(self) -> str:
        return self._lang

    @property
    def is_chinese(self) -> bool:
        return self._lang == LANG_ZH

    # ---------------------------------------------------------------- apply
    def install(self) -> bool:
        """把当前语言装到 QApplication 上（启动时与切换时都走这里）。"""
        app = QCoreApplication.instance()
        if app is None:
            return False
        app.removeTranslator(self._translator)
        if self._lang == LANG_EN:
            return True  # 英文即 source language，无需 .qm
        path = qm_path(self._lang)
        if not path.exists():
            log.warning("translation missing: %s (fallback to english)", path)
            return False
        if not self._translator.load(str(path)):
            log.warning("failed to load %s", path)
            return False
        app.installTranslator(self._translator)
        return True

    def set_preference(self, pref: str, *, persist: bool = True) -> bool:
        """切换语言；返回是否真的换了（调用方据此决定是否广播 retranslate）。"""
        pref = pref if pref in (LANG_AUTO, *SUPPORTED) else LANG_AUTO
        new_lang = resolve_language(pref)
        changed = new_lang != self._lang
        self._pref = pref
        self._lang = new_lang
        if changed:
            self.install()
            self.languageChanged.emit(new_lang)
        return changed

    def describe(self) -> dict:
        return {"preference": self._pref, "language": self._lang,
                "qm": str(qm_path(self._lang)), "qm_exists": qm_path(self._lang).exists()}
