"""Каталог версий Mojang, установка клиента/библиотек/ассетов, слияние json загрузчиков."""
import json
import re
import shutil
import zipfile
from pathlib import Path

from .config import (ARCH, ASSETS_DIR, CACHE_DIR, LIBRARIES_DIR, OS_NAME, VERSIONS_DIR)
from .net import Reporter, download, download_many, get_json

MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
RESOURCES_URL = "https://resources.download.minecraft.net"


# ---------------------------------------------------------------- каталог ---
def fetch_manifest() -> dict:
    cache = CACHE_DIR / "version_manifest.json"
    try:
        data = get_json(MANIFEST_URL)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), "utf-8")
        return data
    except Exception:
        if cache.exists():  # работаем офлайн по кэшу
            return json.loads(cache.read_text("utf-8"))
        raise


def installed_versions() -> list:
    out = []
    if VERSIONS_DIR.exists():
        for d in VERSIONS_DIR.iterdir():
            if (d / f"{d.name}.json").exists():
                out.append(d.name)
    return sorted(out)


def list_versions(settings, manifest=None) -> list:
    """Список версий с учётом фильтров из настроек. Возвращает [{id,type}]."""
    manifest = manifest or fetch_manifest()
    have = set(installed_versions())
    result = []
    for v in manifest["versions"]:
        t = v["type"]
        if t in ("old_alpha", "old_beta") and not settings.get("show_old"):
            continue
        if t == "snapshot" and not settings.get("show_snapshots"):
            continue
        if settings.get("only_downloaded") and v["id"] not in have:
            continue
        result.append({"id": v["id"], "type": t})
    return result


# ------------------------------------------------------------------ правила ---
def _rule_matches(rule: dict) -> bool:
    if "features" in rule:  # demo / custom resolution и пр. не используем
        return False
    os_rule = rule.get("os")
    if os_rule:
        if "name" in os_rule and os_rule["name"] != OS_NAME:
            return False
        if os_rule.get("arch") == "x86" and ARCH != "x86":
            return False
    return True


def rules_allow(rules) -> bool:
    if not rules:
        return True
    allowed = False
    for r in rules:
        if _rule_matches(r):
            allowed = r.get("action") == "allow"
    return allowed


# --------------------------------------------------------------- библиотеки ---
def maven_path(name: str) -> str:
    ext = "jar"
    if "@" in name:
        name, ext = name.split("@", 1)
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    fname = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + f".{ext}"
    return f"{group.replace('.', '/')}/{artifact}/{version}/{fname}"


def lib_key(name: str) -> str:
    base = name.split("@")[0].split(":")
    key = f"{base[0]}:{base[1]}"
    if len(base) > 3:
        key += f":{base[3]}"
    return key


def collect_libraries(vjson: dict) -> list:
    """Возвращает [{name,path,url,sha1,extract}]. extract != None -> нативная библиотека."""
    out = []
    for lib in vjson.get("libraries", []):
        if not rules_allow(lib.get("rules")):
            continue
        name = lib["name"]
        dl = lib.get("downloads")
        if dl:
            art = dl.get("artifact")
            if art and art.get("path"):
                out.append({"name": name, "path": art["path"], "url": art.get("url", ""),
                            "sha1": art.get("sha1"), "extract": None})
            natives = lib.get("natives")
            if natives and OS_NAME in natives:
                cls = natives[OS_NAME].replace("${arch}", "32" if ARCH == "x86" else "64")
                a = dl.get("classifiers", {}).get(cls)
                if a:
                    out.append({"name": name + ":" + cls, "path": a["path"], "url": a["url"],
                                "sha1": a.get("sha1"), "extract": lib.get("extract") or {}})
        else:
            base = lib.get("url", "https://libraries.minecraft.net/")
            p = maven_path(name)
            out.append({"name": name, "path": p, "url": base.rstrip("/") + "/" + p,
                        "sha1": lib.get("sha1"), "extract": None})
    return out


# --------------------------------------------------------------- json версий ---
def version_json_path(version_id: str) -> Path:
    return VERSIONS_DIR / version_id / f"{version_id}.json"


def merge_versions(parent: dict, child: dict) -> dict:
    res = dict(parent)
    for k, v in child.items():
        if k in ("libraries", "arguments", "inheritsFrom"):
            continue
        res[k] = v
    child_libs = child.get("libraries", [])
    child_keys = {lib_key(l["name"]) for l in child_libs}
    res["libraries"] = child_libs + [l for l in parent.get("libraries", [])
                                     if lib_key(l["name"]) not in child_keys]
    pa, ca = parent.get("arguments", {}), child.get("arguments", {})
    args = {"game": pa.get("game", []) + ca.get("game", []),
            "jvm": pa.get("jvm", []) + ca.get("jvm", [])}
    if args["game"] or args["jvm"]:
        res["arguments"] = args
    if "minecraftArguments" in child:
        res["minecraftArguments"] = child["minecraftArguments"]
    res["_jar_id"] = parent.get("_jar_id", parent.get("id"))
    return res


def resolve(version_id: str) -> dict:
    """Читает json версии и сливает с родителем (inheritsFrom)."""
    j = json.loads(version_json_path(version_id).read_text("utf-8"))
    if "inheritsFrom" in j:
        return merge_versions(resolve(j["inheritsFrom"]), j)
    j["_jar_id"] = j.get("jar", version_id)
    return j


def fetch_version_json(version_id: str) -> dict:
    path = version_json_path(version_id)
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    manifest = fetch_manifest()
    for v in manifest["versions"]:
        if v["id"] == version_id:
            data = get_json(v["url"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), "utf-8")
            return data
    raise ValueError(f"Версия {version_id} не найдена в каталоге Mojang")


# ---------------------------------------------------------------- установка ---
def install_version(version_id: str, rep: Reporter = None) -> str:
    """Полностью устанавливает версию (клиент, библиотеки, ассеты)."""
    rep = rep or Reporter()
    raw = fetch_version_json(version_id)
    if "inheritsFrom" in raw:
        install_version(raw["inheritsFrom"], rep)
    vjson = resolve(version_id)

    jar_id = vjson["_jar_id"]
    client = vjson.get("downloads", {}).get("client")
    jar_path = VERSIONS_DIR / jar_id / f"{jar_id}.jar"
    if client and not (jar_path.exists() and jar_path.stat().st_size > 0):
        rep.log(f"Скачиваю клиент {jar_id}...")
        download(client["url"], jar_path, client.get("sha1"))

    libs = collect_libraries(vjson)
    rep.log(f"Библиотеки: {len(libs)}")
    download_many([(l["url"], LIBRARIES_DIR / l["path"], l["sha1"]) for l in libs], rep, label="библиотек")
    extract_natives(vjson, version_id)

    idx = vjson.get("assetIndex")
    if idx:
        install_assets(idx, rep)
    rep.log(f"Версия {version_id} установлена")
    return version_id


def extract_natives(vjson: dict, version_id: str):
    ndir = VERSIONS_DIR / version_id / "natives"
    ndir.mkdir(parents=True, exist_ok=True)
    for lib in collect_libraries(vjson):
        if lib["extract"] is None:
            continue
        jar = LIBRARIES_DIR / lib["path"]
        if not jar.exists():
            continue
        exclude = lib["extract"].get("exclude", [])
        with zipfile.ZipFile(jar) as z:
            for n in z.namelist():
                if n.endswith("/") or any(n.startswith(e) for e in exclude):
                    continue
                z.extract(n, ndir)


def install_assets(index_info: dict, rep: Reporter):
    idx_path = ASSETS_DIR / "indexes" / f"{index_info['id']}.json"
    download(index_info["url"], idx_path, index_info.get("sha1"))
    index = json.loads(idx_path.read_text("utf-8"))
    items = []
    for name, obj in index["objects"].items():
        h = obj["hash"]
        items.append((f"{RESOURCES_URL}/{h[:2]}/{h}", ASSETS_DIR / "objects" / h[:2] / h, h))
    rep.log(f"Ассеты: {len(items)}")
    download_many(items, rep, workers=32, label="ассетов")
    if index.get("virtual") or index.get("map_to_resources"):
        target = ASSETS_DIR / "virtual" / "legacy"
        for name, obj in index["objects"].items():
            src = ASSETS_DIR / "objects" / obj["hash"][:2] / obj["hash"]
            dst = target / name
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
