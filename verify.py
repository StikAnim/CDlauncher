"""Проверка и ремонт файлов: клиентский jar, библиотеки, ассеты, Java, jar-файлы модов.
Недостающее и повреждённое докачивается; результат - структурированный отчёт."""
import json
import shutil
import time
import zipfile
from pathlib import Path

from . import java_manager, modrinth, versions
from .config import ASSETS_DIR, LIBRARIES_DIR, VERSIONS_DIR
from .net import Reporter, download, download_many, sha1_of
from .profiles import game_dir


class VerifyReport:
    def __init__(self):
        self.checked = 0
        self.bad = []            # описания проблем
        self.fixed = 0
        self.unfixable = []

    def problem(self, text):
        self.bad.append(text)

    def summary(self) -> str:
        return (f"Проверено: {self.checked}, проблем: {len(self.bad)}, исправлено: {self.fixed}, "
                f"не удалось исправить: {len(self.unfixable)}")


def _file_ok(path: Path, sha1: str = None, size: int = None) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    if size is not None and path.stat().st_size != size:
        return False
    return sha1 is None or sha1_of(path) == sha1


def check_version(version_id: str, rep: Reporter = None, repair: bool = True, deep: bool = False) -> VerifyReport:
    """deep=True - дополнительно сверяет SHA1 всех файлов ассетов (долго); по умолчанию - наличие и размер."""
    rep, r = rep or Reporter(), VerifyReport()
    json_path = versions.version_json_path(version_id)
    if not json_path.exists():
        r.problem(f"Нет описания версии {version_id}")
        if repair:
            try:
                versions.install_version(version_id, rep)
                r.fixed += 1
            except Exception as e:
                r.unfixable.append(f"{version_id}: {e}")
        return r
    raw = json.loads(json_path.read_text("utf-8"))
    if "inheritsFrom" in raw:
        sub = check_version(raw["inheritsFrom"], rep, repair, deep)
        r.checked += sub.checked
        r.bad += sub.bad
        r.fixed += sub.fixed
        r.unfixable += sub.unfixable
    vjson = versions.resolve(version_id)
    todo = []                                                        # (url, путь, sha1, описание)

    # --- клиентский jar
    client = (vjson.get("downloads") or {}).get("client")
    jar_id = vjson["_jar_id"]
    if client and "inheritsFrom" not in raw:
        jar = VERSIONS_DIR / jar_id / f"{jar_id}.jar"
        r.checked += 1
        if not _file_ok(jar, client.get("sha1"), client.get("size")):
            r.problem(f"Клиент {jar_id}: повреждён или отсутствует")
            todo.append((client["url"], jar, client.get("sha1"), f"клиент {jar_id}"))

    # --- библиотеки
    libs = versions.collect_libraries(vjson)
    for lib in libs:
        r.checked += 1
        path = LIBRARIES_DIR / lib["path"]
        if not _file_ok(path, lib.get("sha1")):
            r.problem(f"Библиотека {lib['name']}")
            if lib["url"]:
                todo.append((lib["url"], path, lib.get("sha1"), lib["name"]))
            else:
                r.unfixable.append(f"{lib['name']}: нет адреса (её создаёт установщик загрузчика)")

    # --- ассеты
    idx = vjson.get("assetIndex")
    if idx:
        idx_path = ASSETS_DIR / "indexes" / f"{idx['id']}.json"
        r.checked += 1
        if not _file_ok(idx_path, idx.get("sha1")):
            r.problem(f"Индекс ассетов {idx['id']}")
            if repair:
                try:
                    download(idx["url"], idx_path, idx.get("sha1"))
                except Exception as e:
                    r.unfixable.append(f"индекс ассетов: {e}")
        if idx_path.exists():
            index = json.loads(idx_path.read_text("utf-8"))
            for name, obj in index["objects"].items():
                h = obj["hash"]
                path = ASSETS_DIR / "objects" / h[:2] / h
                r.checked += 1
                if not _file_ok(path, h if deep else None, obj.get("size")):
                    r.problem(f"Ассет {name}")
                    todo.append((f"{versions.RESOURCES_URL}/{h[:2]}/{h}", path, h, name))

    # --- ремонт
    if repair and todo:
        rep.log(f"Докачиваю файлов: {len(todo)}")
        for _, path, _, _ in todo:
            if path.exists():
                path.unlink()                                      # битый файл удаляем, чтобы скачать начисто
        try:
            download_many([(u, p, s) for u, p, s, _ in todo], rep, workers=16, label="файлов")
            r.fixed += len(todo)
        except Exception as e:
            r.unfixable.append(f"докачка: {e}")
    if repair:
        try:
            versions.extract_natives(vjson, version_id)
        except Exception as e:
            r.unfixable.append(f"natives: {e}")
    return r


def check_java(vjson: dict, mc: str, rep: Reporter, repair: bool = True) -> VerifyReport:
    r = VerifyReport()
    major = java_manager.required_major(vjson, mc)
    r.checked += 1
    if java_manager.find_installed(major) is None:
        r.problem(f"Java {major} не скачана")
        if repair:
            try:
                java_manager.ensure_java(major, rep)
                r.fixed += 1
            except Exception as e:
                r.unfixable.append(f"Java {major}: {e}")
    return r


def check_mods(profile: dict, rep: Reporter = None, repair: bool = True) -> VerifyReport:
    """Повреждённые jar: если мод ставился с Modrinth - скачивается заново, иначе остаётся в отчёте."""
    rep, r = rep or Reporter(), VerifyReport()
    mods = game_dir(profile) / "mods"
    db = modrinth.load_db(profile)
    by_file = {v["file"]: k for k, v in db.items()}
    for jar in sorted(mods.glob("*.jar")) if mods.exists() else []:
        r.checked += 1
        try:
            with zipfile.ZipFile(jar) as z:
                z.namelist()
            continue
        except (zipfile.BadZipFile, OSError):
            pass
        r.problem(f"Повреждён мод {jar.name}")
        pid = by_file.get(jar.name)
        if repair and pid and not str(pid).startswith("cf:"):
            try:
                jar.unlink()
                modrinth.install_mod(profile, pid, rep)
                r.fixed += 1
                continue
            except Exception as e:
                r.unfixable.append(f"{jar.name}: {e}")
        else:
            r.unfixable.append(f"{jar.name}: удалите или скачайте заново вручную")
    return r


def check_profile(profile: dict, rep: Reporter = None, repair: bool = True, deep: bool = False) -> VerifyReport:
    rep, total = rep or Reporter(), VerifyReport()
    parts = [check_version(profile["version_id"], rep, repair, deep)]
    try:
        parts.append(check_java(versions.resolve(profile["version_id"]), profile["mc_version"], rep, repair))
    except Exception as e:
        total.unfixable.append(f"Java: {e}")
    parts.append(check_mods(profile, rep, repair))
    for p in parts:
        total.checked += p.checked
        total.bad += p.bad
        total.fixed += p.fixed
        total.unfixable += p.unfixable
    return total


def check_all_versions(rep: Reporter = None, repair: bool = True, deep: bool = False) -> VerifyReport:
    rep, total = rep or Reporter(), VerifyReport()
    vs = versions.installed_versions()
    for i, v in enumerate(vs, 1):
        rep.log(f"Проверка {v} ({i}/{len(vs)})")
        p = check_version(v, rep, repair, deep)
        total.checked += p.checked
        total.bad += p.bad
        total.fixed += p.fixed
        total.unfixable += p.unfixable
    return total
