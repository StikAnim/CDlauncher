"""Клиент CurseForge API v1 (нужен бесплатный ключ https://console.curseforge.com , заголовок x-api-key)."""
from .net import get_json

BASE = "https://api.curseforge.com/v1"
GAME_ID = 432                                   # Minecraft
CLASS_ID = {"mod": 6, "resourcepack": 12, "world": 17, "shader": 6552, "datapack": 6945, "modpack": 4471}
LOADER_TYPE = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
URL_SECTION = {"mod": "mc-mods", "resourcepack": "texture-packs", "world": "worlds", "shader": "shaders",
               "datapack": "data-packs", "modpack": "modpacks"}


class CFError(Exception):
    pass


def _get(path: str, key: str, params: dict = None) -> dict:
    if not key:
        raise CFError("no_key")
    try:
        return get_json(BASE + path, params=params, headers={"x-api-key": key, "Accept": "application/json"}, timeout=20)
    except Exception as e:
        if "403" in str(e) or "401" in str(e):
            raise CFError("Неверный ключ CurseForge API (403)")
        raise


def normalize_hit(raw: dict, kind: str) -> dict:
    authors = raw.get("authors") or []
    logo = raw.get("logo") or {}
    return {"project_id": f"cf:{raw['id']}", "source": "curseforge", "kind": kind, "slug": raw.get("slug", ""),
            "title": raw.get("name", "?"), "author": authors[0].get("name", "") if authors else "",
            "description": raw.get("summary", ""), "downloads": raw.get("downloadCount", 0), "follows": raw.get("thumbsUpCount", 0),
            "icon_url": logo.get("thumbnailUrl") or logo.get("url"),
            "display_categories": [c.get("name", "") for c in (raw.get("categories") or [])][:4],
            "website": (raw.get("links") or {}).get("websiteUrl") or
                       f"https://www.curseforge.com/minecraft/{URL_SECTION[kind]}/{raw.get('slug', '')}"}


def search(key: str, kind: str, query: str, profile: dict, limit: int, offset: int) -> dict:
    params = {"gameId": GAME_ID, "classId": CLASS_ID[kind], "searchFilter": query or "", "pageSize": min(limit, 50),
              "index": offset, "sortField": 2, "sortOrder": "desc"}
    if kind in ("mod", "datapack", "resourcepack", "shader"):
        params["gameVersion"] = profile["mc_version"]
    if kind == "mod" and profile["loader"] in LOADER_TYPE:
        params["modLoaderType"] = LOADER_TYPE[profile["loader"]]
    data = _get("/mods/search", key, params)
    pg = data.get("pagination") or {}
    return {"hits": [normalize_hit(r, kind) for r in data.get("data", [])], "total": int(pg.get("totalCount", 0))}


def mod(key: str, mod_id) -> dict:
    return _get(f"/mods/{mod_id}", key)["data"]


def description_html(key: str, mod_id) -> str:
    return _get(f"/mods/{mod_id}/description", key).get("data", "")


def files(key: str, mod_id, mc: str = None, loader: str = None, limit: int = 30) -> list:
    params = {"pageSize": limit}
    if mc:
        params["gameVersion"] = mc
    if loader in LOADER_TYPE:
        params["modLoaderType"] = LOADER_TYPE[loader]
    return _get(f"/mods/{mod_id}/files", key, params).get("data", [])


def pick_file(file_list: list):
    """Лучший файл: сначала релизы (releaseType 1), среди них самый свежий."""
    if not file_list:
        return None
    rel = [f for f in file_list if f.get("releaseType") == 1] or file_list
    return sorted(rel, key=lambda f: f.get("fileDate", ""), reverse=True)[0]


def sha1_of(file_info: dict):
    for h in file_info.get("hashes", []):
        if h.get("algo") == 1:
            return h.get("value")
    return None
