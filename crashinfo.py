"""Разбор вылета: понятная причина даже без ИИ + сохранение полного лога запуска."""
import re

from . import ai, sysinfo
from .i18n import tr
from .profiles import game_dir

LOG_NAME = "cd_last_run.log"

# Коды завершения Windows (приведены к беззнаковому виду)
WIN_CODES = {0xC0000005: "gpu", 0xC0000409: "gpu", 0xC000001D: "gpu", 0xC0000374: "gpu"}


def log_path(profile):
    return game_dir(profile) / "logs" / LOG_NAME


def read_tail(path, lines=400) -> str:
    try:
        return "\n".join(path.read_text("utf-8", "replace").splitlines()[-lines:])
    except OSError:
        return ""


def context(profile) -> str:
    gdir = game_dir(profile)
    parts = [read_tail(log_path(profile))]
    crashes = sorted((gdir / "crash-reports").glob("crash-*.txt"), key=lambda p: p.stat().st_mtime) \
        if (gdir / "crash-reports").exists() else []
    if crashes:
        parts.append(crashes[-1].read_text("utf-8", "replace")[:6000])
    return "\n".join(p for p in parts if p)


ERROR_RE = re.compile(r"(Exception|Error|FATAL|Caused by|Unrecognized VM option|Could not|Invalid|cannot be|Unsupported|"
                      r"Mixin apply failed|Failed to)", re.I)
KNOWN_BAD = ("customskinloader",)       # моды, которые лаунчер умеет опознать как причину вылета


def error_lines(ctx: str, limit: int = 4) -> list:
    """Первые осмысленные строки ошибок из лога (для случаев, когда причина не опознана правилами)."""
    out = []
    for line in ctx.splitlines():
        t = line.strip()
        if t and ERROR_RE.search(t) and not t.startswith("at ") and t not in out and "[main/INFO]" not in t:
            out.append(t[:200])
            if len(out) >= limit:
                break
    return out


def known_bad_mods(profile: dict, ctx: str) -> list:
    """Какие из известных проблемных модов лежат в сборке И упомянуты в логе вылета."""
    mods = game_dir(profile) / "mods"
    found = []
    low = ctx.lower()
    for jar in (mods.glob("*.jar") if mods.exists() else []):
        n = jar.name.lower().replace("_", "").replace("-", "")
        if any(b in n for b in KNOWN_BAD) and any(b in low for b in KNOWN_BAD):
            found.append(jar.name)
    return found


def quarantine_known_bad(profile: dict) -> list:
    from . import modrinth
    names = []
    for jar in known_bad_mods(profile, context(profile)):
        if modrinth.quarantine_mod(profile, jar):
            names.append(jar)
    return names


def report_text(profile: dict, code: int) -> str:
    """Текст для отправки разработчику: итог, версии, конец лога."""
    ctx = read_tail(log_path(profile), 80)
    head = f"CD Launcher | {profile['name']} | {profile['loader']} {profile['mc_version']} | RAM {profile.get('ram_mb')} | exit {code}"
    return head + "\n" + "\n".join(summarize(profile, code)) + "\n---\n" + ctx


def summarize(profile: dict, code: int) -> list:
    """Список строк «что случилось»; первая строка - краткий итог."""
    lines = [tr("crash_exit", code=code)]
    ctx = context(profile)
    ucode = code & 0xFFFFFFFF
    if WIN_CODES.get(ucode) == "gpu":
        lines.append(tr("crash_gpu", code=hex(ucode)))
    if re.search(r"Could not reserve enough space|Invalid maximum heap size|Could not create the Java Virtual Machine", ctx):
        lines.append(tr("crash_heap", n=profile.get("ram_mb"), t=sysinfo.total_ram_mb()))
    if "UnsupportedClassVersionError" in ctx and "class file version" in ctx:
        pass  # объяснит ai.heuristic_actions ниже
    try:
        notes, _ = ai.heuristic_actions(profile, ctx)
        lines += notes
    except Exception:
        pass
    if re.search(r"export package .* to module", ctx):
        lines.append(tr("crash_modconflict"))
    bad = known_bad_mods(profile, ctx)
    if bad:
        lines.append(tr("crash_badmod", list=", ".join(bad)))
    if len(lines) == 1:
        lines.append(tr("crash_unknown"))
        lines += ["» " + l for l in error_lines(ctx)]
    lines.append(tr("crash_log_saved", path=str(log_path(profile))))
    return lines


def lower_ram_if_heap_failed(profile: dict, rep) -> bool:
    """Если JVM не смогла зарезервировать heap - снижаем память до безопасной."""
    from . import preflight, profiles
    ctx = context(profile)
    if re.search(r"Could not reserve enough space|Invalid maximum heap size", ctx):
        new = min(int(profile.get("ram_mb", 4096)), preflight.safe_ram_cap())
        if new != int(profile.get("ram_mb", 0)):
            profile["ram_mb"] = new
            profiles.save_profile(profile)
            rep.log("✔ " + tr("pre_ram_cap", n=new, t=sysinfo.total_ram_mb()))
            return True
    return False
