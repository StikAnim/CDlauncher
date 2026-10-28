"""Проверка сборки ПЕРЕД запуском (и при ремонте): частые причины мгновенных вылетов больших сборок.

  1. моды под ДРУГОЙ загрузчик (Forge-мод в Fabric-сборке и наоборот) - карантин в mods_removed/;
  2. дубликаты одного и того же мода - остаётся самый свежий файл;
  3. недостающие обязательные зависимости - докачиваются с Modrinth;
  4. память: для больших сборок поднимается, а если больше физической ОЗУ - снижается
     (иначе JVM не может зарезервировать heap и игра падает сразу).
"""
import shutil
import zipfile
from pathlib import Path

from . import modrinth, profiles, sysinfo
from .config import ARCH
from .i18n import tr
from .net import Reporter
from .profiles import game_dir

ALLOWED = {"fabric": {"fabric", "quilt"}, "quilt": {"quilt", "fabric"}, "forge": {"forge"},
           "neoforge": {"neoforge", "forge"}}


def jar_kinds(jar: Path) -> set:
    """Какие загрузчики объявляет jar (пусто = библиотека без метаданных мода)."""
    kinds = set()
    try:
        with zipfile.ZipFile(jar) as z:
            names = set(z.namelist())
    except (zipfile.BadZipFile, OSError):
        return {"broken"}
    if "fabric.mod.json" in names:
        kinds.add("fabric")
    if "quilt.mod.json" in names:
        kinds.add("quilt")
    if "META-INF/neoforge.mods.toml" in names:
        kinds.add("neoforge")
    if "META-INF/mods.toml" in names or "mcmod.info" in names:
        kinds.add("forge")
    return kinds


def recommended_ram(mod_count: int) -> int:
    if mod_count < 30:
        return 4096
    if mod_count < 80:
        return 6144
    if mod_count < 150:
        return 8192
    return 10240


def safe_ram_cap() -> int:
    """Верхняя граница heap: 70% физической ОЗУ (и 1 ГБ для 32-битной Java)."""
    total = sysinfo.total_ram_mb()
    cap = int(total * 0.7) if total else 8192
    return min(cap, 1024) if ARCH == "x86" else max(cap, 2048)


def quarantine(profile: dict, jar: Path, folder: str = "mods_removed"):
    dest = game_dir(profile) / folder
    dest.mkdir(exist_ok=True)
    shutil.move(str(jar), str(dest / jar.name))


def run(profile: dict, settings, rep: Reporter = None) -> list:
    """Возвращает список сообщений (на языке интерфейса) о том, что было исправлено."""
    rep = rep or Reporter()
    notes, changed = [], False
    mods_dir = game_dir(profile) / "mods"
    jars = sorted(mods_dir.glob("*.jar")) if mods_dir.exists() else []

    allowed = ALLOWED.get(profile["loader"])
    if allowed and jars:
        bad = []
        for j in jars:
            kinds = jar_kinds(j)
            if kinds and not (kinds & allowed):
                bad.append(j)
        for j in bad:
            quarantine(profile, j)
        if bad:
            notes.append(tr("pre_wrong", n=len(bad), list=", ".join(b.name for b in bad[:6])))
        jars = [j for j in jars if j not in bad]

    seen = {}
    for j in sorted(jars, key=lambda p: p.stat().st_mtime):          # старые -> новые
        ids = frozenset(modrinth.read_jar_meta(j)["ids"])
        if not ids:
            continue
        if ids in seen:
            quarantine(profile, seen[ids])
            notes.append(tr("pre_dup", f=seen[ids].name))
        seen[ids] = j
    jars = list(seen.values()) + [j for j in jars if not modrinth.read_jar_meta(j)["ids"]]

    if jars and profile["loader"] != "vanilla":
        try:
            from . import catalog
            got = modrinth.resolve_local_dependencies(
                profile, rep, lambda mid: catalog.find_by_mod_id(mid, profile, settings, rep))
            if got:
                notes.append(tr("pre_deps", list=", ".join(got)))
        except Exception as e:
            rep.log(f"deps: {e}")

    cur = int(profile.get("ram_mb") or settings.get("default_ram"))
    want, cap = cur, safe_ram_cap()
    rec = recommended_ram(len(jars))
    if rec > want:
        want = rec
    if want > cap:
        want = cap
    if want != cur:
        profile["ram_mb"] = want
        changed = True
        notes.append(tr("pre_ram", n=want, m=len(jars)) if want > cur else
                     tr("pre_ram_cap", n=want, t=sysinfo.total_ram_mb()))
    if changed:
        profiles.save_profile(profile)
    for n in notes:
        rep.log("✔ " + n)
    return notes
