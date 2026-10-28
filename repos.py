"""Живые списки версий загрузчиков напрямую из первичных источников + кэш.

  Forge     files.minecraftforge.net/.../promotions_slim.json  и  maven-metadata.xml
  NeoForge  maven.neoforged.net (maven-metadata.xml)
  Fabric    meta.fabricmc.net/v2/versions
  Quilt     meta.quiltmc.org/v3 (v2 как запасной)
Каждый запрос идёт в сеть в момент вызова (при установке версии кэш НЕ используется как первый источник);
кэш на диске нужен только как запасной вариант без интернета и для мгновенного показа списка в интерфейсе.
refresh_async() запускается при каждом старте лаунчера и обновляет все списки параллельно в фоне.
"""
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .config import CACHE_DIR
from .net import get_json, get_text

FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_META = "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
NEO_META = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
NEO_LEGACY_META = "https://maven.neoforged.net/releases/net/neoforged/forge/maven-metadata.xml"
FABRIC_LOADERS = "https://meta.fabricmc.net/v2/versions/loader"
FABRIC_GAME = "https://meta.fabricmc.net/v2/versions/game"
QUILT_LOADERS = ["https://meta.quiltmc.org/v3/versions/loader", "https://meta.quiltmc.org/v2/versions/loader"]
TIMEOUT = 10

_DIR = CACHE_DIR / "repos"
_lock = threading.Lock()


def _cache_path(name):
    return _DIR / f"{name}.json"


def cache_read(name):
    try:
        return json.loads(_cache_path(name).read_text("utf-8"))["data"]
    except Exception:
        return None


def cache_write(name, data):
    with _lock:
        _DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(name).write_text(json.dumps({"ts": time.time(), "data": data}), "utf-8")


def live(name, producer):
    """Свежие данные из сети; при ошибке - последний кэш; если и его нет - исключение."""
    try:
        data = producer()
        cache_write(name, data)
        return data
    except Exception:
        cached = cache_read(name)
        if cached is None:
            raise
        return cached


def _maven_versions(url):
    root = ET.fromstring(get_text(url, timeout=TIMEOUT))
    return [v.text for v in root.iter("version") if v.text]


# ---------------------------------------------------------------- первичные источники
def forge_promos():
    return live("forge_promos", lambda: get_json(FORGE_PROMOS, timeout=TIMEOUT)["promos"])


def forge_all():
    return live("forge_meta", lambda: _maven_versions(FORGE_META))


def neoforge_all(legacy=False):
    name, url = ("neoforge_legacy", NEO_LEGACY_META) if legacy else ("neoforge_meta", NEO_META)
    return live(name, lambda: _maven_versions(url))


def fabric_loaders():
    return live("fabric_loaders", lambda: get_json(FABRIC_LOADERS, timeout=TIMEOUT))


def fabric_game():
    return live("fabric_game", lambda: get_json(FABRIC_GAME, timeout=TIMEOUT))


def quilt_loaders():
    def produce():
        last = None
        for url in QUILT_LOADERS:
            try:
                return get_json(url, timeout=TIMEOUT)
            except Exception as e:
                last = e
        raise last
    return live("quilt_loaders", produce)


# --------------------------------------------------------------------- удобные списки
def _is_stable(v: str) -> bool:
    return not re.search(r"alpha|beta|rc|snapshot|pre", v, re.I)


def loader_versions(loader: str, mc: str) -> list:
    """[{'id': версия, 'tag': 'recommended'|'latest'|'stable'|''}] - новые сверху."""
    loader = loader.lower()
    out = []
    if loader == "forge":
        promos = forge_promos()
        rec, lat = promos.get(f"{mc}-recommended"), promos.get(f"{mc}-latest")
        seen = []
        for full in forge_all():
            if full.startswith(mc + "-"):
                seen.append(full[len(mc) + 1:])
        for v in reversed(seen):
            out.append({"id": v, "tag": "recommended" if v == rec else "latest" if v == lat else ""})
        if not out and (rec or lat):                       # метаданных нет, но промо есть
            out = [{"id": v, "tag": t} for v, t in ((lat, "latest"), (rec, "recommended")) if v]
    elif loader == "neoforge":
        if mc == "1.20.1":
            prefix, vers = "1.20.1-", neoforge_all(legacy=True)
        else:
            m = re.match(r"^1\.(\d+)(?:\.(\d+))?$", mc)
            if not m:
                return []
            prefix, vers = f"{m.group(1)}.{m.group(2) or 0}.", neoforge_all()
        match = [v for v in vers if v.startswith(prefix)]
        stable = [v for v in match if _is_stable(v)]
        for v in reversed(match):
            tag = "latest" if v == match[-1] else ""
            if stable and v == stable[-1]:
                tag = "recommended"
            out.append({"id": v, "tag": tag})
    elif loader == "fabric":
        for i, l in enumerate(fabric_loaders()):
            out.append({"id": l["version"], "tag": "stable" if l.get("stable") else ""})
    elif loader == "quilt":
        for l in quilt_loaders():
            out.append({"id": l["version"], "tag": ""})
    return out


def pick(loader: str, mc: str, wanted: str = None) -> str:
    """Какую версию ставить: выбранную вручную, иначе рекомендованную/стабильную/последнюю."""
    vs = loader_versions(loader, mc)
    if not vs:
        raise LookupError(f"{loader}: для Minecraft {mc} версий не найдено")
    if wanted:
        if any(v["id"] == wanted for v in vs):
            return wanted
        raise LookupError(f"{loader} {wanted} для {mc} не найден")
    for tag in ("recommended", "stable"):
        for v in vs:
            if v["tag"] == tag:
                return v["id"]
    return vs[0]["id"]


def refresh_all() -> dict:
    """Параллельно обновляет все источники. Возвращает {имя: секунды | 'ошибка'}."""
    jobs = {"forge_promos": forge_promos, "forge_meta": forge_all, "neoforge": neoforge_all,
            "neoforge_legacy": lambda: neoforge_all(True), "fabric_loaders": fabric_loaders,
            "fabric_game": fabric_game, "quilt_loaders": quilt_loaders}
    result = {}

    def run(item):
        name, fn = item
        t = time.time()
        try:
            fn()
            result[name] = round(time.time() - t, 2)
        except Exception as e:
            result[name] = f"error: {e}"

    with ThreadPoolExecutor(max_workers=7) as pool:
        list(pool.map(run, jobs.items()))
    return result


def refresh_async(callback=None):
    def work():
        res = refresh_all()
        if callback:
            callback(res)
    threading.Thread(target=work, daemon=True).start()
