"""3D-просмотр скина: зажмите левую кнопку и крутите, колесо - масштаб, двойной щелчок - сброс."""
import io
import time

from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import QSizePolicy, QWidget

from . import skin3d, skin_anim


class Skin3DView(QWidget):
    DEFAULT_YAW, DEFAULT_PITCH = 28.0, 8.0

    def __init__(self):
        super().__init__()
        self.setMinimumSize(200, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.yaw, self.pitch, self.zoom = self.DEFAULT_YAW, self.DEFAULT_PITCH, 1.0
        self.faces, self.pix, self.sheet = None, None, None
        self.interactive, self.auto = True, True
        self._drag, self._idle = None, 0
        self._shade_cache = {}
        self.player, self._pose = None, None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)

    # ---------------------------------------------------------------- данные
    def set_skin(self, png_bytes: bytes):
        img = skin3d.legacy_to_modern(Image.open(io.BytesIO(png_bytes)))
        self.faces = skin3d.build_model(skin3d.is_slim(img))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        self.sheet = QImage.fromData(buf.getvalue()).convertToFormat(QImage.Format.Format_ARGB32)
        self.pix = QPixmap.fromImage(self.sheet)
        self._shade_cache.clear()
        self.update()

    def set_animations(self, anims: dict, min_gap: float = 5, max_gap: float = 67, enabled: bool = True):
        """Случайные анимации Blockbench: первая через min..max секунд, дальше с теми же интервалами."""
        if enabled and anims:
            self.player = skin_anim.AnimationPlayer(anims, min_gap, max_gap, now=time.monotonic())
        else:
            self.player, self._pose = None, None

    def configure(self, interactive: bool, auto: bool):
        """interactive=False -> спокойный вид спереди без вращения (для слабых ПК)."""
        self.interactive, self.auto = interactive, auto and interactive
        if not interactive:
            self.yaw, self.pitch = 0.0, 0.0
        self.setCursor(Qt.CursorShape.OpenHandCursor if interactive else Qt.CursorShape.ArrowCursor)
        self.update()

    # ------------------------------------------------------------ жизненный цикл
    def showEvent(self, e):
        self.timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def _tick(self):
        if self.player is not None:
            pose = self.player.tick(time.monotonic())
            if pose is not None or self._pose is not None:
                self._pose = pose
                self.update()
            if pose is not None:
                return                              # пока играет анимация, автовращение стоит
        if self.auto and self._drag is None:
            if self._idle > 0:
                self._idle -= 1
            else:
                self.yaw = (self.yaw + 0.9) % 360
                self.update()

    # ---------------------------------------------------------------- мышь
    def mousePressEvent(self, e):
        if self.interactive and e.button() == Qt.MouseButton.LeftButton:
            self._drag = (e.position(), self.yaw, self.pitch)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            start, yaw0, pitch0 = self._drag
            d = e.position() - start
            self.yaw = (yaw0 + d.x() * 0.8) % 360
            self.pitch = max(-80.0, min(80.0, pitch0 + d.y() * 0.5))
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self._idle = 60                      # после отпускания автовращение возобновится через ~2 с
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, e):
        if self.interactive:
            self.yaw, self.pitch, self.zoom = self.DEFAULT_YAW, self.DEFAULT_PITCH, 1.0
            self.update()

    def wheelEvent(self, e):
        if self.interactive:
            self.zoom = max(0.6, min(2.4, self.zoom * (1.1 if e.angleDelta().y() > 0 else 0.9)))
            self.update()

    # ------------------------------------------------------------- отрисовка
    def _face_image(self, idx, tex, light):
        level = int(light * 8)
        key = (idx, level)
        img = self._shade_cache.get(key)
        if img is None:
            sx, sy, sw, sh = tex
            img = self.sheet.copy(sx, sy, sw, sh)
            alpha = int((1.0 - level / 8.0) * 190)
            if alpha > 0:                          # затемняем только непрозрачные пиксели (оверлей остаётся прозрачным)
                p = QPainter(img)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
                p.fillRect(img.rect(), QColor(0, 0, 0, alpha))
                p.end()
            self._shade_cache[key] = img
        return img

    def paintEvent(self, e):
        p = QPainter(self)
        if self.faces is None or self.sheet is None:
            p.end()
            return
        scale = min(self.width() / 22.0, self.height() / 36.0) * self.zoom
        # индексы граней нужны кэшу: сопоставляем по идентичности словаря
        index = {id(f): i for i, f in enumerate(self.faces)}
        for r in skin3d.project(self.faces, self.yaw, self.pitch, scale, self.width() / 2, self.height() / 2, pose=self._pose):
            m11, m12, m21, m22, dx, dy = r["T"]
            sx, sy, sw, sh = r["tex"]
            img = self._face_image(index[id(r["face"])], r["tex"], r["light"])
            p.setTransform(QTransform(m11, m12, m21, m22, dx, dy))
            p.drawImage(QRectF(0, 0, sw, sh), img)
        p.end()
