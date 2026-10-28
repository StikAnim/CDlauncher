"""Формат .cdversion: вся сборка в ОДНОМ файле (это zip с другим расширением).

Имя файла: <Название сборки>_<версия Minecraft>.cdversion (например Моя сборка_1.20.1.cdversion).
Внутри: manifest.json (версия игры, загрузчик, память, аргументы запуска...) и папка files/ с содержимым профиля
(моды, конфиги, ресурспаки, шейдеры, опционально миры, иконка). Логи и краш-репорты не упаковываются.
При импорте создаётся НОВЫЙ профиль: нужная версия Minecraft и загрузчик ставятся лаунчером, затем раскладываются файлы."""
import json
import re
import shutil
import time
import zipfile
from pathlib import Path

from . import profiles
from .config import APP_VERSION
from .net import Reporter
from .profiles import game_dir

EXT = ".cdversion"
FORMAT = 1
EXCLUDE_TOP = {"logs", "crash-reports", "mods_removed", "config_removed", "screenshots"}
EXCLUDE_NAMES = {"cd_unresolved.json", "cd_args.txt", "profile.json"}
SAFE = re.compile(r'[\\/:*?"<>|]')


def pack_name(profile: dict) -> str:
    return f"{SAFE.sub('_', profile['name'])}_{profile['mc_version']}{EXT}"


def loader_version_of(profile: dict):
    """Версия загрузчика из id запускаемой версии (fabric-loader-0.15.0-1.20.1, neoforge-21.1.248, 1.20.1-forge-47.2.0)."""
    vid, mc, loader = profile.get("version_id", ""), profile["mc_version"], profile["loader"]
    patterns = {"fabric": rf"^fabric-loader-(.+)-{re.escape(mc)}$", "quilt": rf"^quilt-loader-(.+)-{re.escape(mc)}$",
                "neoforge": r"^neoforge-(.+)$", "forge": r"^.*forge-(.+)$"}
    m = re.match(patterns.get(loader, "^$"), vid)
    return m.group(1) if m else None


def _included(rel: Path, include_saves: bool) -> bool:
    top = rel.parts[0]
    if top in EXCLUDE_TOP or rel.name in EXCLUDE_NAMES or rel.name.startswith("hs_err"):
        return False
    if top == "saves" and not include_saves:
        return False
    return True


def export_profile(profile: dict, dest: Path, include_saves: bool = True, rep: Reporter = None) -> Path:
    rep = rep or Reporter()
    gdir = game_dir(profile)
    files = [p for p in gdir.rglob("*") if p.is_file() and _included(p.relative_to(gdir), include_saves)]
    manifest = {"format": FORMAT, "launcher": APP_VERSION, "name": profile["name"], "mc_version": profile["mc_version"],
                "loader": profile["loader"], "loader_version": loader_version_of(profile), "version_id": profile.get("version_id"),
                "ram_mb": profile.get("ram_mb"), "jvm_args": profile.get("jvm_args", ""), "game_args": profile.get("game_args", ""),
                "java_major": profile.get("java_major", 0), "created": int(time.time()), "includes_saves": include_saves,
                "files": len(files)}
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for i, p in enumerate(files, 1):
            z.write(p, "files/" + p.relative_to(gdir).as_posix())
            if i % 20 == 0 or i == len(files):
                rep.progress(i, len(files))
    rep.log(f"Упаковано файлов: {len(files)} → {dest.name}")
    return dest


def read_manifest(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as z:
            m = json.loads(z.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise ValueError("Это не файл .cdversion (нет manifest.json)")
    if m.get("format", 0) > FORMAT or not m.get("mc_version") or not m.get("loader"):
        raise ValueError("Файл .cdversion создан более новой версией лаунчера или повреждён")
    return m


def _unique(name: str) -> str:
    base, cand, n = SAFE.sub("_", name).strip() or "Version", None, 1
    cand = base
    while (profiles.PROFILES_DIR / cand).exists():
        n += 1
        cand = f"{base} ({n})"
    return cand


def import_pack(path: Path, settings, rep: Reporter = None) -> dict:
    rep = rep or Reporter()
    m = read_manifest(path)
    name = _unique(m.get("name") or Path(path).stem)
    rep.log(f"Импорт «{name}»: {m['loader']} {m['mc_version']}")
    profile = profiles.create_profile(name, m["mc_version"], m["loader"], rep, int(m.get("ram_mb") or 4096), settings,
                                      m.get("loader_version"))
    gdir = game_dir(profile).resolve()
    with zipfile.ZipFile(path) as z:
        members = [n for n in z.namelist() if n.startswith("files/") and not n.endswith("/")]
        for i, n in enumerate(members, 1):
            target = (gdir / n[len("files/"):]).resolve()
            if gdir not in target.parents:                       # защита от zip-slip
                raise ValueError(f"Небезопасный путь в архиве: {n}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if i % 20 == 0 or i == len(members):
                rep.progress(i, len(members))
    for k in ("jvm_args", "game_args", "java_major"):
        if m.get(k):
            profile[k] = m[k]
    profiles.save_profile(profile)
    rep.log(f"Готово: файлов {len(members)}")
    return profile
