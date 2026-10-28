"""Skin Bridge: максимальная видимость скинов Ely.by.

ЧЕСТНО о границах технологии. Скин игрок видит глазами СВОЕГО клиента, а список «кто какой скин
носит» определяет сервер (online-mode: Mojang session server). Поэтому:
  * authlib-injector (уже встроен в запуск) заставляет ВАШ клиент и сервер, где вы играете под Ely.by,
    брать скины из Ely.by;
  * CustomSkinLoader (ставится в сборку этим модулем) заставляет ВАШ клиент показывать скины ДРУГИХ
    игроков из нескольких источников: Ely.by, Mojang, затем фоллбэк на скин по нику;
  * чтобы ваш скин видели ВСЕ на сервере, нужна поддержка на стороне сервера (authlib-injector с Ely.by
    или SkinRestorer на offline-сервере) либо мод CustomSkinLoader у зрителей. Без мода у зрителя и
    без настройки сервера повлиять на то, что рисует чужой клиент, невозможно.
"""
import io
import json
import shutil

from PIL import Image

from . import modrinth
from .config import ROOT
from .net import Reporter
from .profiles import game_dir

CSL_SLUG = "customskinloader"
# Источники по приоритету: Ely.by -> Mojang -> по нику с общедоступных скин-серверов.
LOADLIST = [
    # 1) локальный скин из лаунчера (редактор скинов / офлайн-режим) - имеет приоритет для своего ника
    {"name": "LocalSkin", "type": "Legacy", "checkPNG": True, "skin": "LocalSkin/skins/{USERNAME}.png",
     "model": "auto", "cape": "LocalSkin/capes/{USERNAME}.png", "elytra": "LocalSkin/elytras/{USERNAME}.png"},
    # 2) Ely.by (Yggdrasil API), 3) Mojang, 4) LittleSkin
    {"name": "ElyBy", "type": "Yggdrasil", "apiRoot": "https://authserver.ely.by/api/"},
    {"name": "Mojang", "type": "MojangAPI", "apiRoot": "https://api.mojang.com/",
     "sessionRoot": "https://sessionserver.mojang.com/"},
    {"name": "LittleSkin", "type": "CustomSkinAPI", "root": "https://littleskin.cn/csl/"},
]


def write_config(profile: dict) -> str:
    cfg_dir = game_dir(profile) / "CustomSkinLoader"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"enable": True, "loadlist": LOADLIST, "enableDynamicSkull": True, "enableTransparentSkin": True,
           "forceLoadAllTextures": False, "enableCape": True, "cacheExpiry": 30}
    (cfg_dir / "CustomSkinLoader.json").write_text(json.dumps(cfg, indent=2), "utf-8")
    return str(cfg_dir)


def enable_skin_bridge(profile: dict, rep: Reporter = None) -> bool:
    """Ставит CustomSkinLoader (через Modrinth, вместе с зависимостями) и пишет конфиг.
    Для vanilla-профилей мод поставить нельзя - возвращает False."""
    rep = rep or Reporter()
    if profile["loader"] == "vanilla":
        raise RuntimeError("для vanilla нужен Fabric/Quilt/Forge/NeoForge")
    modrinth.install_mod(profile, CSL_SLUG, rep)
    write_config(profile)
    return True


# ----------------------------------------------------- локальные скины (.cdlauncher/skins)
SKINS_DIR = ROOT / "skins"
ELY_SKIN_PAGE = "https://ely.by/skins"   # у Ely.by нет API загрузки скинов - загрузка только через сайт


def local_skin_path(username: str):
    return SKINS_DIR / f"{username}.png"


def save_local_skin(username: str, png: bytes):
    """Сохраняет скин игрока в .cdlauncher/skins/<ник>.png (проверяет формат 64x64 / 64x32)."""
    img = Image.open(io.BytesIO(png))
    if img.size not in ((64, 64), (64, 32)):
        raise ValueError(f"Скин должен быть 64x64 или 64x32, а не {img.size[0]}x{img.size[1]}")
    SKINS_DIR.mkdir(parents=True, exist_ok=True)
    path = local_skin_path(username)
    img.convert("RGBA").save(path, "PNG")
    return path


def apply_local_skin(profile: dict, account: dict, rep: Reporter = None) -> bool:
    """Перед запуском кладёт сохранённый скин в CustomSkinLoader/LocalSkin профиля.
    Работает для любого режима входа; для офлайн-аккаунтов это единственный способ увидеть свой скин."""
    rep = rep or Reporter()
    src = local_skin_path(account["name"])
    if not src.exists() or profile["loader"] == "vanilla":
        return False
    gdir = game_dir(profile)
    if not has_skin_bridge(profile):
        return False       # мод НЕ ставим сами: если его нет - значит вы его не хотите (или он удалён)
    write_config(profile)
    dest = gdir / "CustomSkinLoader" / "LocalSkin" / "skins"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest / f"{account['name']}.png")
    rep.log(f"Скин {account['name']} подставлен из {src.name}")
    return True


def upload_microsoft(access_token: str, png: bytes, variant: str = "classic"):
    """Загрузка скина в лицензионный аккаунт Microsoft (официальный Minecraft Services API)."""
    import requests
    r = requests.post("https://api.minecraftservices.com/minecraft/profile/skins",
                      headers={"Authorization": f"Bearer {access_token}"}, data={"variant": variant},
                      files={"file": ("skin.png", png, "image/png")}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Minecraft Services {r.status_code}: {r.text[:160]}")


def _csl_jars(profile: dict) -> list:
    mods = game_dir(profile) / "mods"
    return [j for j in mods.glob("*.jar") if "customskinloader" in j.name.lower().replace("_", "").replace("-", "")] \
        if mods.exists() else []


def has_skin_bridge(profile: dict) -> bool:
    return bool(_csl_jars(profile))


def remove_skin_bridge(profile: dict) -> list:
    """Убирает CustomSkinLoader из сборки (jar уходит в mods_removed/) и из учёта лаунчера."""
    removed = []
    for jar in _csl_jars(profile):
        modrinth.quarantine_mod(profile, jar.name)
        removed.append(jar.name)
    db = modrinth.load_db(profile)
    for k in [k for k, v in db.items() if v.get("slug") == CSL_SLUG]:
        db.pop(k)
        modrinth._save_db(profile, db)
    return removed


def cleanup_all_profiles() -> int:
    """Однократная чистка: убрать автоматически поставленный CustomSkinLoader из всех сборок."""
    from . import profiles
    n = 0
    for p in profiles.list_profiles():
        n += len(remove_skin_bridge(p))
    return n
