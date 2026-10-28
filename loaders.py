"""Установка модлоадеров: Fabric, Quilt (meta API), Forge, NeoForge (installer). Версии берутся из repos.py."""
import json
import subprocess

from . import java_manager, repos, versions
from .config import OS_NAME, ROOT
from .net import Reporter, download, get_json

LOADERS = ["vanilla", "fabric", "quilt", "forge", "neoforge"]
META = {"fabric": "https://meta.fabricmc.net/v2", "quilt": "https://meta.quiltmc.org/v3"}


class LoaderError(Exception):
    pass


def _meta_install(loader: str, mc: str, rep: Reporter, wanted=None) -> str:
    base = META[loader]
    lv = repos.pick(loader, mc, wanted) if wanted else None
    if lv is None:  # для конкретной версии Minecraft берём список, совместимый именно с ней
        loaders = get_json(f"{base}/versions/loader/{mc}", timeout=15)
        if not loaders:
            raise LoaderError(f"Для Minecraft {mc} нет загрузчика {loader}")
        stable = [l for l in loaders if l["loader"].get("stable", True)] or loaders
        lv = stable[0]["loader"]["version"]
    rep.log(f"{loader.capitalize()} {lv}")
    profile = get_json(f"{base}/versions/loader/{mc}/{lv}/profile/json", timeout=15)
    vid = profile["id"]
    p = versions.version_json_path(vid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profile), "utf-8")
    return vid


def _write_launcher_profiles():
    f = ROOT / "launcher_profiles.json"
    if not f.exists():
        f.write_text(json.dumps({"profiles": {}, "version": 3}), "utf-8")


def _run_installer(jar, mc: str, rep: Reporter, settings=None) -> str:
    _write_launcher_profiles()
    vjson = versions.resolve(mc)
    major = java_manager.required_major(vjson, mc)
    java = java_manager.ensure_java(major, rep, (settings.get("java_path") if settings else ""))
    before = set(versions.installed_versions())
    rep.log("Запускаю установщик загрузчика (это может занять пару минут)...")
    flags = 0x08000000 if OS_NAME == "windows" else 0
    proc = subprocess.run([java, "-jar", str(jar), "--installClient", str(ROOT)],
                          cwd=str(ROOT), capture_output=True, text=True, creationflags=flags)
    if proc.returncode != 0:
        raise LoaderError("Установщик завершился с ошибкой:\n" + (proc.stdout + proc.stderr)[-1500:])
    new = [v for v in versions.installed_versions() if v not in before]
    if not new:
        raise LoaderError("Установщик не создал новую версию")
    return sorted(new, key=len)[-1]


def _forge(mc: str, rep: Reporter, settings, wanted=None) -> str:
    ver = repos.pick("forge", mc, wanted)
    full = f"{mc}-{ver}"
    url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{full}/forge-{full}-installer.jar"
    jar = ROOT / "cache" / f"forge-{full}-installer.jar"
    rep.log(f"Forge {full}")
    download(url, jar)
    return _run_installer(jar, mc, rep, settings)


def _neoforge(mc: str, rep: Reporter, settings, wanted=None) -> str:
    ver = repos.pick("neoforge", mc, wanted)
    if mc == "1.20.1":
        group, artifact = "net/neoforged/forge", "forge"
    else:
        group, artifact = "net/neoforged/neoforge", "neoforge"
    url = f"https://maven.neoforged.net/releases/{group}/{ver}/{artifact}-{ver}-installer.jar"
    jar = ROOT / "cache" / f"neoforge-{ver}-installer.jar"
    rep.log(f"NeoForge {ver}")
    download(url, jar)
    return _run_installer(jar, mc, rep, settings)


def install(loader: str, mc: str, rep: Reporter = None, settings=None, loader_version: str = None) -> str:
    """Ставит ваниль + модлоадер. loader_version=None -> самая свежая рекомендованная версия ПРЯМО СЕЙЧАС."""
    rep = rep or Reporter()
    loader = loader.lower()
    versions.install_version(mc, rep)
    if loader == "vanilla":
        return mc
    if loader in META:
        vid = _meta_install(loader, mc, rep, loader_version)
    elif loader == "forge":
        vid = _forge(mc, rep, settings, loader_version)
    elif loader == "neoforge":
        vid = _neoforge(mc, rep, settings, loader_version)
    else:
        raise LoaderError(f"Неизвестный загрузчик: {loader}")
    versions.install_version(vid, rep)
    return vid
