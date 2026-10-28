"""Свои серверы Minecraft без хостинга: ядра (Vanilla, Paper, Purpur, Folia, Fabric, Forge, NeoForge), запуск, консоль,
плагины/моды через общий каталог и подключение друзей по P2P (через relay лаунчера).

Источники ядер - первичные: Mojang (vanilla), PaperMC Fill v3 (paper, folia; старый api.papermc.io/v2 больше не получает сборки),
Purpur API, Fabric Meta, Forge / NeoForge installer (--installServer, только 1.17+)."""
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import java_manager, repos, versions
from .config import OS_NAME, SERVERS_DIR
from .net import Reporter, download, get_json

CORES = ["vanilla", "paper", "purpur", "folia", "fabric", "forge", "neoforge"]
PLUGIN_CORES = {"paper", "purpur", "folia"}
MOD_CORES = {"fabric", "forge", "neoforge"}
FILL = "https://fill.papermc.io/v3/projects"
PURPUR = "https://api.purpurmc.org/v2/purpur"
READY_RE = re.compile(r"Done \([\d.,]+s\)! For help")
SAFE = re.compile(r'[\\/:*?"<>|]')


class ServerError(Exception):
    pass


# ------------------------------------------------------------------ учёт серверов
def _file(name: str) -> Path:
    return SERVERS_DIR / name / "server.json"


def list_servers() -> list:
    out = []
    if SERVERS_DIR.exists():
        for d in sorted(SERVERS_DIR.iterdir()):
            if (d / "server.json").exists():
                try:
                    out.append(json.loads((d / "server.json").read_text("utf-8")))
                except ValueError:
                    pass
    return out


def get_server(name: str):
    f = _file(name)
    return json.loads(f.read_text("utf-8")) if f.exists() else None


def save_server(s: dict):
    _file(s["name"]).parent.mkdir(parents=True, exist_ok=True)
    _file(s["name"]).write_text(json.dumps(s, ensure_ascii=False, indent=2), "utf-8")


def server_dir(s: dict) -> Path:
    return SERVERS_DIR / s["name"]


def delete_server(name: str):
    d = SERVERS_DIR / name
    if d.exists() and (d / "server.json").exists():
        shutil.rmtree(d)


def pseudo_profile(s: dict) -> dict:
    """Сервер в виде «профиля»: так каталог модов/плагинов ставит файлы в папку сервера."""
    return {"name": f"srv:{s['name']}", "mc_version": s["mc_version"], "loader": s["core"], "version_id": s["mc_version"],
            "ram_mb": s.get("ram_mb", 2048), "_dir": str(server_dir(s)), "is_server": True}


def kinds_for(core: str) -> list:
    if core in PLUGIN_CORES:
        return ["plugin", "datapack"]
    if core in MOD_CORES:
        return ["mod", "datapack"]
    return ["datapack"]


# ------------------------------------------------------------ версии и загрузки
def core_versions(core: str) -> list:
    """Версии Minecraft, для которых есть это ядро (новые сверху)."""
    if core == "vanilla":
        return [v["id"] for v in versions.fetch_manifest()["versions"] if v["type"] == "release"]
    if core in ("paper", "folia"):
        data = get_json(f"{FILL}/{core}")["versions"]
        out = []
        for group in (data.values() if isinstance(data, dict) else [data]):
            out += list(group) if isinstance(group, list) else [group]
        return out
    if core == "purpur":
        return list(reversed(get_json(PURPUR)["versions"]))
    if core == "fabric":
        return [g["version"] for g in repos.fabric_game() if g.get("stable")]
    if core == "forge":
        keys = repos.forge_promos().keys()
        vs = sorted({k.rsplit("-", 1)[0] for k in keys}, key=lambda v: [int(x) if x.isdigit() else 0 for x in re.split(r"\D+", v)], reverse=True)
        return [v for v in vs if _minor(v) >= 17]
    if core == "neoforge":
        found = {f"1.{v.split('.')[0]}" + (f".{v.split('.')[1]}" if v.split(".")[1] != "0" else "") for v in repos.neoforge_all()
                 if re.match(r"^\d+\.\d+\.", v)}
        return sorted(found, key=lambda v: [int(x) for x in v.split(".")], reverse=True)
    raise ServerError(f"Неизвестное ядро: {core}")


def _minor(mc: str) -> int:
    try:
        return int(mc.split(".")[1])
    except (IndexError, ValueError):
        return 0


def resolve_download(core: str, mc: str) -> dict:
    """{'url','name','sha1'|'sha256'} для ядра, которое скачивается одним файлом."""
    if core == "vanilla":
        srv = versions.fetch_version_json(mc).get("downloads", {}).get("server")
        if not srv:
            raise ServerError(f"У Minecraft {mc} нет серверного jar")
        return {"url": srv["url"], "name": f"minecraft_server.{mc}.jar", "sha1": srv.get("sha1")}
    if core in ("paper", "folia"):
        builds = get_json(f"{FILL}/{core}/versions/{mc}/builds")
        if not builds:
            raise ServerError(f"{core}: для {mc} нет сборок")
        pick = next((b for b in builds if b.get("channel") in ("STABLE", "RECOMMENDED")), builds[0])   # стабильная, иначе новейшая
        d = pick["downloads"]["server:default"]
        return {"url": d["url"], "name": d["name"], "sha256": (d.get("checksums") or {}).get("sha256")}
    if core == "purpur":
        return {"url": f"{PURPUR}/{mc}/latest/download", "name": f"purpur-{mc}.jar"}
    if core == "fabric":
        loader = next(l["version"] for l in repos.fabric_loaders() if l.get("stable"))
        inst = next(i["version"] for i in get_json("https://meta.fabricmc.net/v2/versions/installer") if i.get("stable"))
        return {"url": f"https://meta.fabricmc.net/v2/versions/loader/{mc}/{loader}/{inst}/server/jar", "name": "fabric-server-launch.jar"}
    raise ServerError(f"{core}: загрузка через установщик")


def _java(mc: str, rep: Reporter, settings=None) -> str:
    try:
        vj = versions.fetch_version_json(mc)
        major = java_manager.required_major(vj, mc)
    except Exception:
        major = java_manager.required_major({}, mc)
    return java_manager.ensure_java(major, rep, (settings.get("java_path") if settings else ""))


def _forge_like_args(sdir: Path):
    """Файл аргументов запуска, который создал установщик Forge/NeoForge 1.17+."""
    name = "win_args.txt" if OS_NAME == "windows" else "unix_args.txt"
    found = sorted(sdir.glob(f"libraries/net/*/*/*/{name}"))
    return found[0] if found else None


def create_server(name: str, core: str, mc: str, ram_mb: int = 2048, port: int = 25565, rep: Reporter = None,
                  settings=None) -> dict:
    rep = rep or Reporter()
    name = SAFE.sub("_", name).strip().strip(".")
    if not name or core not in CORES:
        raise ServerError("Неверное имя или ядро")
    if _file(name).exists():
        raise ServerError(f"Сервер «{name}» уже существует")
    sdir = SERVERS_DIR / name
    sdir.mkdir(parents=True, exist_ok=True)
    try:
        s = {"name": name, "core": core, "mc_version": mc, "ram_mb": int(ram_mb), "port": int(port), "online_mode": False,
             "p2p": True, "eula": False, "jar": None, "args_file": None, "created": int(time.time())}
        if core in ("forge", "neoforge"):
            if _minor(mc) < 17:
                raise ServerError("Forge/NeoForge серверы поддерживаются для Minecraft 1.17 и новее")
            ver = repos.pick(core, mc)
            if core == "forge":
                url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{mc}-{ver}/forge-{mc}-{ver}-installer.jar"
            else:
                grp, art = ("net/neoforged/forge", "forge") if mc == "1.20.1" else ("net/neoforged/neoforge", "neoforge")
                url = f"https://maven.neoforged.net/releases/{grp}/{ver}/{art}-{ver}-installer.jar"
            installer = sdir / "installer.jar"
            rep.log(f"Скачиваю установщик {core} {ver}...")
            download(url, installer)
            java = _java(mc, rep, settings)
            rep.log("Устанавливаю сервер (несколько минут)...")
            r = subprocess.run([java, "-jar", str(installer), "--installServer"], cwd=str(sdir), capture_output=True, text=True,
                               creationflags=0x08000000 if OS_NAME == "windows" else 0)
            if r.returncode != 0:
                raise ServerError("Установщик завершился с ошибкой:\n" + (r.stdout + r.stderr)[-1200:])
            args = _forge_like_args(sdir)
            if not args:
                raise ServerError("Установщик не создал файл аргументов запуска")
            s["args_file"] = args.relative_to(sdir).as_posix()
        else:
            d = resolve_download(core, mc)
            rep.log(f"Скачиваю {core} {mc}...")
            download(d["url"], sdir / d["name"], d.get("sha1"))
            s["jar"] = d["name"]
        write_properties(s, {})
        save_server(s)
        rep.log(f"Сервер «{name}» готов")
        return s
    except Exception:
        shutil.rmtree(sdir, ignore_errors=True)      # не оставляем полуустановленный сервер
        raise


# ------------------------------------------------------------------- настройки
def write_properties(s: dict, extra: dict):
    """server.properties: порт и режим входа из настроек, остальное сохраняется."""
    f = server_dir(s) / "server.properties"
    props = {}
    if f.exists():
        for line in f.read_text("utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                props[k] = v
    props.update({"server-port": str(s["port"]), "online-mode": "true" if s.get("online_mode") else "false",
                  "motd": props.get("motd", f"CD Launcher: {s['name']}")})
    props.update({k: str(v) for k, v in extra.items()})
    f.write_text("\n".join(f"{k}={v}" for k, v in props.items()) + "\n", "utf-8")


def accept_eula(s: dict):
    """eula.txt пишется ТОЛЬКО после явного согласия пользователя с https://aka.ms/MinecraftEULA."""
    (server_dir(s) / "eula.txt").write_text(
        "# Accepted in CD Launcher (https://aka.ms/MinecraftEULA)\n" f"# {time.strftime('%a %b %d %H:%M:%S %Y')}\neula=true\n", "utf-8")
    s["eula"] = True
    save_server(s)


def build_command(s: dict, settings=None, rep: Reporter = None) -> list:
    rep = rep or Reporter()
    java = _java(s["mc_version"], rep, settings)
    ram = int(s.get("ram_mb", 2048))
    if s.get("args_file"):
        jvm = server_dir(s) / "user_jvm_args.txt"
        jvm.write_text(f"-Xms{min(1024, ram)}M\n-Xmx{ram}M\n", "utf-8")
        return [java, f"@{jvm.name}", f"@{s['args_file']}", "nogui"]
    return [java, f"-Xms{min(1024, ram)}M", f"-Xmx{ram}M", "-jar", s["jar"], "nogui"]


class ServerProcess:
    """Запущенный сервер: читает вывод в фоне, принимает команды, корректно останавливается."""

    def __init__(self, s: dict, on_line=None, on_ready=None, on_exit=None):
        self.s, self.on_line, self.on_ready, self.on_exit = s, on_line or (lambda l: None), on_ready or (lambda: None), on_exit or (lambda c: None)
        self.proc, self.ready = None, False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, settings=None, rep: Reporter = None):
        if self.running:
            return
        if not self.s.get("eula"):
            raise ServerError("eula")
        cmd = build_command(self.s, settings, rep)
        write_properties(self.s, {})
        self.ready = False
        self.proc = subprocess.Popen(cmd, cwd=str(server_dir(self.s)), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     creationflags=0x08000000 if OS_NAME == "windows" else 0)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            self.on_line(line)
            if not self.ready and READY_RE.search(line):
                self.ready = True
                self.on_ready()
        self.on_exit(self.proc.wait())

    def send(self, command: str):
        if self.running and command.strip():
            try:
                self.proc.stdin.write(command.strip() + "\n")
                self.proc.stdin.flush()
            except OSError:
                pass

    def stop(self, timeout: float = 30.0):
        """Просим сервер сохранить мир и выйти; если не вышел - завершаем принудительно."""
        if not self.running:
            return
        self.send("stop")
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
