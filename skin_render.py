"""Скин Ely.by -> картинка персонажа (анфас) и голова-аватар. Только Pillow, работает в фоне."""
import io

import requests
from PIL import Image

from .net import UA

ELY_SKIN = "https://skinsystem.ely.by/skins/{name}.png"


def fetch_skin(name: str):
    """Скачивает скин игрока с Ely.by. None, если скина нет / нет сети."""
    try:
        r = requests.get(ELY_SKIN.format(name=name), headers=UA, timeout=8)
        if r.status_code == 200 and r.content[:4] == b"\x89PNG":
            return Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception:
        pass
    return None


def placeholder_skin() -> Image.Image:
    """Нейтральный скин-заглушка (рисуется кодом), если настоящего нет."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    def fill(box, col):
        img.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), col), box[:2])
    skin, shirt, pants = (198, 148, 108, 255), (34, 160, 190, 255), (60, 70, 130, 255)
    fill((8, 8, 16, 16), skin); fill((8, 8, 16, 10), (70, 45, 30, 255))       # голова + волосы
    fill((20, 20, 28, 32), shirt)                                               # тело
    fill((44, 20, 48, 32), shirt); fill((36, 52, 40, 64), shirt)                # руки
    fill((4, 20, 8, 32), pants); fill((20, 52, 24, 64), pants)                  # ноги
    fill((44, 20, 48, 22), skin); fill((36, 52, 40, 54), skin)
    return img


def _part(skin, box, mirror=False):
    p = skin.crop(box)
    return p.transpose(Image.FLIP_LEFT_RIGHT) if mirror else p


def render_front(skin: Image.Image = None, scale: int = 10) -> bytes:
    """Фигура анфас 16x32 пикселей * scale, PNG-байты с прозрачностью."""
    skin = (skin or placeholder_skin()).convert("RGBA")
    legacy = skin.height == 32                       # старые скины 64x32: левые части = зеркало правых
    canvas = Image.new("RGBA", (16, 32), (0, 0, 0, 0))

    def put(box, pos, ov_box=None, mirror=False):
        canvas.alpha_composite(_part(skin, box, mirror), pos)
        if ov_box and not legacy:
            canvas.alpha_composite(_part(skin, ov_box, mirror), pos)

    put((20, 20, 28, 32), (4, 8), (20, 36, 28, 48))                                       # тело
    put((44, 20, 48, 32), (0, 8), (44, 36, 48, 48))                                       # правая рука
    put((36, 52, 40, 64) if not legacy else (44, 20, 48, 32), (12, 8),
        (52, 52, 56, 64), mirror=legacy)                                                  # левая рука
    put((4, 20, 8, 32), (4, 20), (4, 36, 8, 48))                                          # правая нога
    put((20, 52, 24, 64) if not legacy else (4, 20, 8, 32), (8, 20),
        (4, 52, 8, 64), mirror=legacy)                                                    # левая нога
    put((8, 8, 16, 16), (4, 0))                                                           # голова
    if skin.height >= 64 or legacy:
        canvas.alpha_composite(_part(skin, (40, 8, 48, 16)), (4, 0))                      # шапка/волосы
    out = canvas.resize((16 * scale, 32 * scale), Image.NEAREST)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def render_head(skin: Image.Image = None, size: int = 64) -> bytes:
    skin = (skin or placeholder_skin()).convert("RGBA")
    head = skin.crop((8, 8, 16, 16))
    head.alpha_composite(skin.crop((40, 8, 48, 16)))
    out = head.resize((size, size), Image.NEAREST)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def render_for(name: str, kind: str = "front", size: int = 10) -> bytes:
    skin = None
    if name:
        from .skins import local_skin_path      # сохранённый в редакторе скин важнее сетевого
        lp = local_skin_path(name)
        skin = Image.open(lp).convert("RGBA") if lp.exists() else fetch_skin(name)
    return render_front(skin, size) if kind == "front" else render_head(skin, size)


THUMB_COLORS = {"vanilla": ((70, 130, 60), (30, 60, 30)), "fabric": ((190, 170, 120), (70, 60, 40)),
                "forge": ((90, 110, 140), (25, 35, 55)), "neoforge": ((230, 120, 40), (80, 35, 10)),
                "quilt": ((140, 90, 220), (45, 25, 90))}


def make_thumb(loader: str, w: int = 300, h: int = 150) -> bytes:
    """Запасная картинка карточки сборки, если нет своих скриншотов."""
    c1, c2 = THUMB_COLORS.get(loader, THUMB_COLORS["vanilla"])
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x / w + y / h) / 2
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def blurred_background(path, width: int = 1100, radius: int = 7):
    """Размытый фон окна (как на макетах). None, если файла нет."""
    try:
        from PIL import ImageFilter
        im = Image.open(path).convert("RGB")
        im = im.resize((width, int(im.height * width / im.width)), Image.LANCZOS).filter(ImageFilter.GaussianBlur(radius))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    except Exception:
        return None


def skin_bytes_for(name: str) -> bytes:
    """PNG-текстура скина (локальная -> Ely.by -> заглушка) для 3D-просмотра."""
    skin = None
    if name:
        from .skins import local_skin_path
        lp = local_skin_path(name)
        skin = Image.open(lp).convert("RGBA") if lp.exists() else fetch_skin(name)
    buf = io.BytesIO()
    (skin or placeholder_skin()).save(buf, "PNG")
    return buf.getvalue()
