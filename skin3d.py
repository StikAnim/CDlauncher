"""3D-модель игрока Minecraft без Qt: геометрия, UV-развёртка и ортографическая проекция.
Каждая грань - прямоугольник; при ортографической проекции текстура ложится на неё ТОЧНО аффинным преобразованием,
поэтому Qt рисует грани через QTransform без перспективных искажений."""
import math

from PIL import Image


# ------------------------------------------------------------------- работа со скином
def is_slim(img: Image.Image) -> bool:
    """Тонкие руки (Alex): последние два столбца текстуры руки полностью прозрачны."""
    if img.height < 64:
        return False
    px = img.convert("RGBA")
    return all(px.getpixel((x, y))[3] == 0 for x in (54, 55) for y in range(20, 32))


def mirror_box(img, su, sv, du, dv, w, h, d):
    """Копирует коробку-текстуру (su,sv) в (du,dv) зеркально (как делает сам Minecraft для старых скинов)."""
    def crop(u, v, ww, hh):
        return img.crop((u, v, u + ww, v + hh)).transpose(Image.FLIP_LEFT_RIGHT)
    src = {"right": crop(su, sv + d, d, h), "front": crop(su + d, sv + d, w, h), "left": crop(su + d + w, sv + d, d, h),
           "back": crop(su + 2 * d + w, sv + d, w, h), "top": crop(su + d, sv, w, d), "bottom": crop(su + d + w, sv, w, d)}
    img.paste(src["left"], (du, dv + d))                    # правая <-> левая грани меняются местами
    img.paste(src["front"], (du + d, dv + d))
    img.paste(src["right"], (du + d + w, dv + d))
    img.paste(src["back"], (du + 2 * d + w, dv + d))
    img.paste(src["top"], (du + d, dv))
    img.paste(src["bottom"], (du + d + w, dv))


def legacy_to_modern(img: Image.Image) -> Image.Image:
    """Скин 64x32 -> 64x64: левые рука и нога получаются зеркалом правых."""
    img = img.convert("RGBA")
    if img.height != 32:
        return img
    out = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    out.paste(img, (0, 0))
    mirror_box(out, 0, 16, 16, 48, 4, 12, 4)
    mirror_box(out, 40, 16, 32, 48, 4, 12, 4)
    return out


# ------------------------------------------------------------------------ геометрия
def box_faces(part, size, center, uv, inflate=0.0, layer="base"):
    w, h, d = size
    u, v = uv
    cx, cy, cz = center
    W, H, D = (w + 2 * inflate) / 2, (h + 2 * inflate) / 2, (d + 2 * inflate) / 2

    def pt(x, y, z):
        return (cx + x, cy + y, cz + z)

    # (имя, нормаль, P0 - левый верх текстуры, P1 - правый верх, P3 - левый низ, прямоугольник текстуры)
    spec = [
        ("front", (0, 0, 1), pt(-W, H, D), pt(W, H, D), pt(-W, -H, D), (u + d, v + d, w, h)),
        ("back", (0, 0, -1), pt(W, H, -D), pt(-W, H, -D), pt(W, -H, -D), (u + 2 * d + w, v + d, w, h)),
        ("right", (-1, 0, 0), pt(-W, H, -D), pt(-W, H, D), pt(-W, -H, -D), (u, v + d, d, h)),
        ("left", (1, 0, 0), pt(W, H, D), pt(W, H, -D), pt(W, -H, D), (u + d + w, v + d, d, h)),
        ("top", (0, 1, 0), pt(-W, H, -D), pt(W, H, -D), pt(-W, H, D), (u + d, v, w, d)),
        ("bottom", (0, -1, 0), pt(-W, -H, D), pt(W, -H, D), pt(-W, -H, -D), (u + d + w, v, w, d)),
    ]
    return [{"part": part, "face": n, "layer": layer, "normal": nm, "p0": p0, "p1": p1, "p3": p3, "tex": tex}
            for n, nm, p0, p1, p3, tex in spec]


def build_model(slim: bool = False) -> list:
    """Игрок целиком: 6 частей x (база + оверлей). Ноги стоят на y=0, рост 32."""
    aw = 3 if slim else 4
    ax = 4 + aw / 2
    parts = [
        ("head", (8, 8, 8), (0, 28, 0), (0, 0), (32, 0)),
        ("body", (8, 12, 4), (0, 18, 0), (16, 16), (16, 32)),
        ("r_arm", (aw, 12, 4), (-ax, 18, 0), (40, 16), (40, 32)),
        ("l_arm", (aw, 12, 4), (ax, 18, 0), (32, 48), (48, 48)),
        ("r_leg", (4, 12, 4), (-2, 6, 0), (0, 16), (0, 32)),
        ("l_leg", (4, 12, 4), (2, 6, 0), (16, 48), (0, 48)),
    ]
    faces = []
    for name, size, center, uv, ov in parts:
        faces += box_faces(name, size, center, uv, 0.0, "base")
        faces += box_faces(name, size, center, ov, 0.5, "overlay")
    return faces


# ------------------------------------------------------------------------ проекция
PIVOT = {"head": (0, 24, 0), "body": (0, 12, 0), "r_arm": (-5, 22, 0), "l_arm": (5, 22, 0),
         "r_leg": (-2, 12, 0), "l_leg": (2, 12, 0)}
PARENT = {"head": "body", "r_arm": "body", "l_arm": "body"}       # голова и руки двигаются вместе с корпусом


def _euler(v, rot):
    """Поворот на (rx, ry, rz) градусов в порядке Z·Y·X (как в Blockbench / three.js)."""
    x, y, z = v
    rx, ry, rz = (math.radians(a) for a in rot)
    y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
    x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
    x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
    return x, y, z


def pose_point(part, p, pose):
    """Точка части тела с учётом позы анимации и иерархии (часть -> корпус)."""
    if not pose:
        return p
    name = part
    while name:
        t = pose.get(name)
        if t:
            pv = PIVOT[name]
            q = _euler((p[0] - pv[0], p[1] - pv[1], p[2] - pv[2]), t.get("rot", (0, 0, 0)))
            pos = t.get("pos", (0, 0, 0))
            p = (pv[0] + q[0] + pos[0], pv[1] + q[1] + pos[1], pv[2] + q[2] + pos[2])
        name = PARENT.get(name)
    return p


def pose_normal(part, n, pose):
    if not pose:
        return n
    name = part
    while name:
        t = pose.get(name)
        if t:
            n = _euler(n, t.get("rot", (0, 0, 0)))
        name = PARENT.get(name)
    return n


def _rot(v, cy, sy, cp, sp):
    x, y, z = v
    x, z = x * cy + z * sy, -x * sy + z * cy          # поворот вокруг Y (yaw)
    y, z = y * cp - z * sp, y * sp + z * cp            # наклон вокруг X (pitch)
    return x, y, z


LIGHT = (-0.35, 0.6, 0.72)


def project(faces, yaw_deg, pitch_deg, scale, cx, cy, center_y=16.0, pose=None) -> list:
    """Видимые грани, от дальних к ближним. Для каждой: T=(m11,m12,m21,m22,dx,dy) переводит координаты
    текстурного прямоугольника (0..sw, 0..sh) в экранные, light 0..1, pts - четыре угла."""
    cyw, syw = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    cp, sp = math.cos(math.radians(pitch_deg)), math.sin(math.radians(pitch_deg))
    ln = math.sqrt(sum(c * c for c in LIGHT))
    out = []
    for f in faces:
        nx, ny, nz = _rot(pose_normal(f["part"], f["normal"], pose), cyw, syw, cp, sp)
        if nz <= 1e-4:                                  # грань смотрит от камеры
            continue

        def scr(p):
            p = pose_point(f["part"], p, pose)
            x, y, z = _rot((p[0], p[1] - center_y, p[2]), cyw, syw, cp, sp)
            return cx + x * scale, cy - y * scale, z

        a, b, d = scr(f["p0"]), scr(f["p1"]), scr(f["p3"])
        c4 = (b[0] + d[0] - a[0], b[1] + d[1] - a[1])
        sx, sy, sw, sh = f["tex"]
        T = ((b[0] - a[0]) / sw, (b[1] - a[1]) / sw, (d[0] - a[0]) / sh, (d[1] - a[1]) / sh, a[0], a[1])
        depth = (a[2] + b[2] + d[2]) / 3 + (0.01 if f["layer"] == "overlay" else 0)
        light = 0.55 + 0.45 * max(0.0, (nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]) / ln)
        out.append({"face": f, "tex": f["tex"], "T": T, "light": min(light, 1.0), "depth": depth,
                    "pts": [(a[0], a[1]), (b[0], b[1]), c4, (d[0], d[1])]})
    out.sort(key=lambda r: r["depth"])
    return out


def apply_T(T, x, y):
    """Для тестов: образ точки текстурного прямоугольника."""
    m11, m12, m21, m22, dx, dy = T
    return m11 * x + m21 * y + dx, m12 * x + m22 * y + dy
