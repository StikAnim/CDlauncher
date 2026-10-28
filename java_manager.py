"""Авто-определение версии Java и скачивание JRE (Eclipse Temurin / Adoptium)."""
import re
import shutil
import tarfile
import zipfile
from pathlib import Path

import requests

from .config import ARCH, CACHE_DIR, OS_NAME, RUNTIME_DIR
from .net import Reporter, UA

LTS = [8, 11, 17, 21, 25]


def required_major(vjson: dict, mc_version: str) -> int:
    """Нужная мажорная версия Java.
    Приоритет: поле javaVersion из json Mojang. Иначе по номеру версии:
    <=1.16 -> 8, 1.17-1.20.4 -> 17, 1.20.5+ -> 21."""
    jv = (vjson.get("javaVersion") or {}).get("majorVersion")
    if jv:
        return next((l for l in LTS if l >= int(jv)), int(jv))
    m = re.match(r"^1\.(\d+)(?:\.(\d+))?", mc_version or "")
    if not m:
        return 21
    minor, patch = int(m.group(1)), int(m.group(2) or 0)
    if minor <= 16:
        return 8
    if minor < 20 or (minor == 20 and patch < 5):
        return 17
    return 21


def _java_exe(root: Path):
    name = "java.exe" if OS_NAME == "windows" else "java"
    for p in root.rglob(f"bin/{name}"):
        return p
    return None


def find_installed(major: int):
    d = RUNTIME_DIR / f"java{major}"
    return _java_exe(d) if d.exists() else None



def extract_archive(archive: Path, target: Path):
    """Распаковывает zip или tar.gz, определяя формат по содержимому, а не по расширению/URL."""
    with open(archive, "rb") as f:
        head = f.read(4)
    if head[:2] == b"PK":
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    elif head[:2] == b"\x1f\x8b":
        with tarfile.open(archive, "r:gz") as t:
            try:
                t.extractall(target, filter="data")
            except TypeError:  # Python < 3.12
                t.extractall(target)
    else:
        raise RuntimeError("Сервер вернул не архив Java (возможно, ошибка сети или блокировка). "
                           "Проверьте интернет/антивирус и повторите")


def ensure_java(major: int, rep: Reporter = None, override: str = "") -> str:
    rep = rep or Reporter()
    if override and Path(override).exists():
        return override
    exe = find_installed(major)
    if exe:
        return str(exe)
    os_map = {"windows": "windows", "linux": "linux", "osx": "mac"}[OS_NAME]
    arch_map = {"x64": "x64", "aarch64": "aarch64", "x86": "x32"}[ARCH]
    url = (f"https://api.adoptium.net/v3/binary/latest/{major}/ga/{os_map}/{arch_map}"
           f"/jre/hotspot/normal/eclipse")
    rep.log(f"Скачиваю Java {major} (Adoptium)...")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, allow_redirects=True, headers=UA, timeout=60) as r:
        r.raise_for_status()
        archive = CACHE_DIR / f"java{major}.download"
        total = int(r.headers.get("content-length", 0))
        got = 0
        with open(archive, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                got += len(chunk)
                if total:
                    rep.progress(got, total)
    if total and got != total:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"Java скачалась не полностью ({got} из {total} байт), повторите попытку")
    target = RUNTIME_DIR / f"java{major}"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    rep.log("Распаковываю Java...")
    try:
        extract_archive(archive, target)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)  # не оставляем полураспакованную Java
        raise
    finally:
        archive.unlink(missing_ok=True)
    exe = _java_exe(target)
    if not exe:
        raise RuntimeError("В архиве Java не найден исполняемый файл")
    if OS_NAME != "windows":
        exe.chmod(0o755)
    rep.log(f"Java {major} готова")
    return str(exe)
