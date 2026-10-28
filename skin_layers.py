"""Логика редактора скинов без Qt (тестируется отдельно): слои «база / оверлей» и заливка."""
from collections import deque

# (x, y, ширина, высота). Формат 64x64 (1.8+)
BASE_64 = [(0, 0, 32, 16), (0, 16, 56, 16), (16, 48, 32, 16)]
OVER_64 = [(32, 0, 32, 16), (0, 32, 56, 16), (0, 48, 16, 16), (48, 48, 16, 16)]
# Формат 64x32 (старый): оверлей только шапка
BASE_32 = [(0, 0, 32, 16), (0, 16, 56, 16)]
OVER_32 = [(32, 0, 32, 16)]


def layer_rects(legacy: bool, layer: str) -> list:
    if legacy:
        return OVER_32 if layer == "overlay" else BASE_32
    return OVER_64 if layer == "overlay" else BASE_64


def in_rects(x: int, y: int, rects: list) -> bool:
    return any(rx <= x < rx + rw and ry <= y < ry + rh for rx, ry, rw, rh in rects)


def allowed(x: int, y: int, width: int, height: int, legacy: bool, layer: str, lock: bool) -> bool:
    if not (0 <= x < width and 0 <= y < height):
        return False
    return in_rects(x, y, layer_rects(legacy, layer)) if lock else True


def flood(start, width, height, get_color, is_allowed) -> set:
    """Заливка 4-связной области одного цвета, только по разрешённым пикселям."""
    sx, sy = start
    if not is_allowed(sx, sy):
        return set()
    target, seen, q = get_color(sx, sy), {(sx, sy)}, deque([(sx, sy)])
    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) not in seen and 0 <= nx < width and 0 <= ny < height \
                    and is_allowed(nx, ny) and get_color(nx, ny) == target:
                seen.add((nx, ny))
                q.append((nx, ny))
    return seen


def overlay_pixels(width: int, height: int, legacy: bool):
    """Все координаты оверлея (чтобы скрывать слой при просмотре)."""
    for rx, ry, rw, rh in layer_rects(legacy, "overlay"):
        for y in range(ry, min(ry + rh, height)):
            for x in range(rx, min(rx + rw, width)):
                yield x, y
