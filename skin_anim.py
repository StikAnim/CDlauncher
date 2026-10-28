"""Анимации Blockbench (.animation.json, формат Bedrock) для 3D-персонажа на главной.
Читает кости head/body/arms/legs, интерполирует ключи, переводит оси Blockbench в оси нашей модели
(модель смотрит на +Z, Blockbench - на -Z: поворот вокруг X и Z меняет знак, смещения X и Z тоже)."""
import json
import random
import re
from pathlib import Path

BONE_MAP = {"head": "head", "hat": None, "body": "body", "torso": "body", "jacket": None,
            "rightarm": "r_arm", "armright": "r_arm", "rarm": "r_arm", "leftarm": "l_arm", "armleft": "l_arm", "larm": "l_arm",
            "rightleg": "r_leg", "legright": "r_leg", "rleg": "r_leg", "leftleg": "l_leg", "legleft": "l_leg", "lleg": "l_leg"}


class AnimError(Exception):
    pass


def bone_part(name: str):
    return BONE_MAP.get(re.sub(r"[^a-z]", "", str(name).lower()))


def _num(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return 0.0                      # выражения Molang не поддерживаются - считаем нулём


def _vec(v) -> tuple:
    if isinstance(v, dict):             # {"post": [...], "pre": [...]} - берём post
        v = v.get("post", v.get("pre", [0, 0, 0]))
    if isinstance(v, (int, float, str)):
        v = [v, v, v]
    v = list(v)[:3] + [0] * (3 - len(list(v)[:3]))
    return tuple(_num(x) for x in v)


def _track(raw) -> list:
    """[(время, (x, y, z))] по возрастанию времени."""
    if raw is None:
        return []
    if isinstance(raw, dict) and not (set(raw) <= {"post", "pre", "lerp_mode"}):
        keys = []
        for t, val in raw.items():
            try:
                keys.append((float(t), _vec(val)))
            except ValueError:
                continue
        return sorted(keys, key=lambda k: k[0])
    return [(0.0, _vec(raw))]


class Animation:
    def __init__(self, name, length, loop, bones):
        self.name, self.length, self.loop, self.bones = name, length, loop, bones   # bones: {part: {"rotation": [...], "position": [...]}}

    def sample(self, t: float) -> dict:
        """Поза на момент t: {часть: {'rot': (rx,ry,rz), 'pos': (x,y,z)}} уже в осях нашей модели."""
        if self.loop and self.length > 0:
            t = t % self.length
        pose = {}
        for part, tracks in self.bones.items():
            rot = _interp(tracks.get("rotation", []), t)
            pos = _interp(tracks.get("position", []), t)
            pose[part] = {"rot": (-rot[0], rot[1], -rot[2]), "pos": (-pos[0], pos[1], -pos[2])}
        return pose


def _interp(keys: list, t: float) -> tuple:
    if not keys:
        return (0.0, 0.0, 0.0)
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, a), (t1, b) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            k = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(a[i] + (b[i] - a[i]) * k for i in range(3))
    return keys[-1][1]


def parse(data: dict) -> dict:
    """{имя: Animation} из словаря Blockbench. AnimError, если анимаций нет."""
    block = data.get("animations") if isinstance(data, dict) else None
    if not isinstance(block, dict) or not block:
        raise AnimError("В файле нет раздела animations (экспортируйте анимацию из Blockbench: File → Export → Export Animations)")
    out = {}
    for name, a in block.items():
        bones = {}
        for bone, tr in (a.get("bones") or {}).items():
            part = bone_part(bone)
            if part:
                bones.setdefault(part, {})
                for kind in ("rotation", "position"):
                    if kind in tr:
                        bones[part][kind] = _track(tr[kind])
        if not bones:
            continue
        keys = [k[0] for tracks in bones.values() for tr in tracks.values() for k in tr]
        length = float(a.get("animation_length") or (max(keys) if keys else 1.0)) or 1.0
        loop = a.get("loop") is True
        out[str(name)] = Animation(str(name), length, loop, bones)
    if not out:
        raise AnimError("В анимации нет костей head/body/arms/legs, подходящих для персонажа")
    return out


def load_file(path: Path) -> dict:
    try:
        return parse(json.loads(Path(path).read_text("utf-8-sig")))
    except ValueError as e:
        raise AnimError(f"Не JSON: {e}")


def load_dir(folder: Path) -> dict:
    anims = {}
    for f in sorted(Path(folder).glob("*.json")) if Path(folder).exists() else []:
        try:
            for name, a in load_file(f).items():
                anims[f"{f.stem}:{name}"] = a
        except AnimError:
            continue
    return anims


class AnimationPlayer:
    """Раз в случайный промежуток [min_gap, max_gap] секунд запускает случайную анимацию и возвращает позу."""

    def __init__(self, anims: dict, min_gap: float = 5, max_gap: float = 67, rng=None, now: float = 0.0):
        self.anims, self.rng = dict(anims), rng or random.Random()
        self.min_gap, self.max_gap = float(min_gap), float(max_gap)
        self.current, self.start, self.last = None, 0.0, None
        self.next_at = now + self._gap()

    def _gap(self) -> float:
        lo, hi = sorted((max(0.0, self.min_gap), max(0.0, self.max_gap)))
        return self.rng.uniform(lo, hi)

    def set_range(self, lo, hi):
        self.min_gap, self.max_gap = float(lo), float(hi)

    def tick(self, now: float):
        """Поза (dict) пока играет анимация, иначе None."""
        if self.current is None:
            if self.anims and now >= self.next_at:
                names = [n for n in self.anims if n != self.last] or list(self.anims)
                self.last = self.rng.choice(names)
                self.current, self.start = self.anims[self.last], now
            else:
                return None
        a = self.current
        duration = a.length * (2 if a.loop else 1)
        t = now - self.start
        if t >= duration:
            self.current, self.next_at = None, now + self._gap()
            return None
        return a.sample(t)
