"""Каталог модов Modrinth: поиск, установка в один клик, авто-зависимости, анализ локальных jar."""
import json
import re
import shutil
import zipfile
from pathlib import Path

from .net import Reporter, download, get_json
from .profiles import game_dir

API = "https://api.modrinth.com/v2"

CATEGORIES = {
    "Все": None, "Optimization": "optimization", "Magic": "magic", "Tech": "technology",
    "RPG / Adventure": "adventure", "Library": "library", "Utility": "utility",
    "Decoration": "decoration", "Worldgen": "worldgen", "Food": "food", "Storage": "storage",
}

BUILTIN_IDS = {"minecraft", "java", "fabricloader", "fabric-loader", "forge", "neoforge", "fml",
               "quilt_loader", "quilt-loader", "javafml", "lowcodefml", "mixinextras"}


class ModError(Exception):
    pass


def loader_cats(loader: str) -> list:
    loader = (loader or "").lower()
    if loader == "quilt":
        return ["quilt", "fabric"]
    if loader == "paper":
        return ["paper", "spigot", "bukkit"]
    if loader == "purpur":
        return ["purpur", "paper", "spigot", "bukkit"]
    if loader == "folia":
        return ["folia"]
    return [loader] if loader and loader != "vanilla" else []


def search_page(query: str, mc: str = None, loader: str = None, category: str = None,
                limit: int = 20, offset: int = 0, project_type: str = "mod") -> dict:
    """Одна страница выдачи: {'hits': [...], 'total': сколько всего найдено, 'offset', 'limit'}."""
    facets = [[f"project_type:{project_type}"]]
    if mc:
        facets.append([f"versions:{mc}"])
    cats = loader_cats(loader)
    if cats:
        facets.append([f"categories:{c}" for c in cats])
    if category:
        facets.append([f"categories:{category}"])
    params = {"query": query or "", "facets": json.dumps(facets), "limit": limit, "offset": offset,
              "index": "relevance" if query else "downloads"}
    data = get_json(f"{API}/search", params=params)
    return {"hits": data.get("hits", []), "total": int(data.get("total_hits", 0)),
            "offset": int(data.get("offset", offset)), "limit": int(data.get("limit", limit))}


def search(query: str, mc: str = None, loader: str = None, category: str = None,
           limit: int = 20, offset: int = 0) -> list:
    return search_page(query, mc, loader, category, limit, offset)["hits"]


def project_details(project: str) -> dict:
    """Полная карточка проекта: описание (markdown), галерея, ссылки, лицензия, версии игры."""
    return get_json(f"{API}/project/{project}")


def project_versions(project: str, mc: str = None, loader: str = None, limit: int = 15) -> list:
    params = {}
    if mc:
        params["game_versions"] = json.dumps([mc])
    cats = loader_cats(loader)
    if cats:
        params["loaders"] = json.dumps(cats)
    return get_json(f"{API}/project/{project}/version", params=params)[:limit]


def find_version_for(project: str, mc: str, loader: str, kind: str):
    """Версия проекта под сборку. Для не-модов (ресурспаки, шейдеры, датапаки) loader не используется, а если точной
    версии игры нет - берётся самая новая (пак обычно работает и на соседних версиях). Возвращает (version, exact)."""
    if kind in ("mod", "plugin"):
        v = find_version(project, mc, loader)
        return v, bool(v)
    params = {"game_versions": json.dumps([mc])}
    versions = get_json(f"{API}/project/{project}/version", params=params)
    if versions:
        rel = [v for v in versions if v.get("version_type") == "release"]
        return (rel or versions)[0], True
    versions = get_json(f"{API}/project/{project}/version")
    return (versions[0] if versions else None), False


def find_version(project: str, mc: str, loader: str):
    params = {"game_versions": json.dumps([mc])}
    cats = loader_cats(loader)
    if cats:
        params["loaders"] = json.dumps(cats)
    versions = get_json(f"{API}/project/{project}/version", params=params)
    if not versions:
        return None
    releases = [v for v in versions if v.get("version_type") == "release"]
    return (releases or versions)[0]


def _db_path(profile: dict) -> Path:
    return game_dir(profile) / "cd_mods.json"


def load_db(profile: dict) -> dict:
    try:
        return json.loads(_db_path(profile).read_text("utf-8"))
    except Exception:
        return {}


def _save_db(profile: dict, db: dict):
    _db_path(profile).write_text(json.dumps(db, ensure_ascii=False, indent=2), "utf-8")


def install_mod(profile: dict, project: str, rep: Reporter = None, _seen: set = None) -> list:
    """Ставит мод и рекурсивно ВСЕ обязательные зависимости. Возвращает названия установленного."""
    rep = rep or Reporter()
    seen = _seen if _seen is not None else set()
    mods_dir = game_dir(profile) / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)

    proj = get_json(f"{API}/project/{project}")
    pid = proj["id"]
    if pid in seen:
        return []
    seen.add(pid)

    db = load_db(profile)
    if pid in db and (mods_dir / db[pid]["file"]).exists():
        rep.log(f"{proj['title']} уже установлен")
        ver = None
    else:
        ver = find_version(pid, profile["mc_version"], profile["loader"])
        if not ver:
            raise ModError(f"«{proj['title']}» не имеет версии для {profile['loader']} {profile['mc_version']}")
        f = next((x for x in ver["files"] if x.get("primary")), ver["files"][0])
        rep.log(f"Скачиваю {proj['title']} {ver['version_number']}...")
        download(f["url"], mods_dir / f["filename"], f.get("hashes", {}).get("sha1"))
        db = load_db(profile)
        db[pid] = {"title": proj["title"], "file": f["filename"], "version": ver["version_number"],
                   "slug": proj["slug"]}
        _save_db(profile, db)

    installed = [proj["title"]] if ver else []
    deps = ver["dependencies"] if ver else []
    for dep in deps:
        if dep.get("dependency_type") != "required":
            continue
        dpid = dep.get("project_id")
        if not dpid and dep.get("version_id"):
            try:
                dpid = get_json(f"{API}/version/{dep['version_id']}")["project_id"]
            except Exception:
                continue
        if dpid:
            try:
                installed += install_mod(profile, dpid, rep, seen)
            except Exception as e:
                rep.log(f"⚠ Зависимость не установлена: {e}")
    return installed


def remove_mod(profile: dict, pid_or_file: str) -> bool:
    mods_dir = game_dir(profile) / "mods"
    db = load_db(profile)
    fname = db.get(pid_or_file, {}).get("file", pid_or_file)
    f = mods_dir / fname
    if f.exists():
        f.unlink()
        db.pop(pid_or_file, None)
        for k in [k for k, v in db.items() if v["file"] == fname]:
            db.pop(k)
        _save_db(profile, db)
        return True
    return False


def installed_mods(profile: dict) -> list:
    mods_dir = game_dir(profile) / "mods"
    db = load_db(profile)
    by_file = {v["file"]: (k, v) for k, v in db.items()}
    out = []
    if mods_dir.exists():
        for f in sorted(mods_dir.glob("*.jar")):
            pid, meta = by_file.get(f.name, (None, None))
            out.append({"file": f.name, "title": meta["title"] if meta else f.name, "project": pid})
    return out


# ------------------------------------------------- анализ локальных jar-файлов ---
def read_jar_meta(jar: Path) -> dict:
    """Возвращает {'ids': {id,...}, 'requires': {id,...}} для Fabric/Quilt/Forge/NeoForge."""
    ids, requires = set(), set()
    try:
        with zipfile.ZipFile(jar) as z:
            names = set(z.namelist())
            if "fabric.mod.json" in names:
                j = json.loads(z.read("fabric.mod.json").decode("utf-8", "replace"), strict=False)
                ids.add(j.get("id", ""))
                ids.update(j.get("provides", []))
                requires.update((j.get("depends") or {}).keys())
            elif "quilt.mod.json" in names:
                j = json.loads(z.read("quilt.mod.json").decode("utf-8", "replace"), strict=False)
                ql = j.get("quilt_loader", {})
                ids.add(ql.get("id", ""))
                for d in ql.get("depends", []):
                    requires.add(d if isinstance(d, str) else d.get("id", ""))
            for toml_name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                if toml_name in names:
                    import tomllib
                    t = tomllib.loads(z.read(toml_name).decode("utf-8", "replace"))
                    for m in t.get("mods", []):
                        ids.add(m.get("modId", ""))
                    for mod_id, deps in (t.get("dependencies") or {}).items():
                        for d in deps:
                            mandatory = d.get("mandatory", d.get("type", "") == "required")
                            if mandatory and d.get("modId"):
                                requires.add(d["modId"])
                    break
    except Exception:
        pass
    ids.discard("")
    requires.discard("")
    return {"ids": ids, "requires": requires}


def missing_dependencies(profile: dict) -> set:
    mods_dir = game_dir(profile) / "mods"
    provided, required = set(), set()
    for jar in mods_dir.glob("*.jar"):
        meta = read_jar_meta(jar)
        provided |= meta["ids"]
        required |= meta["requires"]
    missing = {r for r in required if r not in provided and r.lower() not in BUILTIN_IDS}
    return {"fabric-api" if m.startswith("fabric") else m for m in missing} - provided


def nested_ids(jar: Path, depth: int = 2) -> set:
    """Mod id из вложенных jar (Fabric META-INF/jars, Forge/NeoForge META-INF/jarjar): они уже «есть» в сборке."""
    import io
    found = set()
    try:
        with zipfile.ZipFile(jar) as z:
            for name in z.namelist():
                if name.endswith(".jar") and (name.startswith("META-INF/jars/") or name.startswith("META-INF/jarjar/")):
                    inner = io.BytesIO(z.read(name))
                    meta = read_jar_meta(inner)
                    found |= meta["ids"]
                    if depth > 1:
                        found |= nested_ids(inner, depth - 1)
    except Exception:
        pass
    return found


MAX_AUTO_DEPS = 8


def resolve_local_dependencies(profile: dict, rep: Reporter = None, fallback=None) -> list:
    """Докачивает недостающие обязательные зависимости. Ставит ТОЛЬКО проект, чей slug точно равен mod id,
    и не более MAX_AUTO_DEPS за раз: угадывание «похожих» модов в больших сборках приводило к вылетам."""
    rep = rep or Reporter()
    installed = []
    mods_dir = game_dir(profile) / "mods"
    nested = set()
    for jar in mods_dir.glob("*.jar"):
        nested |= nested_ids(jar)
    missing = sorted(m for m in missing_dependencies(profile) if m not in nested)
    if len(missing) > MAX_AUTO_DEPS:
        rep.log(f"⚠ Слишком много «недостающих» зависимостей ({len(missing)}): автоустановку пропускаю. {', '.join(missing[:12])}")
        return installed
    for mod_id in missing:
        try:
            installed += install_mod(profile, mod_id, rep)
        except Exception as e:
            got = []
            if fallback:  # проект называется иначе, чем mod id: ищем по названию и ПРОВЕРЯЕМ по содержимому jar
                try:
                    got = fallback(mod_id)
                except Exception as e2:
                    rep.log(f"⚠ Поиск «{mod_id}»: {e2}")
            installed += got
            if not got:
                rep.log(f"⚠ Зависимость «{mod_id}» не установлена: {e}")
    return installed


def find_jar_by_mod_id(profile: dict, mod_id: str):
    for jar in (game_dir(profile) / "mods").glob("*.jar"):
        if mod_id in read_jar_meta(jar)["ids"]:
            return jar
    return None


def quarantine_mod(profile: dict, mod_id_or_file: str):
    """Безопасное «удаление»: jar уходит в mods_removed/, чтобы можно было вернуть."""
    gdir = game_dir(profile)
    jar = gdir / "mods" / mod_id_or_file
    if not jar.exists():
        jar = find_jar_by_mod_id(profile, mod_id_or_file)
    if not jar:
        for cand in (gdir / "mods").glob("*.jar"):
            if mod_id_or_file.lower() in cand.name.lower():
                jar = cand
                break
    if not jar:
        return None
    dest = gdir / "mods_removed"
    dest.mkdir(exist_ok=True)
    shutil.move(str(jar), str(dest / jar.name))
    db = load_db(profile)
    for k in [k for k, v in db.items() if v["file"] == jar.name]:
        db.pop(k)
    _save_db(profile, db)
    return jar.name


def quarantine_check(profile: dict, mod_id_or_file: str) -> bool:
    """True, если указанный ИИ мод реально существует в сборке (защита от галлюцинаций)."""
    mods_dir = game_dir(profile) / "mods"
    if (mods_dir / mod_id_or_file).exists() or find_jar_by_mod_id(profile, mod_id_or_file):
        return True
    return any(mod_id_or_file.lower() in c.name.lower() for c in mods_dir.glob("*.jar")) if mod_id_or_file else False
