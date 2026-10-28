"""Смена папки данных лаунчера: перенос всего содержимого, проверка, запись нового пути, перезапуск."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config
from .net import Reporter


class DataDirError(Exception):
    pass


def _files(root: Path) -> list:
    return [p for p in root.rglob("*") if p.is_file()]


def validate(old: Path, new: Path):
    old, new = Path(old).resolve(), Path(new).resolve()
    if old == new:
        raise DataDirError("Это уже текущая папка данных")
    if old in new.parents or new in old.parents:
        raise DataDirError("Новая папка не может быть внутри текущей (и наоборот)")
    try:
        new.mkdir(parents=True, exist_ok=True)
        probe = new / ".write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        raise DataDirError(f"Нет доступа к папке {new}: {e}")
    return old, new


def migrate(old: Path, new: Path, rep: Reporter = None, delete_old: bool = False) -> Path:
    """Копирует все данные в новую папку, сверяет размеры, затем записывает новый путь.
    Старую папку удаляет только если delete_old=True и всё сошлось."""
    rep = rep or Reporter()
    old, new = validate(old, new)
    files = _files(old) if old.exists() else []
    total = len(files)
    rep.log(f"Переношу {total} файлов в {new}")
    for i, f in enumerate(files, 1):
        dest = new / f.relative_to(old)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.stat().st_size != f.stat().st_size:
            shutil.copy2(f, dest)
        if i % 50 == 0 or i == total:
            rep.progress(i, total)
    bad = [f for f in files if not (new / f.relative_to(old)).exists()
           or (new / f.relative_to(old)).stat().st_size != f.stat().st_size]
    if bad:
        raise DataDirError(f"Не все файлы перенесены (например {bad[0].name}); старая папка не тронута")
    config.write_pointer(new)                       # только после успешной проверки
    if delete_old and old.exists():
        shutil.rmtree(old, ignore_errors=True)
    rep.log("Перенос завершён")
    return new


def restart_command() -> list:
    return [sys.executable] if getattr(sys, "frozen", False) else [sys.executable] + sys.argv


def restart():
    """Запускает новый экземпляр лаунчера (он прочитает новую папку данных) - вызывающий затем закрывает текущий."""
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(restart_command(), creationflags=flags, close_fds=True)
