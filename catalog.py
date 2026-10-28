"""Единый каталог: Modrinth + CurseForge, типы контента (моды, ресурспаки, датапаки, шейдеры, миры, сборки)."""
import json
import math
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from . import curseforge, modrinth, profiles
from .config import CACHE_DIR
from .net import Reporter, download, download_many, get_json
from .profiles import game_dir

KINDS = ["mod", "resourcepack", "datapack", "shader", "world", "modpack", "plugin"]
SOURCES = ["modrinth", "curseforge", "all"]
MR_TYPE = {"mod": "mod", "resourcepack": "resourcepack", "datapack": "datapack", "shader": "shader",
           "modpack": "modpack", "world": None, "plugin": "plugin"}                       # у Modrinth нет раздела «карты»
DEST_DIR = {"mod": "mods", "resourcepack": "resourcepacks", "shader": "shaderpacks", "plugin": "plugins"}
ALLOWED_TOP = {"mods", "resourcepacks", "shaderpacks", "datapacks", "saves", "plugins", "world"}


class Blocked(Exception):
    """Автор запретил скачивание сторонними программами - только через сайт."""
    def __init__(self, url):
        super().__init__(url)
        self.url = url


def cf_key(settings) -> str:
    return (settings.get("curseforge_api_key") or "").strip() if settings else ""


def page_size(source: str, per_page: int) -> int:
    return max(per_page // 2, 5) if source == "all" else per_page


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


# ======================================================================== поиск
def _mr_norm(h: dict, kind: str) -> dict:
    return {"project_id": h["project_id"], "source": "modrinth", "kind": kind, "slug": h.get("slug", ""),
            "title": h.get("title", "?"), "author": h.get("author", ""), "description": h.get("description", ""),
            "downloads": h.get("downloads", 0), "follows": h.get("follows", 0), "icon_url": h.get("icon_url"),
            "display_categories": h.get("display_categories") or h.get("categories", [])[:4],
            "website": f"https://modrinth.com/{MR_TYPE[kind]}/{h.get('slug', h['project_id'])}"}


def _mr_search(kind, query, profile, category, size, off, notes):
    mc = profile["mc_version"] if kind in ("mod", "datapack", "resourcepack", "shader", "plugin") else None
    loader = profile["loader"] if kind in ("mod", "plugin") else None
    r = modrinth.search_page(query, mc, loader, category if kind == "mod" else None, size, off, project_type=MR_TYPE[kind])
    if not r["hits"] and mc and kind in ("resourcepack", "shader") and off == 0:
        r = modrinth.search_page(query, None, None, None, size, off, project_type=MR_TYPE[kind])
        notes.append("filter_relaxed")
    return {"hits": [_mr_norm(h, kind) for h in r["hits"]], "total": r["total"]}


def search(source: str, kind: str, query: str, profile: dict, category, per_page: int, page: int, settings) -> dict:
    """{'hits': [...], 'total': N, 'pages': P, 'page_size': S, 'notes': [коды]} - hits одинакового формата для обоих источников."""
    size, off, notes, parts = page_size(source, per_page), page * page_size(source, per_page), [], []
    if source in ("modrinth", "all"):
        if MR_TYPE[kind] is None:
            notes.append("worlds_cf_only")
        else:
            r = _mr_search(kind, query, profile, category, size, off, notes)
            parts.append(("modrinth", r["hits"], r["total"]))
    if source in ("curseforge", "all"):
        key = cf_key(settings)
        if kind == "plugin":
            notes.append("plugins_modrinth_only")
        elif not key:
            notes.append("cf_key_missing")
        else:
            try:
                r = curseforge.search(key, kind, query, profile, size, off)
                parts.append(("curseforge", r["hits"], r["total"]))
            except Exception as e:
                notes.append(f"cf_error:{e}")
    merged, seen = [], set()
    longest = max((len(p[1]) for p in parts), default=0)
    for i in range(longest):                         # чередуем источники; повтор одного проекта на двух сайтах убираем
        for _, hits, _ in parts:
            if i < len(hits):
                h = hits[i]
                sig = (_norm_title(h["title"]), h["kind"])
                if sig in seen:
                    continue
                seen.add(sig)
                merged.append(h)
    pages = max([math.ceil(t / size) for _, _, t in parts] or [1])
    return {"hits": merged, "total": sum(t for _, _, t in parts), "pages": max(pages, 1), "page_size": size, "notes": notes}


# ===================================================================== подробности
def details(hit: dict, profile: dict, settings) -> dict:
    """Описание, галерея, ссылки, версии - в общем формате для окна подробностей."""
    kind = hit["kind"]
    if hit["source"] == "modrinth":
        p = modrinth.project_details(hit["project_id"])
        try:
            vs = modrinth.project_versions(hit["project_id"], profile["mc_version"], profile["loader"] if kind == "mod" else None)
        except Exception:
            vs = []
        gallery = [{"thumb": g.get("url"), "url": g.get("raw_url") or g.get("url"), "title": g.get("title", "")}
                   for g in sorted(p.get("gallery") or [], key=lambda g: (not g.get("featured"), g.get("ordering", 0)))]
        gv = p.get("game_versions") or []
        lic = (p.get("license") or {}).get("name", "")
        return {"title": p.get("title", hit["title"]), "summary": p.get("description", ""), "body": p.get("body") or p.get("description", ""),
                "body_format": "markdown", "gallery": gallery, "icon_url": p.get("icon_url") or hit.get("icon_url"),
                "meta": f"⬇ {p.get('downloads', 0):,}   ♥ {p.get('followers', 0):,}   {', '.join(p.get('loaders', []))}   "
                        f"MC {gv[0] if gv else '?'}–{gv[-1] if gv else '?'}   {lic}",
                "links": [("Modrinth", hit.get("website")), ("Source", p.get("source_url")), ("Issues", p.get("issues_url")),
                          ("Wiki", p.get("wiki_url")), ("Discord", p.get("discord_url"))],
                "versions": [f"{v.get('version_number', '?')} · {v.get('version_type', '')} · {', '.join(v.get('game_versions', [])[:3])}"
                             f" · {', '.join(v.get('loaders', []))}" for v in vs]}
    key, cid = cf_key(settings), hit["project_id"].split(":", 1)[1]
    raw = curseforge.mod(key, cid)
    try:
        body = curseforge.description_html(key, cid)
    except Exception:
        body = f"<p>{raw.get('summary', '')}</p>"
    try:
        fl = curseforge.files(key, cid, profile["mc_version"], profile["loader"] if kind == "mod" else None, 15)
    except Exception:
        fl = []
    links = raw.get("links") or {}
    return {"title": raw.get("name", hit["title"]), "summary": raw.get("summary", ""), "body": body, "body_format": "html",
            "gallery": [{"thumb": s.get("thumbnailUrl") or s.get("url"), "url": s.get("url"), "title": s.get("title", "")}
                        for s in raw.get("screenshots") or []],
            "icon_url": (raw.get("logo") or {}).get("thumbnailUrl") or hit.get("icon_url"),
            "meta": f"⬇ {raw.get('downloadCount', 0):,}   ♥ {raw.get('thumbsUpCount', 0):,}   "
                    f"{', '.join(c.get('name', '') for c in raw.get('categories') or [])[:80]}",
            "links": [("CurseForge", hit.get("website")), ("Source", links.get("sourceUrl")), ("Issues", links.get("issuesUrl")),
                      ("Wiki", links.get("wikiUrl"))],
            "versions": [f"{f.get('displayName', f.get('fileName', '?'))} · {', '.join(f.get('gameVersions', [])[:4])}" for f in fl]}


# ====================================================================== установка
def _db_put(profile, key, entry):
    db = modrinth.load_db(profile)
    db[key] = entry
    modrinth._save_db(profile, db)


def _dest_dir(kind: str, profile: dict, world: str = None) -> Path:
    g = game_dir(profile)
    if kind == "datapack":
        if profile.get("_dir"):                      # сервер: датапаки лежат в мире сервера (по умолчанию «world»)
            return g / (world or "world") / "datapacks"
        return g / "saves" / world / "datapacks" if world else g / "datapacks"
    return g / DEST_DIR[kind]


def list_worlds(profile: dict) -> list:
    saves = game_dir(profile) / "saves"
    if not saves.exists():
        return []
    return [d.name for d in sorted(saves.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir()]


def _safe_join(base: Path, rel: str) -> Path:
    target = (base / rel).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        raise ValueError(f"Небезопасный путь в архиве: {rel}")
    return target


def _unique(base: Path, name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "World"
    cand, n = name, 1
    while (base / cand).exists():
        n += 1
        cand = f"{name} ({n})"
    return cand


def install_world_zip(profile: dict, zip_path: Path, title: str) -> str:
    """Распаковывает карту в saves/: ищет папку с level.dat внутри архива (она может лежать глубже)."""
    saves = game_dir(profile) / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with zipfile.ZipFile(zip_path) as z:
            for m in z.namelist():
                _safe_join(tmp, m)
            z.extractall(tmp)
        roots = sorted((p.parent for p in tmp.rglob("level.dat")), key=lambda p: len(p.parts))
        if not roots:
            raise ValueError("В архиве нет level.dat - это не карта Minecraft")
        name = _unique(saves, roots[0].name if roots[0] != tmp else title)
        shutil.copytree(roots[0], saves / name)
    return name


def _record(profile, hit, version, filename, rel):
    _db_put(profile, hit["project_id"], {"title": hit["title"], "file": filename, "version": version,
                                          "slug": hit.get("slug", ""), "kind": hit["kind"], "rel": rel})


def _mr_install_file(hit, profile, rep, world):
    kind = hit["kind"]
    ver, exact = modrinth.find_version_for(hit["project_id"], profile["mc_version"], profile["loader"], kind)
    if not ver:
        raise modrinth.ModError(f"«{hit['title']}»: нет подходящих версий")
    f = next((x for x in ver["files"] if x.get("primary")), ver["files"][0])
    dest = _dest_dir(kind, profile, world)
    rep.log(f"Скачиваю {hit['title']} {ver.get('version_number', '')}...")
    download(f["url"], dest / f["filename"], f.get("hashes", {}).get("sha1"))
    rel = str((dest / f["filename"]).relative_to(game_dir(profile))).replace("\\", "/")
    _record(profile, hit, ver.get("version_number", ""), f["filename"], rel)
    return {"installed": [hit["title"]], "note": None if exact else "version_mismatch"}


def _cf_file_for(hit, profile, key, kind):
    cid = hit["project_id"].split(":", 1)[1]
    fl = curseforge.files(key, cid, profile["mc_version"] if kind != "world" else None,
                          profile["loader"] if kind == "mod" else None, 40)
    f = curseforge.pick_file(fl)
    if not f:
        raise modrinth.ModError(f"«{hit['title']}»: нет подходящих файлов на CurseForge")
    return f


def _cf_install(hit, profile, key, rep, world, seen=None):
    kind, seen = hit["kind"], seen if seen is not None else set()
    if hit["project_id"] in seen:
        return {"installed": []}
    seen.add(hit["project_id"])
    f = _cf_file_for(hit, profile, key, kind)
    if not f.get("downloadUrl"):                     # автор отключил загрузку сторонними программами - уважаем
        raise Blocked(hit.get("website") or "https://www.curseforge.com")
    installed = []
    if kind == "world":
        tmp = CACHE_DIR / "worlds" / f["fileName"]
        rep.log(f"Скачиваю карту {hit['title']}...")
        download(f["downloadUrl"], tmp, curseforge.sha1_of(f))
        name = install_world_zip(profile, tmp, hit["title"])
        _record(profile, hit, f.get("displayName", ""), name, f"saves/{name}")
        return {"installed": [f"{hit['title']} → saves/{name}"]}
    dest = _dest_dir(kind, profile, world)
    rep.log(f"Скачиваю {hit['title']} (CurseForge)...")
    download(f["downloadUrl"], dest / f["fileName"], curseforge.sha1_of(f))
    rel = str((dest / f["fileName"]).relative_to(game_dir(profile))).replace("\\", "/")
    _record(profile, hit, f.get("displayName", ""), f["fileName"], rel)
    installed.append(hit["title"])
    if kind == "mod":                                # обязательные зависимости (relationType 3)
        for dep in f.get("dependencies", []):
            if dep.get("relationType") != 3:
                continue
            try:
                raw = curseforge.mod(key, dep["modId"])
                sub = curseforge.normalize_hit(raw, "mod")
                if sub["project_id"] not in modrinth.load_db(profile):
                    installed += _cf_install(sub, profile, key, rep, world, seen)["installed"]
            except Blocked as b:
                rep.log(f"⚠ Зависимость {dep['modId']}: автор запретил автоскачивание - {b.url}")
            except Exception as e:
                rep.log(f"⚠ Зависимость {dep['modId']}: {e}")
    return {"installed": installed}


def install_mrpack(path: Path, settings, rep: Reporter, display_name: str = "Modpack"):
    """Сборка Modrinth (.mrpack): новый профиль + загрузчик + все файлы + overrides."""
    with zipfile.ZipFile(path) as z:
        idx = json.loads(z.read("modrinth.index.json"))
        deps = idx.get("dependencies", {})
        mc = deps.get("minecraft")
        if not mc:
            raise ValueError("В сборке не указана версия Minecraft")
        loader, lver = "vanilla", None
        for k, l in (("fabric-loader", "fabric"), ("quilt-loader", "quilt"), ("neoforge", "neoforge"), ("forge", "forge")):
            if k in deps:
                loader, lver = l, deps[k]
                break
        name = _unique(profiles.PROFILES_DIR, idx.get("name") or display_name)
        rep.log(f"Создаю сборку «{name}»: {loader} {mc}")
        profile = profiles.create_profile(name, mc, loader, rep, int(settings.get("default_ram") or 4096), settings, lver)
        gdir = game_dir(profile)
        items = []
        for f in idx.get("files", []):
            if (f.get("env") or {}).get("client") == "unsupported" or not f.get("downloads"):
                continue
            items.append((f["downloads"][0], _safe_join(gdir, f["path"]), (f.get("hashes") or {}).get("sha1")))
        rep.log(f"Файлов в сборке: {len(items)}")
        download_many(items, rep, workers=8, label="файлов сборки")
        for member in z.namelist():
            for prefix in ("overrides/", "client-overrides/"):
                if member.startswith(prefix) and not member.endswith("/"):
                    target = _safe_join(gdir, member[len(prefix):])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(member))
    return profile


def install(hit: dict, profile: dict, settings, rep: Reporter = None, world: str = None) -> dict:
    """{'installed': [...], 'blocked': url|None, 'note': код|None, 'new_profile': имя|None}"""
    rep = rep or Reporter()
    kind, out = hit["kind"], {"installed": [], "blocked": None, "note": None, "new_profile": None}
    try:
        if hit["source"] == "modrinth":
            if kind == "mod":
                out["installed"] = modrinth.install_mod(profile, hit["project_id"], rep)
            elif kind == "modpack":
                ver = get_json(f"{modrinth.API}/project/{hit['project_id']}/version")[0]
                f = next((x for x in ver["files"] if x.get("primary")), ver["files"][0])
                pack = CACHE_DIR / "mrpack" / f["filename"]
                rep.log(f"Скачиваю сборку {hit['title']}...")
                download(f["url"], pack, f.get("hashes", {}).get("sha1"))
                out["new_profile"] = install_mrpack(pack, settings, rep, hit["title"])["name"]
                out["installed"] = [hit["title"]]
            else:
                out.update(_mr_install_file(hit, profile, rep, world))
        else:
            if kind == "modpack":
                out["blocked"] = hit.get("website")      # CurseForge-сборки требуют ручного разбора manifest.json
                out["note"] = "cf_modpack_manual"
            else:
                out.update(_cf_install(hit, profile, cf_key(settings), rep, world))
    except Blocked as b:
        out["blocked"] = b.url
    return out


# ============================================================= список / удаление
def _entries(folder: Path, prefix: str, label: str = "") -> list:
    if not folder.exists():
        return []
    return [{"title": f"{label}{p.name}", "rel": f"{prefix}/{p.name}"} for p in sorted(folder.iterdir()) if not p.name.startswith(".")]


def list_installed(profile: dict, kind: str) -> list:
    g = game_dir(profile)
    if kind == "mod":
        return [{"title": m["title"], "rel": f"mods/{m['file']}"} for m in modrinth.installed_mods(profile)]
    if kind in ("resourcepack", "shader", "plugin"):
        return _entries(g / DEST_DIR[kind], DEST_DIR[kind])
    if kind == "datapack" and profile.get("_dir"):
        return _entries(g / "world" / "datapacks", "world/datapacks")
    if kind == "datapack":
        out = _entries(g / "datapacks", "datapacks", "(профиль) ")
        for w in list_worlds(profile):
            out += _entries(g / "saves" / w / "datapacks", f"saves/{w}/datapacks", f"{w}: ")
        return out
    if kind == "world":
        return [{"title": w, "rel": f"saves/{w}"} for w in list_worlds(profile)]
    return []


def remove_project(profile: dict, project_id: str) -> bool:
    """Удаляет то, что поставлено из каталога (по записи учёта); для модов - jar, для остального - файл/папку."""
    e = modrinth.load_db(profile).get(project_id)
    if not e:
        return False
    kind = e.get("kind", "mod")
    rel = e.get("rel") or f"mods/{e['file']}"
    ok = remove_item(profile, kind, rel)
    if not ok:                                      # файла уже нет - просто чистим запись
        db = modrinth.load_db(profile)
        db.pop(project_id, None)
        modrinth._save_db(profile, db)
    return ok


def remove_item(profile: dict, kind: str, rel: str) -> bool:
    g = game_dir(profile).resolve()
    target = (g / rel).resolve()
    if g not in target.parents or target.relative_to(g).parts[0] not in ALLOWED_TOP or not target.exists():
        return False
    if kind == "mod":
        return modrinth.remove_mod(profile, target.name)
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    db = modrinth.load_db(profile)
    for k in [k for k, v in db.items() if v.get("rel") == rel]:
        db.pop(k)
    modrinth._save_db(profile, db)
    return True


# ==================================== поиск зависимости по mod id с проверкой jar
UNRESOLVED_TTL = 24 * 3600


def _unresolved_path(profile):
    return game_dir(profile) / "cd_unresolved.json"


def find_by_mod_id(mod_id: str, profile: dict, settings, rep: Reporter = None) -> list:
    """Находит мод по его mod id, даже если название проекта другое (tacz -> timeless-and-classics-zero).
    Ставится ТОЛЬКО проект, чей скачанный jar действительно содержит этот mod id (ничего не «угадывается»)."""
    rep = rep or Reporter()
    mid = mod_id.lower()
    if len(mid) < 3:
        return []
    up = _unresolved_path(profile)
    try:
        bad = json.loads(up.read_text("utf-8"))
    except Exception:
        bad = {}
    if time.time() - bad.get(mid, 0) < UNRESOLVED_TTL:
        return []
    mc, loader = profile["mc_version"], profile["loader"]
    cands = []                                             # (источник, hit, url, sha1)
    try:
        for h in modrinth.search(mod_id, mc, loader, None, 8):
            v = modrinth.find_version(h["project_id"], mc, loader)
            if v:
                f = next((x for x in v["files"] if x.get("primary")), v["files"][0])
                cands.append(("modrinth", _mr_norm(h, "mod"), f["url"], f.get("hashes", {}).get("sha1")))
    except Exception as e:
        rep.log(f"Modrinth: {e}")
    key = cf_key(settings)
    if key:
        try:
            for h in curseforge.search(key, "mod", mod_id, profile, 8, 0)["hits"]:
                f = curseforge.pick_file(curseforge.files(key, h["project_id"].split(":")[1], mc, loader, 20))
                if f and f.get("downloadUrl"):
                    cands.append(("curseforge", h, f["downloadUrl"], curseforge.sha1_of(f)))
        except Exception as e:
            rep.log(f"CurseForge: {e}")
    cands.sort(key=lambda c: mid not in _norm_title(c[1]["title"] + c[1]["slug"]))   # сначала похожие по названию
    for src, hit, url, sha1 in cands[:6]:
        tmp = CACHE_DIR / "verify" / f"{_norm_title(hit['slug'] or hit['title'])}-{abs(hash(url)) % 10**8}.jar"
        try:
            download(url, tmp, sha1)
            ids = {i.lower() for i in modrinth.read_jar_meta(tmp)["ids"] | modrinth.nested_ids(tmp)}
        except Exception:
            continue
        if mid in ids:
            rep.log(f"Нашёл «{mod_id}»: {hit['title']} ({src})")
            res = install(hit, profile, settings, rep)
            if res["installed"]:
                return res["installed"]
    bad[mid] = time.time()
    try:
        up.write_text(json.dumps(bad), "utf-8")
    except OSError:
        pass
    hint = "" if key else " Часть модов есть только на CurseForge: добавьте ключ API в Настройки -> Дополнительно."
    rep.log(f"⚠ «{mod_id}» не найден для {loader} {mc}.{hint}")
    return []
