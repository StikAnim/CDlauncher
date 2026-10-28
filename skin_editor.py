"""Встроенный редактор скинов 64x64 / 64x32: кисть, ластик, пипетка, заливка, палитра, слои, предпросмотр."""
import io

from PIL import Image
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                             QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from . import skin_layers as L
from . import skin_render, skins
from .dialogs import RoundedDialog
from .i18n import tr

PALETTE = ["#000000", "#ffffff", "#7f7f7f", "#c8c8c8", "#8b4513", "#d2a679", "#ff0000", "#ff8000",
           "#ffd400", "#32c832", "#006400", "#00c8c8", "#1e64ff", "#00008b", "#8a2be2", "#ff69b4"]


def image_to_png(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def png_to_image(data: bytes) -> QImage:
    img = QImage.fromData(data)
    return img.convertToFormat(QImage.Format.Format_ARGB32)


class SkinCanvas(QWidget):
    ZOOM = 8

    def __init__(self, on_change, on_pick):
        super().__init__()
        self.on_change, self.on_pick = on_change, on_pick
        self.tool, self.color, self.size_px = "brush", QColor("#22d3ee"), 1
        self.layer, self.lock, self.show_overlay = "base", True, True
        self.undo_stack = []
        self.img = QImage(64, 64, QImage.Format.Format_ARGB32)
        self.img.fill(Qt.GlobalColor.transparent)
        self._resize()

    @property
    def legacy(self) -> bool:
        return self.img.height() == 32

    def _resize(self):
        self.setFixedSize(self.img.width() * self.ZOOM, self.img.height() * self.ZOOM)
        self.update()

    def set_image(self, img: QImage):
        w, h = img.width(), img.height()
        if (w, h) not in ((64, 64), (64, 32)):
            canvas = QImage(64, 64, QImage.Format.Format_ARGB32)
            canvas.fill(Qt.GlobalColor.transparent)
            p = QPainter(canvas)
            p.drawImage(0, 0, img.scaled(64, 64) if w > 64 or h > 64 else img)
            p.end()
            img = canvas
        self.img = img.convertToFormat(QImage.Format.Format_ARGB32)
        self.undo_stack.clear()
        self._resize()
        self.on_change()

    def set_format(self, legacy: bool):
        if legacy == self.legacy:
            return
        new = QImage(64, 32 if legacy else 64, QImage.Format.Format_ARGB32)
        new.fill(Qt.GlobalColor.transparent)
        p = QPainter(new)
        p.drawImage(0, 0, self.img)
        p.end()
        self.push_undo()
        self.img = new
        self._resize()
        self.on_change()

    def push_undo(self):
        self.undo_stack.append(self.img.copy())
        del self.undo_stack[:-30]

    def undo(self):
        if self.undo_stack:
            self.img = self.undo_stack.pop()
            self._resize()
            self.on_change()

    def _allowed(self, x, y):
        return L.allowed(x, y, self.img.width(), self.img.height(), self.legacy, self.layer, self.lock)

    # ----------------------------------------------------------------- рисование
    def paintEvent(self, e):
        p = QPainter(self)
        z = self.ZOOM
        for cy in range(0, self.height(), z):                       # шахматка прозрачности
            for cx in range(0, self.width(), z):
                p.fillRect(cx, cy, z, z, QColor("#3a3f4b") if (cx // z + cy // z) % 2 else QColor("#2b2f39"))
        view = self.img.copy()
        if not self.show_overlay:
            for x, y in L.overlay_pixels(view.width(), view.height(), self.legacy):
                view.setPixelColor(x, y, QColor(0, 0, 0, 0))
        p.drawImage(self.rect(), view)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        for gx in range(0, self.width(), z):
            p.drawLine(gx, 0, gx, self.height())
        for gy in range(0, self.height(), z):
            p.drawLine(0, gy, self.width(), gy)
        p.setPen(QPen(self.color, 2, Qt.PenStyle.DashLine))          # контур активного слоя
        for rx, ry, rw, rh in L.layer_rects(self.legacy, self.layer):
            p.drawRect(rx * z, ry * z, rw * z, rh * z)
        p.end()

    def _xy(self, e):
        pos = e.position()
        return int(pos.x() // self.ZOOM), int(pos.y() // self.ZOOM)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self.tool != "picker":
                self.push_undo()
            self._apply(*self._xy(e))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self.tool in ("brush", "eraser"):
            self._apply(*self._xy(e))

    def _put(self, x, y, color):
        if self._allowed(x, y):
            self.img.setPixelColor(x, y, color)

    def _apply(self, x, y):
        if not (0 <= x < self.img.width() and 0 <= y < self.img.height()):
            return
        if self.tool == "picker":
            c = self.img.pixelColor(x, y)
            if c.alpha() > 0:
                self.color = c
                self.on_pick(c)
            return
        if self.tool in ("brush", "eraser"):
            col = self.color if self.tool == "brush" else QColor(0, 0, 0, 0)
            for dy in range(self.size_px):
                for dx in range(self.size_px):
                    self._put(x + dx, y + dy, col)
        elif self.tool == "fill":
            cells = L.flood((x, y), self.img.width(), self.img.height(),
                            lambda a, b: self.img.pixel(a, b), self._allowed)
            for cx, cy in cells:
                self.img.setPixelColor(cx, cy, self.color)
        self.update()
        self.on_change()


class SkinEditorDialog(RoundedDialog):
    def __init__(self, win, account):
        super().__init__(win)
        self.win, self.account = win, account
        self.setWindowTitle(f"{tr('skin_editor_title')} — {account['name']}")
        root = QHBoxLayout()
        self.set_content(root)
        self.canvas = SkinCanvas(self.schedule_preview, self.set_color_label)
        root.addWidget(self.canvas)

        side = QVBoxLayout()
        row = QHBoxLayout()
        self.tool_btns = {}
        for key, label in (("brush", "tool_brush"), ("eraser", "tool_eraser"), ("picker", "tool_picker"), ("fill", "tool_fill")):
            b = QPushButton(tr(label))
            b.setCheckable(True)
            b.setObjectName("sub")
            b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            row.addWidget(b)
            self.tool_btns[key] = b
        self.tool_btns["brush"].setChecked(True)
        side.addLayout(row)

        crow = QHBoxLayout()
        self.color_btn = QPushButton(tr("color"))
        self.color_btn.clicked.connect(self.pick_color)
        self.size = QSpinBox()
        self.size.setRange(1, 4)
        self.size.valueChanged.connect(lambda v: setattr(self.canvas, "size_px", v))
        crow.addWidget(self.color_btn, 1)
        crow.addWidget(QLabel(tr("brush_size")))
        crow.addWidget(self.size)
        side.addLayout(crow)
        grid = QGridLayout()
        for i, hx in enumerate(PALETTE):
            b = QPushButton()
            b.setFixedSize(26, 26)
            b.setStyleSheet(f"background:{hx}; border:1px solid rgba(255,255,255,.4); border-radius:4px; padding:0;")
            b.clicked.connect(lambda _=False, c=hx: self.set_color(QColor(c)))
            grid.addWidget(b, i // 8, i % 8)
        side.addLayout(grid)

        self.layer = QComboBox()
        self.layer.addItem(tr("layer_base"), "base")
        self.layer.addItem(tr("layer_overlay"), "overlay")
        self.layer.currentIndexChanged.connect(lambda: self._layer())
        self.lock = QCheckBox(tr("lock_layer"))
        self.lock.setChecked(True)
        self.lock.toggled.connect(lambda v: (setattr(self.canvas, "lock", v), self.canvas.update()))
        self.overlay_vis = QCheckBox(tr("show_overlay"))
        self.overlay_vis.setChecked(True)
        self.overlay_vis.toggled.connect(lambda v: (setattr(self.canvas, "show_overlay", v), self.canvas.update(), self.schedule_preview()))
        self.fmt = QComboBox()
        self.fmt.addItems(["64x64", "64x32"])
        self.fmt.currentIndexChanged.connect(lambda i: self.canvas.set_format(i == 1))
        for w in (self.layer, self.lock, self.overlay_vis):
            side.addWidget(w)
        frow = QHBoxLayout()
        frow.addWidget(QLabel(tr("format_label")))
        frow.addWidget(self.fmt, 1)
        side.addLayout(frow)

        self.preview = QLabel()
        self.preview.setFixedSize(112, 224)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)

        r1 = QHBoxLayout()
        for label, fn in (("undo", self.canvas.undo), ("import_png", self.import_png),
                          ("import_current", self.import_current), ("export_png", self.export_png)):
            b = QPushButton(tr(label))
            b.setObjectName("ghost")
            b.clicked.connect(lambda _=False, f=fn: f())
            r1.addWidget(b)
        side.addLayout(r1)
        self.save_btn = QPushButton(tr("save_wear"))
        self.save_btn.setObjectName("play")
        self.save_btn.clicked.connect(self.save_wear)
        side.addWidget(self.save_btn)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setMaximumWidth(380)
        side.addWidget(self.status)
        root.addLayout(side)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.update_preview)
        self.load_initial()

    # ------------------------------------------------------------------ состояние
    def set_tool(self, k):
        self.canvas.tool = k
        for key, b in self.tool_btns.items():
            b.setChecked(key == k)

    def _layer(self):
        self.canvas.layer = self.layer.currentData()
        self.canvas.update()

    def set_color(self, c):
        self.canvas.color = c
        self.set_color_label(c)

    def set_color_label(self, c):
        fg = "#000" if c.lightness() > 128 else "#fff"
        self.color_btn.setText(f"{tr('color')}  {c.name()}")
        self.color_btn.setStyleSheet(f"background:{c.name()}; color:{fg};")

    def pick_color(self):
        c = QColorDialog.getColor(self.canvas.color, self, tr("color"))
        if c.isValid():
            self.set_color(c)

    def schedule_preview(self):
        self.timer.start(120)

    def update_preview(self):
        try:
            img = Image.open(io.BytesIO(image_to_png(self.canvas.img))).convert("RGBA")
            if not self.canvas.show_overlay:
                for x, y in L.overlay_pixels(img.width, img.height, img.height == 32):
                    img.putpixel((x, y), (0, 0, 0, 0))
            data = skin_render.render_front(img, 7)
            pm = QPixmap()
            pm.loadFromData(data)
            self.preview.setPixmap(pm.scaled(112, 224, Qt.AspectRatioMode.KeepAspectRatio))
        except Exception as e:
            self.status.setText(str(e))

    # -------------------------------------------------------------- загрузка/экспорт
    def load_initial(self):
        p = skins.local_skin_path(self.account["name"])
        if p.exists():
            self.canvas.set_image(png_to_image(p.read_bytes()))
        else:
            self.canvas.set_image(png_to_image(self._pil_png(skin_render.fetch_skin(self.account["name"]) or skin_render.placeholder_skin())))
        self.fmt.blockSignals(True)
        self.fmt.setCurrentIndex(1 if self.canvas.legacy else 0)
        self.fmt.blockSignals(False)
        self.set_color_label(self.canvas.color)
        self.update_preview()

    @staticmethod
    def _pil_png(img):
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    def import_png(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("import_png"), "", "PNG (*.png)")
        if path:
            self.canvas.push_undo()
            self.canvas.set_image(png_to_image(open(path, "rb").read()))

    def import_current(self):
        skin = skin_render.fetch_skin(self.account["name"])
        if skin:
            self.canvas.push_undo()
            self.canvas.set_image(png_to_image(self._pil_png(skin)))

    def export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("export_png"), f"{self.account['name']}.png", "PNG (*.png)")
        if path:
            open(path, "wb").write(image_to_png(self.canvas.img))

    # ---------------------------------------------------------- СОХРАНИТЬ И НАДЕТЬ
    def save_wear(self):
        png = image_to_png(self.canvas.img)
        acc = self.account
        try:
            path = skins.save_local_skin(acc["name"], png)          # всегда: .cdlauncher/skins/<ник>.png
            if acc["type"] == "ely":
                QDesktopServices.openUrl(QUrl(skins.ELY_SKIN_PAGE))
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
                self.status.setText(tr("skin_ely_note"))
            elif acc["type"] == "microsoft":
                fresh = self.win.accounts.ensure_fresh(acc, self.win.settings)
                skins.upload_microsoft(fresh["access_token"], png)
                self.status.setText(tr("skin_ms_ok"))
            else:
                self.status.setText(tr("skin_saved_local", path=str(path)))
            self.win.pages["home"].reload_skin()
        except Exception as e:
            self.status.setText(tr("skin_error", e=e))
