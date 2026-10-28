"""Загрузка и кэш картинок (иконки и галереи модов): любой формат -> PNG на диске, без Qt."""
import hashlib
import io
from pathlib import Path

from PIL import Image

from .config import CACHE_DIR
from .net import _session

IMG_DIR = CACHE_DIR / "img"
MAX_BYTES = 8 * 1024 * 1024


def fetch_image(url: str, max_side: int = 0):
    """Путь к PNG в кэше (скачивает при первом обращении) или None при ошибке."""
    if not url:
        return None
    key = hashlib.sha1(f"{url}|{max_side}".encode()).hexdigest()
    path = IMG_DIR / f"{key}.png"
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        r = _session.get(url, timeout=15, stream=True)
        r.raise_for_status()
        data = b""
        for chunk in r.iter_content(1 << 15):
            data += chunk
            if len(data) > MAX_BYTES:
                return None
        img = Image.open(io.BytesIO(data))
        img.seek(0)
        img = img.convert("RGBA")
        if max_side and max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.LANCZOS)
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        img.save(path, "PNG")
        return path
    except Exception:
        return None
