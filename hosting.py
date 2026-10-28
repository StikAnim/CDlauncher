"""Запуск сервера друзей / relay ПРЯМО В ЛАУНЧЕРЕ, чтобы не нужен был VPS: один из друзей включает его у себя,
остальные подключаются к его адресу. (Порт хоста должен быть достижим: проброс порта или общий адрес.)"""
import asyncio
import importlib.util
import os
import threading

from .config import ROOT, resource_path


def _load_server_module():
    os.environ.setdefault("CD_SERVER_DB", str(ROOT / "friends_server_db.json"))
    path = resource_path("server/cd_server.py")
    spec = importlib.util.spec_from_file_location("cd_server_embedded", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HostedServer:
    def __init__(self):
        self.thread = None
        self.loop = None
        self.task = None
        self.port = 0
        self.error = None

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, port: int = 25585, host: str = "0.0.0.0", wait: float = 3.0) -> bool:
        if self.running:
            return True
        self.error, self.port = None, port
        ready = threading.Event()

        def work():
            try:
                mod = _load_server_module()
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                self.task = self.loop.create_task(mod.main(host, port))
                self.loop.call_later(0.4, ready.set)
                self.loop.run_until_complete(self.task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.error = str(e)
                ready.set()

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        ready.wait(wait)
        return self.running and not self.error

    def stop(self):
        if self.loop and self.task:
            self.loop.call_soon_threadsafe(self.task.cancel)
        if self.thread:
            self.thread.join(timeout=3)
        self.thread = None
