"""CD AI Helper: локальная нейросеть (Ollama) для авто-ремонта сборок и создания сборок по тексту.

Движок устроен надёжно: сначала работают детерминированные правила (они находят типовые
причины вылетов даже без нейросети), затем модель дополняет диагноз. Любое действие ИИ
проходит проверку (белый список действий, существующие файлы, реальные проекты Modrinth).
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests

from . import java_manager, modrinth, profiles, versions
from .config import CACHE_DIR, OS_NAME
from .i18n import LANG, tr
from .net import Reporter
from .profiles import game_dir

SYSTEM_FIX = (
    "Ты CD AI - эксперт по вылетам Minecraft. Отвечай СТРОГО JSON без пояснений вне JSON: "
    '{"diagnosis": "краткая причина по-русски", "actions": [{"type": "...", "target": "..."}]}. '
    "Допустимые type: remove_mod (target = имя .jar или mod id), install_mod (target = slug мода на Modrinth), "
    "set_java (target = 8, 17 или 21), set_ram (target = МБ числом), delete_config (target = имя файла в config/). "
    "Если не уверен - верни пустой список actions."
)
SYSTEM_BUILD = (
    "Ты CD AI - подборщик сборок Minecraft. Отвечай СТРОГО JSON: "
    '{"name": "...", "mc_version": "1.20.1", "loader": "fabric|forge|neoforge|quilt", "ram_mb": 4096, '
    '"mods": ["slug1", "slug2"]}. mods - slug реальных модов Modrinth (до 15). '
    "Для слабых ПК добавляй sodium, lithium, ferrite-core, modernfix, immediatelyfast, entityculling (Fabric). "
    "Для Forge/NeoForge: embeddium, ferrite-core, modernfix."
)

OPTIMIZATION_MODS = {
    "fabric": ["sodium", "lithium", "ferrite-core", "modernfix", "immediatelyfast", "entityculling", "iris"],
    "quilt": ["sodium", "lithium", "ferrite-core", "modernfix", "immediatelyfast", "entityculling"],
    "forge": ["embeddium", "ferrite-core", "modernfix", "entityculling"],
    "neoforge": ["sodium", "ferrite-core", "modernfix", "entityculling"],
}


def lang_rule() -> str:
    return (f" ALWAYS write all human-readable text (diagnosis, explanations, replies) strictly in "
            f"{LANG.english_name}. JSON keys and action types stay in English.")


class AIError(Exception):
    pass


# ------------------------------------------------------------------ Ollama ---
class OllamaClient:
    def __init__(self, settings):
        self.settings = settings

    @property
    def base(self):
        return self.settings.get("ollama_url").rstrip("/")

    @property
    def model(self):
        return self.settings.get("ai_model")

    def is_running(self) -> bool:
        try:
            return requests.get(self.base + "/api/tags", timeout=2).status_code == 200
        except Exception:
            return False

    def has_model(self) -> bool:
        try:
            tags = requests.get(self.base + "/api/tags", timeout=5).json().get("models", [])
            want = self.model.split(":")[0]
            return any(m["name"] == self.model or m["name"].split(":")[0] == want and ":" not in self.model
                       for m in tags)
        except Exception:
            return False

    @staticmethod
    def _find_binary():
        exe = shutil.which("ollama")
        if exe:
            return exe
        if OS_NAME == "windows":
            cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
            if cand.exists():
                return str(cand)
        return None

    def ensure_ready(self, rep: Reporter = None):
        """Запускает/ставит Ollama и скачивает модель, если нужно."""
        rep = rep or Reporter()
        if not self.is_running():
            exe = self._find_binary()
            if not exe:
                if OS_NAME != "windows":
                    raise AIError("Ollama не установлена. Установите с https://ollama.com/download и повторите.")
                rep.log("Скачиваю Ollama (локальный ИИ-движок)...")
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                setup = CACHE_DIR / "OllamaSetup.exe"
                with requests.get("https://ollama.com/download/OllamaSetup.exe", stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(setup, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                rep.log("Устанавливаю Ollama...")
                subprocess.run([str(setup), "/SILENT", "/NORESTART"], check=False)
                exe = self._find_binary()
                if not exe:
                    raise AIError("Не удалось установить Ollama автоматически")
            rep.log("Запускаю Ollama...")
            flags = 0x08000000 | 0x00000008 if OS_NAME == "windows" else 0
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=flags)
            for _ in range(60):
                if self.is_running():
                    break
                time.sleep(0.5)
            else:
                raise AIError("Ollama не запустилась")
        if not self.has_model():
            rep.log(f"Скачиваю модель {self.model} (один раз)...")
            with requests.post(self.base + "/api/pull", json={"name": self.model, "stream": True},
                               stream=True, timeout=None) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    j = json.loads(line)
                    if j.get("error"):
                        raise AIError(j["error"])
                    if j.get("total"):
                        rep.progress(j.get("completed", 0), j["total"])
            rep.log("Модель готова")

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        payload = {"model": self.model, "stream": False,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "options": {"temperature": 0.2, "num_ctx": 8192}}
        if json_mode:
            payload["format"] = "json"
        r = requests.post(self.base + "/api/chat", json=payload, timeout=600)
        if r.status_code != 200:
            raise AIError(f"Ollama: {r.text[:200]}")
        return r.json()["message"]["content"]


def extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            pass
    raise AIError("Модель вернула ответ не в формате JSON")


# --------------------------------------------------------------- Fix Engine ---
def collect_crash_context(profile: dict) -> str:
    gdir = game_dir(profile)
    parts = []
    crashes = sorted((gdir / "crash-reports").glob("crash-*.txt"), key=lambda p: p.stat().st_mtime) \
        if (gdir / "crash-reports").exists() else []
    if crashes:
        parts.append("== CRASH REPORT ==\n" + crashes[-1].read_text("utf-8", "replace")[:6000])
    log = gdir / "logs" / "latest.log"
    if log.exists():
        lines = log.read_text("utf-8", "replace").splitlines()
        parts.append("== latest.log (конец) ==\n" + "\n".join(lines[-150:]))
    hs = sorted(gdir.glob("hs_err_pid*.log"), key=lambda p: p.stat().st_mtime)
    if hs:
        parts.append("== hs_err ==\n" + hs[-1].read_text("utf-8", "replace")[:1500])
    return "\n\n".join(parts)


def heuristic_actions(profile: dict, ctx: str) -> tuple:
    """Правила для типовых вылетов. Возвращает (список объяснений, список действий)."""
    notes, actions = [], []

    m = re.search(r"class file version (\d+)\.\d+", ctx)
    if "UnsupportedClassVersionError" in ctx and m:
        need = int(m.group(1)) - 44
        major = next((l for l in java_manager.LTS if l >= need), need)
        notes.append(tr("fix_java", n=need))
        actions.append({"type": "set_java", "target": str(major)})
    elif re.search(r"has been compiled by a more recent version|java\.lang\.UnsupportedClassVersionError", ctx):
        actions.append({"type": "set_java", "target": "21"})
        notes.append(tr("fix_java", n=21))

    if "OutOfMemoryError" in ctx or "Java heap space" in ctx:
        cur = int(profile.get("ram_mb", 4096))
        actions.append({"type": "set_ram", "target": str(min(cur * 2, 12288))})
        notes.append(tr("fix_ram"))

    for line in ctx.splitlines():  # Fabric/Quilt: отсутствует зависимость
        if "which is missing" in line or "is missing" in line and "requires" in line:
            mm = re.search(r"of ([a-z0-9_\-]+), which is missing", line)
            ids = re.findall(r"\(([a-z0-9_\-]+)\)", line)
            target = mm.group(1) if mm else (ids[-1] if ids else None)
            if target:
                target = "fabric-api" if target.startswith("fabric") and target != "fabric-language-kotlin" else target
                notes.append(tr("fix_missing", mod=target))
                actions.append({"type": "install_mod", "target": target})
    for mid, _by in re.findall(r"Mod ID: '([^']+)', Requested by: '([^']+)'[^\n]*MISSING", ctx):  # Forge
        if mid not in ("minecraft", "forge", "neoforge"):
            notes.append(tr("fix_missing", mod=mid))
            actions.append({"type": "install_mod", "target": mid})

    for a, b in re.findall(r"Mod '[^']*' \(([a-z0-9_\-]+)\)[^\n]*?(?:is incompatible with|conflicts with)[^\n]*?\(([a-z0-9_\-]+)\)", ctx):
        notes.append(tr("fix_conflict", a=a, b=b))
        actions.append({"type": "remove_mod", "target": a})

    for mid in re.findall(r"(?:Mixin apply failed|mixin.*?failed)[^\n]*?from mod ([a-z0-9_\-]+)", ctx, re.I):
        notes.append(tr("fix_mixin", mod=mid))
        actions.append({"type": "remove_mod", "target": mid})

    m = re.search(r"Duplicate mods? found.*?\n(.*?)\n\n", ctx, re.S)
    if m:
        files = re.findall(r"([\w\-.+]+\.jar)", m.group(1))
        if len(files) >= 2:
            notes.append(tr("fix_dup", f=files[-1]))
            actions.append({"type": "remove_mod", "target": files[-1]})
    return notes, actions


def _dedupe(actions: list) -> list:
    seen, out = set(), []
    for a in actions:
        key = (a.get("type"), str(a.get("target")))
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


def apply_actions(profile: dict, actions: list, settings, rep: Reporter) -> list:
    """Выполняет проверенные действия. Возвращает список отчётов о сделанном."""
    done = []
    gdir = game_dir(profile)
    for a in _dedupe(actions):
        t, target = a.get("type"), str(a.get("target", "")).strip()
        try:
            if t == "remove_mod" and target:
                name = modrinth.quarantine_mod(profile, target)
                if name:
                    done.append(tr("act_removed", f=name))
            elif t == "install_mod" and target:
                try:
                    got = modrinth.install_mod(profile, target, rep)
                except Exception:
                    from . import catalog
                    got = catalog.find_by_mod_id(target, profile, settings, rep)
                if got:
                    done.append(tr("act_installed", mods=", ".join(got)))
            elif t == "set_java" and target.isdigit() and int(target) in java_manager.LTS:
                profile["java_major"] = int(target)
                done.append(tr("act_java", n=target))
            elif t == "set_ram" and target.isdigit():
                profile["ram_mb"] = max(1024, min(int(target), 32768))
                done.append(tr("act_ram", n=profile["ram_mb"]))
            elif t == "delete_config" and target:
                p = (gdir / "config" / Path(target).name)
                if p.exists() and p.is_file():
                    bak = gdir / "config_removed"
                    bak.mkdir(exist_ok=True)
                    shutil.move(str(p), str(bak / p.name))
                    done.append(tr("act_config", f=p.name))
        except Exception as e:
            rep.log(f"⚠ Действие {t} {target}: {e}")
    profiles.save_profile(profile)
    return done


def fix_profile(profile: dict, settings, rep: Reporter = None) -> dict:
    """Анализирует вылет и ИСПРАВЛЯЕТ сборку. Возвращает {'diagnosis','applied'}."""
    rep = rep or Reporter()
    ctx = collect_crash_context(profile)
    if not ctx:
        return {"diagnosis": tr("fix_nolog"), "applied": []}
    notes, actions = heuristic_actions(profile, ctx)

    # Зависимости, объявленные внутри jar (вдруг мод положили вручную)
    for dep in modrinth.missing_dependencies(profile):
        actions.append({"type": "install_mod", "target": dep})
        notes.append(tr("fix_dep", mod=dep))

    diagnosis = " ".join(notes)
    client = OllamaClient(settings)
    try:
        client.ensure_ready(rep)
        mods = [m["file"] for m in modrinth.installed_mods(profile)]
        user = (f"Сборка: {profile['loader']} {profile['mc_version']}, Java {profile.get('java_major') or 'авто'}, "
                f"RAM {profile.get('ram_mb')} МБ.\nМоды: {', '.join(mods) or 'нет'}\n"
                f"Найдено правилами: {diagnosis or 'ничего'}\n\n{ctx[:9000]}")
        data = extract_json(client.chat(SYSTEM_FIX + lang_rule(), user, json_mode=True))
        if data.get("diagnosis"):
            diagnosis = (diagnosis + " " if diagnosis else "") + str(data["diagnosis"])
        if not actions:  # если правила молчат - доверяем (проверенным) советам модели
            for a in data.get("actions", [])[:6]:
                if isinstance(a, dict) and a.get("type") == "remove_mod":
                    if not modrinth.quarantine_check(profile, str(a.get("target", ""))):
                        continue
                if isinstance(a, dict):
                    actions.append(a)
    except Exception as e:
        rep.log(f"ИИ-модель недоступна ({e}); использую встроенные правила")

    applied = apply_actions(profile, actions, settings, rep)
    if not diagnosis:
        diagnosis = tr("fix_unknown")
    return {"diagnosis": diagnosis.strip(), "applied": applied}


# ------------------------------------------------------- генератор сборок ---
def _heuristic_plan(prompt: str, settings) -> dict:
    low = prompt.lower()
    mc = re.search(r"\b1\.\d{1,2}(?:\.\d{1,2})?\b", prompt)
    loader = next((l for l in ("neoforge", "quilt", "fabric", "forge") if l in low), "fabric")
    weak = bool(re.search(r"слаб|потато|potato|low|оптимиз|fps|optimi", low))
    mods = list(OPTIMIZATION_MODS[loader]) if weak else []
    return {"name": "AI " + time.strftime("%H%M%S"), "mc_version": mc.group(0) if mc else "1.20.1",
            "loader": loader, "ram_mb": 3072 if weak else 4096, "mods": mods,
            "categories": [c for key, c in (("маги", "magic"), ("magic", "magic"), ("техно", "technology"),
                                            ("tech", "technology"), ("rpg", "adventure"),
                                            ("приключ", "adventure"), ("еда", "food"), ("food", "food"),
                                            ("декор", "decoration")) if key in low]}


def build_from_prompt(prompt: str, settings, rep: Reporter = None) -> dict:
    """Текст -> готовая сборка: профиль, загрузчик, моды со всеми зависимостями."""
    rep = rep or Reporter()
    plan = _heuristic_plan(prompt, settings)
    client = OllamaClient(settings)
    try:
        client.ensure_ready(rep)
        data = extract_json(client.chat(SYSTEM_BUILD + lang_rule(), prompt, json_mode=True))
        for k in ("name", "mc_version", "loader", "ram_mb"):
            if data.get(k):
                plan[k] = data[k]
        if isinstance(data.get("mods"), list):
            plan["mods"] = [str(m) for m in data["mods"]][:15] or plan["mods"]
    except Exception as e:
        rep.log(f"ИИ-модель недоступна ({e}); строю сборку по правилам")

    plan["loader"] = str(plan["loader"]).lower()
    if plan["loader"] not in ("fabric", "forge", "neoforge", "quilt"):
        plan["loader"] = "fabric"
    manifest = versions.fetch_manifest()
    releases = {v["id"] for v in manifest["versions"] if v["type"] == "release"}
    if plan["mc_version"] not in releases:
        plan["mc_version"] = manifest["latest"]["release"]
    plan["name"] = profiles.safe_name(str(plan["name"]))
    base, n = plan["name"], 1
    while (profiles.PROFILES_DIR / plan["name"]).exists():
        n += 1
        plan["name"] = f"{base} {n}"

    rep.log(f"План: {plan['loader']} {plan['mc_version']}, моды: {', '.join(plan['mods']) or '—'}")
    profile = profiles.create_profile(plan["name"], plan["mc_version"], plan["loader"], rep,
                                      int(plan["ram_mb"]), settings)
    installed, skipped = [], []
    for query in plan["mods"]:
        try:
            installed += modrinth.install_mod(profile, query, rep)
        except Exception:
            hits = modrinth.search(query, profile["mc_version"], profile["loader"], limit=1)
            if hits:
                try:
                    installed += modrinth.install_mod(profile, hits[0]["project_id"], rep)
                    continue
                except Exception:
                    pass
            skipped.append(query)
    for cat in plan.get("categories", []):  # тематические моды по категории
        for hit in modrinth.search("", profile["mc_version"], profile["loader"], cat, limit=3):
            try:
                installed += modrinth.install_mod(profile, hit["project_id"], rep)
            except Exception as e:
                rep.log(f"⚠ {hit['title']}: {e}")
    from . import catalog
    installed += modrinth.resolve_local_dependencies(
        profile, rep, lambda mid: catalog.find_by_mod_id(mid, profile, settings, rep))
    return {"profile": profile, "installed": list(dict.fromkeys(installed)), "skipped": skipped}


def free_chat(question: str, settings, rep: Reporter = None) -> str:
    client = OllamaClient(settings)
    client.ensure_ready(rep)
    return client.chat("You are CD AI, a helpful Minecraft modpack assistant. Answer briefly." + lang_rule(), question)
