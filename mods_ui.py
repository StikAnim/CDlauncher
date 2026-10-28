"""Каталог модов в стиле Modrinth: карточки с иконками, окно с описанием / галереей / версиями."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QPushButton, QScrollArea,
                             QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from . import catalog, imgcache
from .dialogs import RoundedDialog
from .i18n import tr
from .ui import _ALIVE, ClickFrame, ClickLabel, Worker, fit_pixmap, muted


def fmt_num(n) -> str:
    n = int(n or 0)
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class ImageLoader(QThread):
    """Скачивает картинки параллельно и сообщает о каждой по мере готовности: loaded(ключ, путь_к_png)."""
    loaded = pyqtSignal(str, str)

    def __init__(self, items, max_side=0):
        super().__init__()
        self.items, self.max_side = list(items), max_side
        _ALIVE.add(self)
        self.finished.connect(lambda: _ALIVE.discard(self))

    def run(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(imgcache.fetch_image, url, self.max_side): key for key, url in self.items if url}
            for fut in as_completed(futs):
                path = fut.result()
                if path:
                    self.loaded.emit(futs[fut], str(path))


class ModCard(ClickFrame):
    ICON = 64

    def __init__(self, hit: dict, rich: bool, installed: bool, on_install, on_open, on_remove=None):
        super().__init__()
        self.hit = hit
        self.setObjectName("card2")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        self.icon = QLabel()
        self.icon.setFixedSize(self.ICON, self.ICON)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet("background: rgba(255,255,255,0.06); border-radius: 10px;")
        self.icon.setVisible(rich)
        lay.addWidget(self.icon)
        col = QVBoxLayout()
        title = QLabel(f"<b>{hit.get('title', '?')}</b> <span style='opacity:0.6'>— {hit.get('author', '')}</span>")
        desc = muted(hit.get("description", ""))
        desc.setMaximumHeight(40)
        cats = ", ".join(hit.get("display_categories") or hit.get("categories", [])[:4])
        meta = muted(f"⬇ {fmt_num(hit.get('downloads'))}   ♥ {fmt_num(hit.get('follows'))}   {cats}")
        for w in (title, desc, meta):
            col.addWidget(w)
        lay.addLayout(col, 1)
        self.btn = QPushButton()
        self.btn.setObjectName("accent")
        self.btn.clicked.connect(lambda _=False: on_install(hit))
        self.btn_rm = QPushButton(tr("remove"))                  # «Удалить» рядом с уже установленным
        self.btn_rm.setObjectName("danger")
        self.btn_rm.clicked.connect(lambda _=False: on_remove and on_remove(hit))
        lay.addWidget(self.btn)
        lay.addWidget(self.btn_rm)
        self.set_installed(installed)
        if rich:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.clicked.connect(lambda: on_open(hit))

    def set_installed(self, installed: bool):
        self.btn.setText(tr("installed_tag") if installed else tr("install"))
        self.btn.setEnabled(not installed)
        self.btn_rm.setVisible(installed)

    def set_icon(self, path: str):
        pm = QPixmap(path)
        if not pm.isNull():
            self.icon.setPixmap(fit_pixmap(pm, self.ICON, self.ICON))


class ImageViewer(RoundedDialog):
    def __init__(self, parent, title: str, path: str):
        super().__init__(parent)
        self.setWindowTitle(title or tr("gallery"))
        lay = QVBoxLayout()
        self.set_content(lay)
        lab = QLabel()
        pm = QPixmap(path)
        lab.setPixmap(pm.scaled(960, 600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lab)
        if title:
            lay.addWidget(muted(title))


class ModDetailDialog(RoundedDialog):
    """Описание, галерея, версии и ссылки проекта (Modrinth или CurseForge). Загрузка в фоне."""

    def __init__(self, win, hit: dict, profile: dict, on_install):
        super().__init__(win)
        self.win, self.hit, self.profile, self.on_install = win, hit, profile, on_install
        self.setWindowTitle(hit.get("title", "Mod"))
        self.resize(880, 660)
        root = QVBoxLayout()
        self.set_content(root)
        head = QHBoxLayout()
        self.icon = QLabel()
        self.icon.setFixedSize(96, 96)
        self.icon.setStyleSheet("background: rgba(255,255,255,0.06); border-radius: 14px;")
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self.icon)
        col = QVBoxLayout()
        src = "CurseForge" if hit.get("source") == "curseforge" else "Modrinth"
        self.title = QLabel(f"<span style='font-size:20px'><b>{hit.get('title', '')}</b></span>  — {hit.get('author', '')}  <small>[{src}]</small>")
        self.summary = muted(hit.get("description", ""))
        self.meta = muted("")
        for w in (self.title, self.summary, self.meta):
            col.addWidget(w)
        head.addLayout(col, 1)
        self.btn = QPushButton(tr("install"))
        self.btn.setObjectName("accent")
        self.btn.clicked.connect(lambda: (self.on_install(hit), self.btn.setEnabled(False), self.btn.setText(tr("installed_tag"))))
        head.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)
        self.links = QHBoxLayout()
        root.addLayout(self.links)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        self.body.setPlainText(tr("loading"))
        self.tabs.addTab(self.body, tr("description"))
        self.gallery_area = QScrollArea()
        self.gallery_area.setWidgetResizable(True)
        self.gallery_inner = QWidget()
        self.gallery_grid = QGridLayout(self.gallery_inner)
        self.gallery_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.gallery_area.setWidget(self.gallery_inner)
        self.versions = QListWidget()
        self.tabs.addTab(self.versions, tr("versions_tab"))
        self.thumbs, self.full_urls, self.loaders = {}, {}, []
        self.w = Worker(lambda rep: catalog.details(hit, profile, win.settings))
        self.w.done.connect(self.fill)
        self.w.failed.connect(lambda m: self.body.setPlainText(tr("err_generic", e=m)))
        self.w.start()
        if hit.get("icon_url"):
            self.start_loader([("icon", hit["icon_url"])], 128)

    def start_loader(self, items, max_side):
        loader = ImageLoader(items, max_side)
        loader.loaded.connect(self.on_image)
        self.loaders.append(loader)
        loader.start()

    def on_image(self, key, path):
        if key == "icon":
            self.icon.setPixmap(fit_pixmap(QPixmap(path), 96, 96))
        elif key.startswith("g"):
            lab = self.thumbs.get(key)
            if lab is not None:
                lab.setPixmap(QPixmap(path).scaled(240, 135, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        elif key.startswith("full:"):
            ImageViewer(self, self.full_urls.get(key, ""), path).exec()

    def fill(self, d: dict):
        self.title.setText(f"<span style='font-size:20px'><b>{d['title']}</b></span>  — {self.hit.get('author', '')}")
        self.summary.setText(d["summary"])
        self.meta.setText(d["meta"])
        if d["body_format"] == "html":
            self.body.setHtml(d["body"])
        else:
            self.body.setMarkdown(d["body"])
        for label, url in d["links"]:
            if url:
                b = QPushButton(label)
                b.setObjectName("ghost")
                b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
                self.links.addWidget(b)
        self.links.addStretch(1)
        gallery = d["gallery"]
        if gallery:
            self.tabs.insertTab(1, self.gallery_area, f"{tr('gallery')} ({len(gallery)})")
            items = []
            for i, g in enumerate(gallery):
                key = f"g{i}"
                cell = QFrame()
                cell.setObjectName("card2")
                cl = QVBoxLayout(cell)
                thumb = ClickLabel()
                thumb.setFixedSize(240, 135)
                thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
                thumb.setCursor(Qt.CursorShape.PointingHandCursor)
                cap = muted(g.get("title") or "")
                cap.setMaximumWidth(240)
                cl.addWidget(thumb)
                cl.addWidget(cap)
                self.thumbs[key] = thumb
                thumb.clicked.connect(lambda k=key, u=g["url"], t=g.get("title", ""): self.open_full(k, u, t))
                self.gallery_grid.addWidget(cell, i // 3, i % 3)
                items.append((key, g.get("thumb") or g["url"]))
            self.start_loader(items, 480)
        self.versions.clear()
        for v in d["versions"] or [tr("no_versions")]:
            self.versions.addItem(v)

    def open_full(self, key, url, title):
        k = f"full:{key}"
        self.full_urls[k] = title
        self.start_loader([(k, url)], 1800)
