"""Окна лаунчера без системной рамки: скруглённые углы как у главного окна, своя шапка, перетаскивание."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QPushButton, QToolButton, QVBoxLayout,
                             QWidget)

from .config import resource_path
from .i18n import tr


class _Bar(QFrame):
    def __init__(self, dlg, title):
        super().__init__()
        self.dlg = dlg
        self.setObjectName("dlgBar")
        self.setFixedHeight(38)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 6, 0)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(resource_path("assets/app_icon_32.png"))).scaled(
            24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))   # без сглаживания
        self.title = QLabel(title)
        self.title.setObjectName("h2")
        close = QToolButton()
        close.setText("✕")
        close.setObjectName("wbtnClose")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(lambda _=False: dlg.reject())
        h.addWidget(logo)
        h.addSpacing(8)
        h.addWidget(self.title, 1)
        h.addWidget(close)

    def mousePressEvent(self, e):
        wh = self.dlg.windowHandle()
        if e.button() == Qt.MouseButton.LeftButton and wh:
            wh.startSystemMove()


class RoundedDialog(QDialog):
    """Базовый класс всех окон: подклассы кладут содержимое через set_content(layout)."""

    def __init__(self, parent=None, title=""):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)
        self._frame = QFrame()
        self._frame.setObjectName("dlgFrame")
        outer.addWidget(self._frame)
        fl = QVBoxLayout(self._frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)
        self._bar = _Bar(self, title)
        fl.addWidget(self._bar)
        self._body = QWidget()
        fl.addWidget(self._body, 1)

    def set_content(self, layout):
        layout.setContentsMargins(16, 14, 16, 16)
        self._body.setLayout(layout)

    def setWindowTitle(self, text):
        super().setWindowTitle(text)
        self._bar.title.setText(text)


def _buttons(dlg, lay, ok_text, cancel_text=None):
    row = QHBoxLayout()
    row.addStretch(1)
    if cancel_text:
        b = QPushButton(cancel_text)
        b.setObjectName("ghost")
        b.clicked.connect(lambda _=False: dlg.reject())
        row.addWidget(b)
    ok = QPushButton(ok_text)
    ok.setObjectName("accent")
    ok.clicked.connect(lambda _=False: dlg.accept())
    row.addWidget(ok)
    lay.addLayout(row)


def _text(text):
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setMinimumWidth(360)
    lab.setMaximumWidth(520)
    return lab


def confirm(parent, text: str) -> bool:
    d = RoundedDialog(parent, "CD Launcher")
    lay = QVBoxLayout()
    d.set_content(lay)
    lay.addWidget(_text(text))
    _buttons(d, lay, tr("yes"), tr("no"))
    return d.exec() == QDialog.DialogCode.Accepted


def info(parent, text: str):
    d = RoundedDialog(parent, "CD Launcher")
    lay = QVBoxLayout()
    d.set_content(lay)
    lay.addWidget(_text(text))
    _buttons(d, lay, tr("ok"))
    d.exec()


def choose(parent, title: str, label: str, items: list):
    """Выбор из списка; возвращает строку или None."""
    d = RoundedDialog(parent, title)
    lay = QVBoxLayout()
    d.set_content(lay)
    lay.addWidget(_text(label))
    lst = QListWidget()
    lst.addItems(items)
    lst.setCurrentRow(0)
    lst.setMinimumHeight(160)
    lst.itemDoubleClicked.connect(lambda _=None: d.accept())
    lay.addWidget(lst)
    _buttons(d, lay, tr("ok"), tr("cancel"))
    if d.exec() == QDialog.DialogCode.Accepted and lst.currentItem() is not None:
        return lst.currentItem().text()
    return None
