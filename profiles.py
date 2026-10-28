"""Изолированные профили (сборки): у каждой свои моды, конфиги, миры и ресурспаки."""
import json
import re
import shutil
import time
from pathlib import Path

from . import loaders
from .config import PROFILES_DIR
from .net import Reporter

SUBDIRS = ("mods", "config", "saves", "resourcepacks", "shaderpacks", "logs")


def safe_name(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip(".")
    if not s:
        raise ValueError("Пустое имя сборки")
    return s


def game_dir(profile: dict) -> Path:
    if profile.get("_dir"):                 # серверы: у них своя папка, но каталог модов/плагинов работает так же
        return Path(profile["_dir"])
    return PROFILES_DIR / profile["name"]


def list_profiles() -> list:
    out = []
    if PROFILES_DIR.exists():
        for d in sorted(PROFILES_DIR.iterdir()):
            f = d / "profile.json"
            if f.exists():
                try:
                    out.append(json.loads(f.read_text("utf-8")))
                except ValueError:
                    pass
    return out


def icon_path(profile: dict):
    """Своя иконка сборки (profiles/<имя>/icon.png) или None."""
    f = game_dir(profile) / "icon.png"
    return f if f.exists() else None


def set_icon(profile: dict, image_path) -> Path:
    """Сохраняет иконку сборки как PNG. Пиксель-арт остаётся как есть, большие картинки уменьшаются до 256 px."""
    from PIL import Image
    img = Image.open(image_path).convert("RGBA")
    if max(img.size) > 256:
        img.thumbnail((256, 256), Image.LANCZOS)
    dest = game_dir(profile) / "icon.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")
    return dest


def clear_icon(profile: dict):
    f = game_dir(profile) / "icon.png"
    if f.exists():
        f.unlink()


def get_profile(name: str):
    if name.startswith("srv:"):
        from . import servers
        return servers.pseudo_profile(servers.get_server(name[4:]))
    f = PROFILES_DIR / name / "profile.json"
    return json.loads(f.read_text("utf-8")) if f.exists() else None


def save_profile(profile: dict):
    d = PROFILES_DIR / profile["name"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), "utf-8")


def create_profile(name: str, mc_version: str, loader: str, rep: Reporter = None,
                   ram_mb: int = 4096, settings=None, loader_version: str = None) -> dict:
    rep = rep or Reporter()
    name = safe_name(name)
    if (PROFILES_DIR / name / "profile.json").exists():
        raise ValueError(f"Сборка «{name}» уже существует")
    rep.log(f"Создаю сборку «{name}»: {loader} {mc_version}")
    version_id = loaders.install(loader, mc_version, rep, settings, loader_version)
    d = PROFILES_DIR / name
    for sub in SUBDIRS:
        (d / sub).mkdir(parents=True, exist_ok=True)
    profile = {"name": name, "mc_version": mc_version, "loader": loader.lower(),
               "version_id": version_id, "ram_mb": int(ram_mb), "java_major": 0,
               "created": int(time.time())}
    save_profile(profile)
    if settings is not None and settings.get("skin_bridge_auto") and profile["loader"] != "vanilla":
        try:
            from . import skins
            skins.enable_skin_bridge(profile, rep)
        except Exception as e:  # Skin Bridge не критичен для создания сборки
            rep.log(f"Skin Bridge: {e}")
    return profile


def delete_profile(name: str):
    d = PROFILES_DIR / name
    if d.exists() and (d / "profile.json").exists():
        shutil.rmtree(d)


def list_targets() -> list:
    """Сборки + серверы (как псевдопрофили) - для каталога модов и плагинов."""
    from . import servers
    return list_profiles() + [servers.pseudo_profile(s) for s in servers.list_servers()]
