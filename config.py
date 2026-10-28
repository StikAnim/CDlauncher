"""Пути, настройки и темы CD Launcher.

Все данные лаунчера лежат в одной папке `.cdlauncher`:
  profiles/<имя>/   - изолированная папка каждой сборки (моды, конфиги, миры, ресурспаки)
  versions/         - общие файлы версий Minecraft (json + jar)
  libraries/        - общие библиотеки
  assets/           - общие ассеты (звуки, языки)
  runtime/          - скачанные Java
"""
import json
import os
import platform
import sys
import threading
from pathlib import Path

APP_NAME = "CD Launcher"
APP_VERSION = "1.0.0"


def _base_dir() -> Path:
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", str(Path.home())))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))


DEFAULT_ROOT = _base_dir() / ".cdlauncher"
POINTER = _base_dir() / "cdlauncher_location.json"      # маленький файл «где лежат данные»; всегда в стандартном месте


def _resolve_root() -> Path:
    """Папка данных: выбранная пользователем (см. cdlauncher_location.json) либо стандартная %APPDATA%\\.cdlauncher."""
    try:
        p = Path(json.loads(POINTER.read_text("utf-8"))["root"])
        if p.exists() or p.parent.exists():
            return p
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return DEFAULT_ROOT


ROOT = _resolve_root()


def is_first_run() -> bool:
    """Первый запуск: нет ни указателя папки данных, ни старых настроек в стандартной папке."""
    return not POINTER.exists() and not (DEFAULT_ROOT / "settings.json").exists()


def write_pointer(path) -> None:
    POINTER.parent.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(json.dumps({"root": str(path)}, ensure_ascii=False), "utf-8")
PROFILES_DIR = ROOT / "profiles"
VERSIONS_DIR = ROOT / "versions"
LIBRARIES_DIR = ROOT / "libraries"
ASSETS_DIR = ROOT / "assets"
RUNTIME_DIR = ROOT / "runtime"
CACHE_DIR = ROOT / "cache"
ANIMATIONS_DIR = ROOT / "animations"
SERVERS_DIR = ROOT / "servers"
CONFIG_FILE = ROOT / "settings.json"
ACCOUNTS_FILE = ROOT / "accounts.json"
AUTHLIB_JAR = ROOT / "authlib-injector.jar"

if sys.platform.startswith("win"):
    OS_NAME = "windows"
elif sys.platform == "darwin":
    OS_NAME = "osx"
else:
    OS_NAME = "linux"

_machine = platform.machine().lower()
if _machine in ("arm64", "aarch64"):
    ARCH = "aarch64"
elif _machine in ("x86", "i386", "i686"):
    ARCH = "x86"
else:
    ARCH = "x64"


def ensure_dirs() -> None:
    for d in (ROOT, PROFILES_DIR, VERSIONS_DIR, LIBRARIES_DIR, ASSETS_DIR, RUNTIME_DIR, CACHE_DIR, ANIMATIONS_DIR, SERVERS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def resource_path(rel: str) -> Path:
    """Путь к ресурсу и в исходниках, и внутри PyInstaller-сборки."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = Path(__file__).resolve().parent.parent
    return Path(base) / rel


THEMES = {
    "CD Purple":       {"accent": "#a855f7", "sidebar": "#17151f", "button": "#6d3fc0", "card": "#2a2340", "text": "#f3ecff", "background": "#1e1a2b"},
    "Ely Cyan":       {"accent": "#22d3ee", "sidebar": "#0b1418", "button": "#0891b2", "card": "#16262c", "text": "#e6f1ff", "background": "#0f1b20"},
    "Dracula Purple": {"accent": "#bd93f9", "sidebar": "#1e1f29", "button": "#6d4fc2", "card": "#343746", "text": "#f8f8f2", "background": "#282a36"},
    "Emerald Dark":   {"accent": "#34d399", "sidebar": "#07140f", "button": "#059669", "card": "#12261d", "text": "#e8fff4", "background": "#0b1a14"},
    "Sunset Red":     {"accent": "#fb7185", "sidebar": "#1a0b0e", "button": "#be123c", "card": "#2e1519", "text": "#ffe9ec", "background": "#240f14"},
    "Cyberpunk Gold": {"accent": "#facc15", "sidebar": "#12100a", "button": "#b8860b", "card": "#2a2410", "text": "#fff8dc", "background": "#1a1608"},
}

DEFAULTS = {
    "show_old": True,            # Alpha / Beta
    "show_snapshots": False,
    "show_modloaders": True,
    "only_downloaded": False,
    "theme": "CD Purple",
    "colors": dict(THEMES["CD Purple"]),
    "language": "",              # пусто = определить по ОС при первом запуске
    "default_ram": 4096,
    "java_path": "",
    "ely_client_id": "",
    "ely_client_secret": "",
    "ms_client_id": "",
    "ai_model": "qwen2.5-coder:1.5b",
    "ollama_url": "http://127.0.0.1:11434",
    "last_profile": "",
    "p2p_server": "",            # host:port вашего координатора (server/cd_server.py)
    "cd_id": "",
    "cd_secret": "",
    "skin_bridge_auto": False,   # ставить CustomSkinLoader в новые сборки (ВЫКЛ: мод может ломать запуск)
    "csl_cleanup_v1": False,
    "default_jvm_args": "",       # общие свои аргументы JVM для всех сборок (необязательно)
    "default_game_args": "",      # общие свои аргументы игры
    "curseforge_api_key": "",     # бесплатный ключ: https://console.curseforge.com
    "catalog_kind": "mod",
    "catalog_source": "modrinth",
    "mods_rich": True,            # каталог модов: иконки, описание, галерея
    "mods_per_page": 20,
    "skin_3d": True,              # 3D-просмотр скина на главной (можно крутить мышью)
    "skin_autorotate": True,
    "anim_enabled": True,         # случайные анимации Blockbench на персонаже
    "anim_min": 5,                # минимальный интервал между анимациями, секунд
    "anim_max": 67,               # максимальный интервал, секунд
    # --- внешний вид (Настройки -> Оформление)
    "ui_radius": 14,              # скругление карточек и кнопок
    "ui_win_radius": 14,          # скругление окна
    "ui_font": "Segoe UI",
    "ui_font_size": 13,
    "ui_bg_mode": "default",      # default | custom | none
    "ui_bg_path": "",
    "ui_bg_blur": 7,
    "ui_bg_dim": 47,              # затемнение фона, %
    "ui_glass": 62,               # непрозрачность карточек, %
    "ui_sidebar_w": 108,
    "ui_anim": True,
    "versions_view": "cards",     # cards | list     # однократная чистка CustomSkinLoader из существующих сборок
}


class Settings:
    def __init__(self):
        self._lock = threading.Lock()
        self.data = json.loads(json.dumps(DEFAULTS))
        self.load()

    def load(self):
        try:
            loaded = json.loads(CONFIG_FILE.read_text("utf-8"))
            for k, v in loaded.items():
                self.data[k] = v
            for k, v in DEFAULTS["colors"].items():
                self.data["colors"].setdefault(k, v)
            # стандартная тема теперь фиолетовая: один раз переключаем тех, кто не менял голубую тему по умолчанию
            if not loaded.get("theme_v2"):
                if self.data["theme"] == "Ely Cyan" and self.data["colors"] == THEMES["Ely Cyan"]:
                    self.data["theme"], self.data["colors"] = "CD Purple", dict(THEMES["CD Purple"])
                self.data["theme_v2"] = True
        except Exception:
            pass

    def save(self):
        with self._lock:
            ROOT.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")

    def get(self, key, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value, save=True):
        self.data[key] = value
        if save:
            self.save()
