"""Сетевые утилиты: JSON-запросы, надёжное скачивание с проверкой SHA1, пул загрузок."""
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

UA = {"User-Agent": "CDLauncher/1.0 (+minecraft launcher)"}
_session = requests.Session()
_session.headers.update(UA)


class Reporter:
    """Передаёт сообщения и прогресс из фоновых функций в интерфейс."""

    def __init__(self, log=None, progress=None):
        self._log = log or (lambda m: None)
        self._progress = progress or (lambda c, t: None)

    def log(self, message: str):
        self._log(str(message))

    def progress(self, current: int, total: int):
        self._progress(int(current), int(total))


def get_json(url, params=None, headers=None, timeout=30):
    r = _session.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_text(url, timeout=30):
    r = _session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def post_json(url, payload=None, data=None, headers=None, timeout=30, check=True):
    r = _session.post(url, json=payload, data=data, headers=headers, timeout=timeout)
    if check:
        r.raise_for_status()
    return r


def sha1_of(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest, sha1=None, retries=3, timeout=60) -> Path:
    """Скачивает файл. Если файл уже есть и хэш совпадает - ничего не делает."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        if sha1 is None or sha1_of(dest) == sha1:
            return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(retries):
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with _session.get(url, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        if chunk:
                            f.write(chunk)
            if sha1 and sha1_of(tmp) != sha1:
                raise IOError(f"SHA1 не совпал для {url}")
            tmp.replace(dest)
            return dest
        except Exception as e:  # повторяем с паузой
            last = e
            time.sleep(1 + attempt)
    raise IOError(f"Не удалось скачать {url}: {last}")


def download_many(items, rep=None, workers=16, label="файлов"):
    """items: список (url, dest, sha1). Параллельная загрузка с прогрессом."""
    rep = rep or Reporter()
    items = [i for i in items if i[0]]
    total = len(items)
    if not total:
        return
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download, u, d, s) for (u, d, s) in items]
        for fut in as_completed(futures):
            fut.result()
            done += 1
            if done % 25 == 0 or done == total:
                rep.progress(done, total)
    rep.log(f"Загружено {total} {label}")
