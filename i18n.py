"""Локализация: LanguageManager с переключением «на лету» и авто-определением языка ОС."""
import json
import locale
import os
import sys

from .config import resource_path

LANG_DIR = "assets/lang"
FALLBACK = "en_US"
ORDER = ["uk_UA", "ru_RU", "en_US", "de_DE", "es_ES", "fr_FR", "pl_PL", "zh_CN"]
PREFIX_MAP = {"uk": "uk_UA", "ru": "ru_RU", "en": "en_US", "de": "de_DE", "es": "es_ES",
              "fr": "fr_FR", "pl": "pl_PL", "zh": "zh_CN"}


def detect_system_language() -> str:
    """Определяет язык ОС (Windows: через GetUserDefaultUILanguage)."""
    codes = []
    try:
        if sys.platform.startswith("win"):
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            codes.append(locale.windows_locale.get(lcid, ""))
        codes.append(locale.getlocale()[0] or "")
        codes.append(os.environ.get("LANG", ""))
    except Exception:
        pass
    for c in codes:
        prefix = (c or "").replace("-", "_").split("_")[0].lower()
        if prefix in PREFIX_MAP:
            return PREFIX_MAP[prefix]
    return FALLBACK


class LanguageManager:
    def __init__(self):
        self.code = FALLBACK
        self._tables = {}
        self._listeners = []
        self.base = self._load(FALLBACK)

    def _load(self, code: str) -> dict:
        if code not in self._tables:
            try:
                p = resource_path(f"{LANG_DIR}/{code}.json")
                self._tables[code] = json.loads(p.read_text("utf-8"))
            except Exception:
                self._tables[code] = {}
        return self._tables[code]

    def available(self) -> list:
        """[(code, native_name, flag)] в фиксированном порядке."""
        out = []
        for code in ORDER:
            t = self._load(code)
            if t:
                out.append((code, t.get("_name", code), t.get("_flag", "")))
        return out

    def set_language(self, code: str):
        if not self._load(code):
            code = FALLBACK
        self.code = code
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    def on_change(self, callback):
        self._listeners.append(callback)

    def tr(self, key: str, **kw) -> str:
        text = self._load(self.code).get(key) or self.base.get(key) or key
        if kw:
            try:
                return text.format(**kw)
            except (KeyError, IndexError, ValueError):
                return text
        return text

    @property
    def native_name(self) -> str:
        return self._load(self.code).get("_name", self.code)

    @property
    def english_name(self) -> str:
        return self._load(self.code).get("_english") or self._load(FALLBACK).get("_english", "English")

    @property
    def ai_instruction(self) -> str:
        return self.tr("ai_lang_instr")


lang = LanguageManager()


def tr(key: str, **kw) -> str:
    return lang.tr(key, **kw)


LANG = lang  # алиас: ai.py использует LANG.english_name
