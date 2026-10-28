"""Сборка командной строки и запуск Minecraft (включая authlib-injector для Ely.by)."""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import java_manager, versions
from .config import (APP_NAME, APP_VERSION, ASSETS_DIR, AUTHLIB_JAR, LIBRARIES_DIR, OS_NAME,
                     VERSIONS_DIR)
from .net import Reporter, download, get_json
from .p2p import parse_lan_port
from .profiles import game_dir

AUTHLIB_META = ["https://authlib-injector.yushi.moe/artifact/latest.json",
                "https://bmclapi2.bangbang93.com/mirrors/authlib-injector/artifact/latest.json"]


def ensure_authlib_injector(rep: Reporter) -> Path:
    if AUTHLIB_JAR.exists() and AUTHLIB_JAR.stat().st_size > 0:
        return AUTHLIB_JAR
    rep.log("Скачиваю authlib-injector...")
    last = None
    for meta in AUTHLIB_META:
        try:
            info = get_json(meta)
            download(info["download_url"], AUTHLIB_JAR)
            return AUTHLIB_JAR
        except Exception as e:
            last = e
    raise RuntimeError(f"Не удалось скачать authlib-injector: {last}")


def main_client_jar(vjson: dict, vid: str):
    """Клиентский jar для classpath.

    У Forge/NeoForge 1.17+ в параметрах JVM стоит -DignoreList=...,${version_name}.jar - так загрузчик узнаёт,
    какой jar убрать из модульного пути (иначе два модуля «minecraft» и ResolutionException). Поэтому jar
    должен называться ТАК ЖЕ, как запускаемая версия: versions/<id>/<id>.jar (как делают официальный лаунчер и HMCL).
    Для версий без inheritsFrom это тот же файл. Копия делается один раз (жёсткая ссылка, иначе копирование)."""
    jar_id = vjson["_jar_id"]
    base = VERSIONS_DIR / jar_id / f"{jar_id}.jar"
    if vid == jar_id:
        return base
    child = VERSIONS_DIR / vid / f"{vid}.jar"
    if base.exists() and (not child.exists() or child.stat().st_size != base.stat().st_size):
        child.parent.mkdir(parents=True, exist_ok=True)
        if child.exists():
            child.unlink()
        try:
            os.link(base, child)
        except OSError:
            shutil.copyfile(base, child)
    return child if child.exists() else base


def gc_flags(major: int) -> list:
    """Тюнинг сборщика мусора для больших сборок (меньше фризов, меньше риска OOM)."""
    base = ["-XX:+UseG1GC", "-XX:+UnlockExperimentalVMOptions", "-XX:+DisableExplicitGC", "-XX:MaxGCPauseMillis=200"]
    if major < 11:
        return base
    return base + ["-XX:+ParallelRefProcEnabled", "-XX:G1NewSizePercent=30", "-XX:G1MaxNewSizePercent=40",
                   "-XX:G1HeapRegionSize=8M", "-XX:G1ReservePercent=20", "-XX:G1HeapWastePercent=5",
                   "-XX:G1MixedGCCountTarget=4", "-XX:InitiatingHeapOccupancyPercent=15",
                   "-XX:G1MixedGCLiveThresholdPercent=90", "-XX:G1RSetUpdatingPauseTimePercent=5",
                   "-XX:SurvivorRatio=32", "-XX:+PerfDisableSharedMem", "-XX:MaxTenuringThreshold=1"]


def _argfile_quote(a: str) -> str:
    return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"'


_ARG_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')


def split_args(text: str) -> list:
    """Разбор пользовательской строки аргументов: пробелы разделяют, "кавычки" группируют.
    Обратные слэши НЕ экранируют (пути Windows остаются как есть). Пустая строка -> []."""
    out = []
    for m in _ARG_RE.finditer(text or ""):
        out.append(next(g for g in m.groups() if g is not None))
    return out


def _expand(template: str, values: dict) -> str:
    return re.sub(r"\$\{(\w+)\}", lambda m: str(values.get(m.group(1), "")), template)


def _process_args(items, values) -> list:
    out = []
    for it in items:
        if isinstance(it, str):
            out.append(_expand(it, values))
        elif isinstance(it, dict) and versions.rules_allow(it.get("rules")):
            val = it.get("value", [])
            for v in ([val] if isinstance(val, str) else val):
                out.append(_expand(v, values))
    return out


def build_command(profile: dict, account: dict, settings, rep: Reporter = None, extra_args=None, custom: bool = True) -> tuple:
    """Возвращает (команда, рабочая папка). Всё нужное докачивается автоматически."""
    rep = rep or Reporter()
    vid = profile["version_id"]
    versions.install_version(vid, rep)  # докачает недостающее
    vjson = versions.resolve(vid)

    major = profile.get("java_major") or java_manager.required_major(vjson, profile["mc_version"])
    java = java_manager.ensure_java(int(major), rep, settings.get("java_path"))

    gdir = game_dir(profile)
    gdir.mkdir(parents=True, exist_ok=True)
    natives = VERSIONS_DIR / vid / "natives"
    versions.extract_natives(vjson, vid)

    cp, seen = [], set()
    for lib in versions.collect_libraries(vjson):
        if lib["extract"] is not None:
            continue
        p = str(LIBRARIES_DIR / lib["path"])
        if p not in seen and Path(p).exists():
            seen.add(p)
            cp.append(p)
    cp.append(str(main_client_jar(vjson, vid)))
    sep = ";" if OS_NAME == "windows" else ":"

    assets_index = (vjson.get("assetIndex") or {}).get("id", "legacy")
    game_assets = str(ASSETS_DIR / "virtual" / "legacy") if assets_index in ("legacy", "pre-1.6") else str(ASSETS_DIR)
    values = {
        "auth_player_name": account["name"], "version_name": vid, "game_directory": str(gdir),
        "assets_root": str(ASSETS_DIR), "assets_index_name": assets_index, "game_assets": game_assets,
        "auth_uuid": account["uuid"], "auth_access_token": account["access_token"],
        "auth_session": account["access_token"], "clientid": "", "auth_xuid": "",
        "user_type": "msa" if account["type"] == "microsoft" else "mojang", "user_properties": "{}",
        "version_type": vjson.get("type", "release"), "natives_directory": str(natives),
        "launcher_name": APP_NAME.replace(" ", ""), "launcher_version": APP_VERSION,
        "classpath": sep.join(cp), "library_directory": str(LIBRARIES_DIR),
        "classpath_separator": sep, "resolution_width": "854", "resolution_height": "480",
    }

    if "arguments" in vjson:
        jvm = _process_args(vjson["arguments"].get("jvm", []), values)
        game = _process_args(vjson["arguments"].get("game", []), values)
    else:
        jvm = [f"-Djava.library.path={natives}", "-cp", sep.join(cp)]
        game = [_expand(a, values) for a in vjson.get("minecraftArguments", "").split()]
    if "-cp" not in jvm and "-classpath" not in jvm:
        jvm += ["-cp", sep.join(cp)]

    ram = int(profile.get("ram_mb") or settings.get("default_ram"))
    pre = [f"-Xmx{ram}M", f"-Xms{min(2048, ram // 2)}M"] + gc_flags(int(major))
    if OS_NAME == "osx":
        pre.append("-XstartOnFirstThread")
    if account["type"] == "ely":
        jar = ensure_authlib_injector(rep)
        pre.insert(0, f"-javaagent:{jar}=ely.by")  # скины и плащи Ely.by везде
    custom_jvm = custom_game = []
    if custom:  # свои параметры: после стандартных, поэтому они их переопределяют (например, -Xmx)
        custom_jvm = split_args(settings.get("default_jvm_args") or "") + split_args(profile.get("jvm_args", ""))
        custom_game = split_args(settings.get("default_game_args") or "") + split_args(profile.get("game_args", ""))
    cmd = [java] + pre + jvm + custom_jvm + [vjson["mainClass"]] + game + custom_game + list(extra_args or [])
    if int(major) >= 9 and sum(len(a) + 1 for a in cmd) > 28000:
        # Очень длинная командная строка (большая сборка / глубокий путь) упирается в лимит Windows -
        # передаём аргументы через @argfile (поддерживается Java 9+).
        argfile = gdir / "cd_args.txt"
        argfile.write_text("\n".join(_argfile_quote(a) for a in cmd[1:]), "utf-8")
        cmd = [java, f"@{argfile}"]
    return cmd, gdir


BAD_ARGS_RE = re.compile(r"Unrecognized (VM )?option|Improperly specified VM option|Invalid (maximum|initial) heap size|"
                         r"Could not create the Java Virtual Machine|Could not find or load main class|"
                         r"UnrecognizedOptionException|OptionException|Unknown option", re.I)


def _run_once(cmd, gdir, rep, on_lan_port, mode="w") -> tuple:
    """Один запуск: вывод идёт в интерфейс и в logs/cd_last_run.log. Возвращает (код, секунд, весь вывод)."""
    flags = 0x08000000 if OS_NAME == "windows" else 0
    log_dir = gdir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    started, lines = time.time(), []
    with open(log_dir / "cd_last_run.log", mode, encoding="utf-8", errors="replace") as out:
        out.write("CMD: " + " ".join(cmd[:1] + cmd[1:6]) + " ...\n")
        proc = subprocess.Popen(cmd, cwd=str(gdir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", creationflags=flags)
        for line in proc.stdout:
            line = line.rstrip()
            out.write(line + "\n")
            out.flush()
            lines.append(line)
            if len(lines) > 600:
                del lines[:100]
            rep.log(line)
            if on_lan_port:
                port = parse_lan_port(line)
                if port:
                    on_lan_port(port)
        code = proc.wait()
    return code, time.time() - started, "\n".join(lines)


def run_game(profile: dict, account: dict, settings, rep: Reporter = None, on_lan_port=None, extra_args=None) -> int:
    rep = rep or Reporter()
    try:  # проверка сборки не должна мешать запуску, даже если сама упадёт
        from . import preflight
        preflight.run(profile, settings, rep)
    except Exception as e:
        rep.log(f"preflight: {e}")
    try:
        from . import skins
        skins.apply_local_skin(profile, account, rep)
    except Exception as e:
        rep.log(f"skins: {e}")

    has_custom = bool(split_args(profile.get("jvm_args", "")) or split_args(profile.get("game_args", ""))
                      or split_args(settings.get("default_jvm_args") or "") or split_args(settings.get("default_game_args") or ""))
    cmd, gdir = build_command(profile, account, settings, rep, extra_args, custom=True)
    rep.log("Запуск Minecraft...")
    code, secs, output = _run_once(cmd, gdir, rep, on_lan_port)
    if code != 0 and has_custom and secs < 25 and BAD_ARGS_RE.search(output):
        # Ваши параметры мешают JVM стартовать - в любом случае запускаем без них
        rep.log("⚠ Игра не стартовала с вашими параметрами запуска - повторяю без них")
        cmd, gdir = build_command(profile, account, settings, rep, extra_args, custom=False)
        with open(gdir / "logs" / "cd_last_run.log", "a", encoding="utf-8") as f:
            f.write("\n=== retry without custom launch arguments ===\n")
        code, secs, output = _run_once(cmd, gdir, rep, on_lan_port, mode="a")
    rep.log(f"Игра завершилась с кодом {code} через {int(secs)} с")
    return code
