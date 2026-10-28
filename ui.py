"""Интерфейс CD Launcher (PyQt6) по макетам: размытый фон, glass-карточки, боковая панель с иконками.
Все надписи идут через tr(); язык и цвета меняются на лету без перезапуска."""
import math
import socket
import time
import traceback
from collections import deque
from pathlib import Path

from PyQt6.QtCore import (QEasingCurve, QEvent, QObject, QPropertyAnimation, QRectF, QSize, Qt, QThread, QTimer, QUrl,
                          pyqtSignal)
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFontComboBox, QInputDialog, QSlider, QColorDialog, QComboBox, QDialog, QFormLayout, QFrame,
                             QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMainWindow, QMenu, QPlainTextEdit, QProgressBar,
                             QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTabWidget, QTextBrowser,
                             QToolButton, QVBoxLayout, QWidget)

from . import config as app_config
from . import (ai, auth, catalog, cdpack, crashinfo, datadir, hosting, java_manager, launch, modrinth, netinfo, p2p, preflight, profiles, repos,
               servers, skin_anim, skin_render, verify, versions)
from .accounts import AccountStore
from .config import (ANIMATIONS_DIR, APP_NAME, APP_VERSION, PROFILES_DIR, RUNTIME_DIR, THEMES, Settings, resource_path)
from .i18n import detect_system_language, lang, tr
from .loaders import LOADERS
from .net import Reporter
from .dialogs import RoundedDialog, choose, confirm, info
from .skin_editor import SkinEditorDialog
from .skin_view import Skin3DView
from .theme import build_qss

_ALIVE = set()          # живые потоки: без этого Qt убивает QThread, на который потеряна ссылка


_ICONS = {}
PIXEL_SIZES = (16, 32, 48, 64, 96, 128)


def icon(name: str) -> QIcon:
    """Пиксельная иконка: исходник 16x16, на каждый размер - своя копия, масштаб ТОЛЬКО методом ближайшего пикселя."""
    if name not in _ICONS:
        base = QImage(str(resource_path(f"assets/icons/{name}.png")))
        ic = QIcon()
        if not base.isNull():
            for s in PIXEL_SIZES:
                ic.addPixmap(QPixmap.fromImage(base.scaled(s, s, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                           Qt.TransformationMode.FastTransformation)))
        _ICONS[name] = ic
    return _ICONS[name]


def app_icon() -> QIcon:
    """Иконка лаунчера для окна и панели задач - тоже без сглаживания."""
    ic = QIcon()
    base = QImage(str(resource_path("assets/app_icon.png")))
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(QPixmap.fromImage(base.scaled(s, s, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)))
    return ic


def fit_pixmap(pm: QPixmap, w: int, h: int) -> QPixmap:
    """Увеличение - без сглаживания (пиксель-арт остаётся чётким), уменьшение - со сглаживанием."""
    mode = Qt.TransformationMode.FastTransformation if pm.width() <= w and pm.height() <= h else Qt.TransformationMode.SmoothTransformation
    return pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, mode)


def pixmap_from(data: bytes) -> QPixmap:
    pm = QPixmap()
    pm.loadFromData(data)
    return pm


class Worker(QThread):
    """Выполняет fn(rep) в фоне; результат/ошибка возвращаются сигналами в GUI-поток."""
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        _ALIVE.add(self)
        self.finished.connect(lambda: _ALIVE.discard(self))

    def run(self):
        rep = Reporter(self.log.emit, lambda c, t: self.progress.emit(c, t))
        try:
            self.done.emit(self.fn(rep))
        except Exception as e:
            traceback.print_exc()
            self.failed.emit(f"{type(e).__name__}: {e}")


class Tr:
    """Привязка надписей к ключам перевода: retranslate() обновляет всё на лету."""
    def bind(self, setter, key, **kw):
        if not hasattr(self, "_trs"):
            self._trs = []
        self._trs.append((setter, key, kw))
        setter(tr(key, **kw))

    def retranslate(self):
        for setter, key, kw in getattr(self, "_trs", []):
            setter(tr(key, **kw))
        if hasattr(self, "refresh"):
            self.refresh()


class Page(QWidget, Tr):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.settings = win.settings
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(26, 20, 26, 18)
        self.root.setSpacing(14)

    def header(self, key, right=None):
        row = QHBoxLayout()
        t = QLabel()
        t.setObjectName("pageTitle")
        self.bind(t.setText, key)
        row.addWidget(t, 1)
        if right is not None:
            row.addWidget(right)
        self.root.addLayout(row)
        return row

    def run(self, fn, done=None, failed=None, log=None, progress=None):
        w = Worker(fn)
        if done:
            w.done.connect(done)
        if failed:
            w.failed.connect(failed)
        if log:
            w.log.connect(log)
        if progress:
            w.progress.connect(lambda c, t: progress(c, t))
        w.start()
        return w


def add_action(menu, text, fn):
    """QAction + triggered: аргумент checked не должен затирать значения по умолчанию в лямбдах."""
    a = QAction(text, menu)
    a.triggered.connect(lambda _=False: fn())
    menu.addAction(a)
    return a


def card(name="card"):
    f = QFrame()
    f.setObjectName(name)
    return f


def dot(color):
    d = QLabel("●")
    d.setStyleSheet(f"color:{color}; font-size:10px; background:transparent; border:none;")
    return d


def muted(text=""):
    m = QLabel(text)
    m.setObjectName("muted")
    m.setWordWrap(True)
    return m


class ClickFrame(QFrame):
    clicked = pyqtSignal()

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)


class ClickLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, e):
        self.clicked.emit()


def set_bar(bar, c, t):
    bar.setMaximum(max(int(t), 1))
    bar.setValue(int(c))


def mc_minor(version: str) -> int:
    try:
        return int(version.split(".")[1])
    except (IndexError, ValueError):
        return 0


# ================================================================== ГЛАВНАЯ ===
def restart_app():
    """Новый экземпляр читает новую папку данных; текущий закрываем."""
    datadir.restart()
    QApplication.instance().quit()


def ask_data_location(default: Path):
    """Первый запуск: где хранить данные лаунчера. Возвращает выбранную папку или None."""
    d = RoundedDialog(None, tr("datadir_first_title"))
    lay = QVBoxLayout()
    d.set_content(lay)
    text = QLabel(tr("datadir_first_text"))
    text.setWordWrap(True)
    text.setMinimumWidth(480)
    lay.addWidget(text)
    row = QHBoxLayout()
    edit = QLineEdit(str(default))
    browse = QPushButton(tr("datadir_browse"))
    browse.setObjectName("ghost")

    def pick():
        p = QFileDialog.getExistingDirectory(d, tr("datadir_browse"))
        if p:
            edit.setText(str(Path(p) / "CDLauncher"))
    browse.clicked.connect(lambda _=False: pick())
    row.addWidget(edit, 1)
    row.addWidget(browse)
    lay.addLayout(row)
    btns = QHBoxLayout()
    btns.addStretch(1)
    b_def = QPushButton(tr("datadir_use_default"))
    b_def.setObjectName("ghost")
    b_def.clicked.connect(lambda _=False: (edit.setText(str(default)), d.accept()))
    b_ok = QPushButton(tr("datadir_confirm"))
    b_ok.setObjectName("accent")
    b_ok.clicked.connect(lambda _=False: d.accept())
    btns.addWidget(b_def)
    btns.addWidget(b_ok)
    lay.addLayout(btns)
    d.exec()
    return Path(edit.text().strip() or str(default))


class ConsoleDialog(RoundedDialog):
    """Консоль игры: весь вывод Minecraft в реальном времени; окно не блокирует лаунчер."""

    def __init__(self, win, lines, title=None, on_command=None):
        super().__init__(win, title or tr("console"))
        self.resize(900, 520)
        lay = QVBoxLayout()
        self.set_content(lay)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(6000)
        self.text.setStyleSheet("font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px;")
        self.text.setPlainText("\n".join(lines))
        lay.addWidget(self.text, 1)
        if on_command:                                   # консоль сервера: можно вводить команды
            self.cmd = QLineEdit()
            self.cmd.setPlaceholderText(tr("server_command_ph"))
            self.cmd.returnPressed.connect(lambda: (on_command(self.cmd.text()), self.cmd.clear()))
            lay.addWidget(self.cmd)
        row = QHBoxLayout()
        self.auto = QCheckBox(tr("console_autoscroll"))
        self.auto.setChecked(True)
        row.addWidget(self.auto)
        row.addStretch(1)
        for key, fn in (("console_clear", self.clear), ("console_copy", self.copy), ("console_save", self.save)):
            b = QPushButton(tr(key))
            b.setObjectName("ghost")
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        lay.addLayout(row)
        self.on_clear = None

    def append(self, line):
        self.text.appendPlainText(line)
        if self.auto.isChecked():
            self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def clear(self):
        self.text.clear()
        if self.on_clear:
            self.on_clear()

    def copy(self):
        QApplication.clipboard().setText(self.text.toPlainText())

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("console_save"), "console.log", "Log (*.log *.txt)")
        if path:
            Path(path).write_text(self.text.toPlainText(), "utf-8")


class HomePage(Page):
    SKIN_RELOAD_MS = 10 * 60 * 1000          # скин подгружается заново каждые 10 минут

    def __init__(self, win):
        super().__init__(win)
        exit_btn = QToolButton()
        exit_btn.setIcon(icon("exit"))
        exit_btn.setObjectName("dots")
        exit_btn.clicked.connect(win.open_accounts)
        self.header("dashboard", exit_btn)

        hero = card()
        hl = QHBoxLayout(hero)
        hl.setContentsMargins(20, 16, 20, 18)
        hl.setSpacing(18)
        # --- слева: большой персонаж (обведённая область на макете) с кнопками обновления скина и редактора
        left = QVBoxLayout()
        tools = QHBoxLayout()
        self.btn_reload = QToolButton()
        self.btn_reload.setIcon(icon("reload"))
        self.btn_reload.setObjectName("dots")
        self.btn_reload.setToolTip(tr("skin_reload"))
        self.btn_reload.clicked.connect(lambda _=False: self.reload_skin())
        self.btn_edit_skin = QToolButton()
        self.btn_edit_skin.setIcon(icon("edit"))
        self.btn_edit_skin.setObjectName("dots")
        self.btn_edit_skin.setToolTip(tr("skin_editor_title"))
        self.btn_edit_skin.clicked.connect(win.open_skin_editor)
        tools.addWidget(self.btn_reload)
        tools.addStretch(1)
        tools.addWidget(self.btn_edit_skin)
        left.addLayout(tools)
        self.skin = Skin3DView()
        self.skin.setToolTip(tr("skin_drag_hint"))
        left.addWidget(self.skin, 1)
        hl.addLayout(left, 3)
        # --- справа: ник, сборка, кнопки
        info = QVBoxLayout()
        self.nick = QLabel("—")
        self.nick.setObjectName("nick")
        self.sub = muted("")
        info.addWidget(self.nick)
        info.addWidget(self.sub)
        info.addSpacing(12)
        lab = muted()
        self.bind(lab.setText, "current_profile")
        info.addWidget(lab)
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._saved)
        crow = QHBoxLayout()
        crow.addWidget(self.combo, 1)
        self.btn_opts = QToolButton()
        self.btn_opts.setIcon(icon("settings"))
        self.btn_opts.setObjectName("dots")
        self.btn_opts.setToolTip(tr("launch_options"))
        self.btn_opts.clicked.connect(lambda _=False: win.open_launch_options(self.combo.currentData()))
        crow.addWidget(self.btn_opts)
        info.addLayout(crow)
        info.addStretch(1)
        self.btn_fix = QPushButton()
        self.bind(self.btn_fix.setText, "fix_with_ai")
        self.btn_fix.setVisible(False)
        self.btn_fix.clicked.connect(self.fix)
        info.addWidget(self.btn_fix)
        self.btn_report = QPushButton()
        self.btn_report.setObjectName("ghost")
        self.bind(self.btn_report.setText, "copy_report")
        self.btn_report.setVisible(False)
        self.btn_report.clicked.connect(self.copy_report)
        info.addWidget(self.btn_report)
        self.btn_console = QPushButton()
        self.btn_console.setObjectName("ghost")
        self.btn_console.setIcon(icon("console"))
        self.bind(self.btn_console.setText, "console")
        self.btn_console.clicked.connect(self.open_console)
        info.addWidget(self.btn_console)
        self.btn_play = QPushButton()
        self.btn_play.setObjectName("play")
        self.bind(self.btn_play.setText, "play")
        self.btn_play.clicked.connect(lambda: self.play())
        info.addWidget(self.btn_play)
        self.last_code = 0
        hl.addLayout(info, 2)
        self.root.addWidget(hero, 1)

        bottom = QHBoxLayout()
        prog = card()
        pl = QVBoxLayout(prog)
        self.status = QLabel()
        self.bind(self.status.setText, "status_ready")
        self.bar = QProgressBar()
        self.bar.setValue(0)
        pl.addWidget(self.status)
        pl.addWidget(self.bar)
        bottom.addWidget(prog, 1)
        aic = card()
        al = QHBoxLayout(aic)
        self.ai_dot = dot("#888")
        self.ai_label = QLabel()
        al.addWidget(self.ai_dot)
        al.addWidget(self.ai_label, 1)
        aic.setMinimumWidth(240)
        bottom.addWidget(aic)
        self.root.addLayout(bottom)

        self.worker = None
        self.running = False
        self._skin_for = None
        self._skin_busy = False
        self.console_buf = deque(maxlen=6000)
        self.console = None
        self.skin_timer = QTimer(self)
        self.skin_timer.setInterval(self.SKIN_RELOAD_MS)
        self.skin_timer.timeout.connect(self.reload_skin)
        self.skin_timer.start()

    # ------------------------------------------------------------------ консоль
    def console_add(self, line):
        self.console_buf.append(line)
        if self.console is not None and self.console.isVisible():
            self.console.append(line)

    def open_console(self):
        if self.console is None or not self.console.isVisible():
            self.console = ConsoleDialog(self.win, list(self.console_buf))
            self.console.on_clear = self.console_buf.clear
            self.console.show()
        else:
            self.console.raise_()

    # ------------------------------------------------------------------
    def refresh(self):
        cur = self.combo.currentData() or self.settings.get("last_profile")
        self.combo.blockSignals(True)
        self.combo.clear()
        for p in profiles.list_profiles():
            self.combo.addItem(f"{p['name']}  ({p['loader'].capitalize()} {p['mc_version']})", p["name"])
        if self.combo.count() == 0:
            self.combo.addItem(tr("no_profile"), None)
        i = self.combo.findData(cur)
        self.combo.setCurrentIndex(max(i, 0))
        self.combo.blockSignals(False)
        acc = self.win.accounts.current()
        self.nick.setText(acc["name"] if acc else tr("sign_in"))
        self.sub.setText({"ely": "Ely.by 3D", "microsoft": "Microsoft", "offline": "Offline"}.get(acc["type"], "") if acc else "")
        self.load_skin(acc["name"] if acc else "")
        self.check_ai()

    def select_profile(self, name):
        i = self.combo.findData(name)
        if i >= 0:
            self.combo.setCurrentIndex(i)

    def _saved(self):
        if self.combo.currentData():
            self.settings.set("last_profile", self.combo.currentData())

    def current_profile(self):
        n = self.combo.currentData()
        return profiles.get_profile(n) if n else None

    def apply_skin_prefs(self):
        s = self.settings
        self.skin.configure(bool(s.get("skin_3d")), bool(s.get("skin_autorotate")))
        self.skin.set_animations(self.win.animations, float(s.get("anim_min") or 5), float(s.get("anim_max") or 67),
                                 bool(s.get("anim_enabled")) and bool(s.get("skin_3d")))

    def load_skin(self, name):
        self.apply_skin_prefs()
        if self._skin_for == name and self.skin.sheet is not None:
            return
        self._skin_for = name
        self._fetch_skin(name)

    def _fetch_skin(self, name):
        if self._skin_busy:
            return
        self._skin_busy = True

        def done(data):
            self._skin_busy = False
            self.skin.set_skin(data)
        self.run(lambda rep: skin_render.skin_bytes_for(name), done, lambda m: setattr(self, "_skin_busy", False))

    def reload_skin(self):
        """Кнопка обновления и таймер раз в 10 минут: заново берёт скин (Ely.by / локальный) без сброса поворота."""
        acc = self.win.accounts.current()
        self._skin_for = acc["name"] if acc else ""
        self._fetch_skin(self._skin_for)

    def check_ai(self):
        client = ai.OllamaClient(self.settings)

        def done(ok):
            self.ai_dot.setStyleSheet(f"color:{'#4ade80' if ok else '#9ca3af'}; font-size:10px; background:transparent;")
            self.ai_label.setText(f"{tr('local_ai')}: {tr('ai_ready') if ok else tr('ai_off')}")
        self.run(lambda rep: client.is_running(), done)

    # ------------------------------------------------------------------
    def play(self, extra_args=None):
        if self.running:
            return
        p = self.current_profile()
        if not p:
            info(self, tr("no_profile_hint"))
            return
        acc = self.win.accounts.current()
        if not acc:
            self.win.open_accounts()
            acc = self.win.accounts.current()
            if not acc:
                return
        self.running = True
        self.btn_play.setEnabled(False)
        self.btn_fix.setVisible(False)
        self.btn_report.setVisible(False)
        self.bar.setValue(0)
        self.status.setText(tr("checking"))
        self.console_add(f"===== {p['name']} ({p['loader']} {p['mc_version']}) =====")
        self.win.set_presence("playing", p["name"])

        def job(rep):
            fresh = self.win.accounts.ensure_fresh(acc, self.settings)
            return launch.run_game(p, fresh, self.settings, rep, on_lan_port=lambda port: self.win.lan_opened.emit(p["name"], port),
                                   extra_args=extra_args)

        def line(msg):
            self.console_add(msg)
            self.status.setText(msg[:90])

        self.worker = self.run(job, self.finished, self.errored, line, lambda c, t: set_bar(self.bar, c, t))

    def copy_report(self):
        p = self.current_profile()
        if p:
            QApplication.clipboard().setText(crashinfo.report_text(p, self.last_code))
            self.status.setText(tr("report_copied"))

    def finished(self, code):
        self.last_code = code
        self.btn_report.setVisible(code != 0)
        self.running = False
        self.btn_play.setEnabled(True)
        self.bar.setValue(self.bar.maximum())
        self.btn_fix.setVisible(code != 0)
        if code == 0:
            self.status.setText(tr("status_ready"))
        else:
            p = self.current_profile()
            lines = crashinfo.summarize(p, code) if p else [tr("crash_exit", code=code)]
            for l in lines:
                self.console_add("⚠ " + l)
            self.status.setText(lines[0] + " — " + (lines[1] if len(lines) > 1 else ""))
        self.win.set_presence("online")

    def errored(self, msg):
        self.running = False
        self.last_code = -1
        self.btn_report.setVisible(True)
        self.btn_play.setEnabled(True)
        self.console_add("⚠ " + msg)
        self.status.setText(msg[:90])
        self.btn_fix.setVisible(True)
        self.win.set_presence("online")

    def fix(self):
        p = self.current_profile()
        if p:
            self.win.pages["ai"].start_fix(p["name"])
            self.win.switch("ai")


# ================================================================== ВЕРСИИ ===
def profile_pixmap(p: dict, size: int):
    """Иконка сборки: своя (icon.png) или иконка загрузчика. Увеличение - без сглаживания."""
    path = profiles.icon_path(p)
    if path:
        pm = QPixmap(str(path))
        if not pm.isNull():
            return fit_pixmap(pm, size, size)
    return icon(p["loader"] if p["loader"] != "vanilla" else "vanilla").pixmap(size, size)


class ProfileCard(ClickFrame):
    def __init__(self, page, p):
        super().__init__()
        self.setObjectName("card2")
        self.setFixedSize(190, 170)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        top = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(profile_pixmap(p, 24))
        title = QLabel(f"<b>{p['name']}</b>")
        title.setToolTip(f"{p['loader']} {p['mc_version']}")
        top.addWidget(ic)
        top.addWidget(title, 1)
        top.addWidget(page.menu_button(p))
        lay.addLayout(top)
        thumb = QLabel()
        thumb.setFixedSize(168, 84)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        shots_dir = PROFILES_DIR / p["name"] / "screenshots"
        shots = sorted(shots_dir.glob("*.png"), key=lambda f: f.stat().st_mtime) if shots_dir.exists() else []
        if shots:
            pm = QPixmap(str(shots[-1])).scaled(168, 84, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        else:
            pm = pixmap_from(skin_render.make_thumb(p["loader"])).scaled(168, 84, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            if profiles.icon_path(p):                      # своя иконка по центру заставки
                painter = QPainter(pm)
                big = profile_pixmap(p, 64)
                painter.drawPixmap((168 - big.width()) // 2, (84 - big.height()) // 2, big)
                painter.end()
        thumb.setPixmap(pm)
        lay.addWidget(thumb)
        st = QHBoxLayout()
        st.addWidget(dot("#4ade80"))
        st.addWidget(QLabel(f"{p['mc_version']} · {tr('status_ready')}"), 1)
        lay.addLayout(st)
        self.clicked.connect(lambda: page.win.select_profile(p["name"]))


class ProfileRow(ClickFrame):
    """Режим «список»: маленькая иконка, рядом название, справа версия, статус и меню."""

    def __init__(self, page, p):
        super().__init__()
        self.setObjectName("card2")
        self.setFixedHeight(46)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 8, 4)
        ic = QLabel()
        ic.setPixmap(profile_pixmap(p, 32))
        h.addWidget(ic)
        h.addWidget(QLabel(f"<b>{p['name']}</b>"), 1)
        h.addWidget(muted(f"{p['loader'].capitalize()} {p['mc_version']}"))
        h.addWidget(dot("#4ade80"))
        h.addWidget(QLabel(tr("status_ready")))
        h.addWidget(page.menu_button(p))
        self.clicked.connect(lambda: page.win.select_profile(p["name"]))


class VersionsPage(Page):
    def __init__(self, win):
        super().__init__(win)
        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.btn_import = QToolButton()
        self.btn_import.setIcon(icon("import"))
        self.btn_import.setObjectName("dots")
        self.btn_import.setToolTip(tr("import_cdversion"))
        self.btn_import.clicked.connect(lambda _=False: self.import_cdversion())
        self.btn_new = QPushButton()
        self.btn_new.setObjectName("accent")
        self.bind(self.btn_new.setText, "create_profile")
        self.btn_new.clicked.connect(self.new_profile)
        self.search = QLineEdit()
        self.search.setFixedWidth(190)
        self.bind(self.search.setPlaceholderText, "search")
        self.search.textChanged.connect(self.refresh)
        rl.addWidget(self.btn_import)
        rl.addWidget(self.btn_new)
        rl.addWidget(self.search)
        self.header("versions_title", right)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.grid.setSpacing(12)
        self.scroll.setWidget(self.inner)
        self.root.addWidget(self.scroll, 1)
        bar = card("card2")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.addWidget(dot("#4ade80"))
        self.foot = QLabel()
        bl.addWidget(self.foot, 1)
        self.bar = QProgressBar()
        self.bar.setFixedWidth(220)
        bl.addWidget(self.bar)
        self.root.addWidget(bar)

    def retranslate(self):
        self.btn_import.setToolTip(tr("import_cdversion"))
        super().retranslate()

    def refresh(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        q = self.search.text().strip().lower()
        items = [p for p in profiles.list_profiles() if q in p["name"].lower() or q in p["mc_version"] or q in p["loader"]]
        as_list = self.settings.get("versions_view") == "list"
        for i, p in enumerate(items):
            if as_list:
                self.grid.addWidget(ProfileRow(self, p), i, 0)
            else:
                self.grid.addWidget(ProfileCard(self, p), i // 3, i % 3)
        if as_list:
            self.grid.setColumnStretch(0, 1)
        self.foot.setText(tr("all_up_to_date") if items else tr("no_profile_hint"))

    # ------------------------------------------------------------- меню сборки
    def menu_button(self, p):
        b = QToolButton()
        b.setText("⋮")
        b.setObjectName("dots")
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(b)
        add_action(m, tr("play"), lambda: self.play_profile(p["name"]))
        add_action(m, tr("mods_btn"), lambda: self.open_mods(p["name"]))
        add_action(m, tr("launch_options"), lambda: self.win.open_launch_options(p["name"]))
        add_action(m, tr("change_icon"), lambda: self.change_icon(p["name"]))
        if profiles.icon_path(p):
            add_action(m, tr("reset_icon"), lambda: self.reset_icon(p["name"]))
        add_action(m, tr("export_cdversion"), lambda: self.export_profile(p["name"]))
        add_action(m, tr("verify_files"), lambda: self.verify_profile(p["name"]))
        add_action(m, tr("open_folder"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(PROFILES_DIR / p["name"]))))
        add_action(m, tr("delete"), lambda: self.delete(p["name"]))
        b.setMenu(m)
        return b

    def play_profile(self, name):
        self.win.select_profile(name)
        self.win.pages["home"].play()

    def open_mods(self, name):
        self.win.switch("mods")
        self.win.pages["mods"].select_profile(name)

    def delete(self, name):
        if confirm(self, tr("confirm_delete", name=name)):
            profiles.delete_profile(name)
            self.win.refresh_all()

    def new_profile(self):
        NewProfileDialog(self.win).exec()
        self.win.refresh_all()

    def change_icon(self, name):
        path, _ = QFileDialog.getOpenFileName(self, tr("change_icon"), "", "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif)")
        p = profiles.get_profile(name)
        if path and p:
            try:
                profiles.set_icon(p, path)
                self.refresh()
                self.foot.setText(tr("icon_set"))
            except Exception as e:
                info(self, tr("err_generic", e=e))

    def reset_icon(self, name):
        p = profiles.get_profile(name)
        if p:
            profiles.clear_icon(p)
            self.refresh()

    # ------------------------------------------- .cdversion и проверка файлов
    def run_job(self, fn, done):
        self.bar.setValue(0)
        self.w = self.run(fn, done, lambda m: self.foot.setText(tr("err_generic", e=m)), self.foot.setText,
                          lambda c, t: set_bar(self.bar, c, t))

    def export_profile(self, name):
        p = profiles.get_profile(name)
        if not p:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("export_cdversion"), cdpack.pack_name(p), "CD version (*.cdversion)")
        if not path:
            return
        with_saves = confirm(self, tr("export_saves_q"))
        self.run_job(lambda rep: cdpack.export_profile(p, Path(path), with_saves, rep),
                     lambda out: self.foot.setText(tr("export_done", path=str(out))))

    def import_cdversion(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("import_cdversion"), "", "CD version (*.cdversion)")
        if not path:
            return
        try:
            cdpack.read_manifest(Path(path))               # быстрая проверка до долгой установки
        except ValueError as e:
            info(self, str(e))
            return
        self.run_job(lambda rep: cdpack.import_pack(Path(path), self.settings, rep),
                     lambda p: (self.foot.setText(tr("import_done", name=p["name"])), self.win.refresh_all()))

    def verify_profile(self, name):
        p = profiles.get_profile(name)
        if p:
            self.foot.setText(tr("verify_busy"))
            self.run_job(lambda rep: verify.check_profile(p, rep),
                         lambda r: info(self, f"{tr('verify_done')}\n\n{r.summary()}" + ("\n\n" + "\n".join(r.unfixable[:8]) if r.unfixable else "")))


class NewProfileDialog(RoundedDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win, self.settings = win, win.settings
        self.setWindowTitle(tr("new_profile_title"))
        self.setMinimumWidth(430)
        lay = QVBoxLayout()
        self.set_content(lay)
        form = QFormLayout()
        self.name = QLineEdit()
        self.mc = QComboBox()
        self.loader = QComboBox()
        for l in LOADERS:
            if l == "vanilla" or self.settings.get("show_modloaders"):
                self.loader.addItem(icon(l), l)
        self.lver = QComboBox()
        self.lver.addItem(tr("auto"), None)
        self._lv_token = 0
        self.loader.currentIndexChanged.connect(lambda _: self.load_loader_versions())
        self.mc.currentIndexChanged.connect(lambda _: self.load_loader_versions())
        self.ram = QSpinBox()
        self.ram.setRange(1024, 32768)
        self.ram.setSingleStep(512)
        self.ram.setSuffix(" MB")
        self.ram.setValue(int(self.settings.get("default_ram")))
        form.addRow(tr("name"), self.name)
        form.addRow(tr("mc_version"), self.mc)
        form.addRow(tr("loader"), self.loader)
        form.addRow(tr("loader_version"), self.lver)
        form.addRow(tr("memory"), self.ram)
        lay.addLayout(form)
        self.bar = QProgressBar()
        self.status = muted(tr("wait"))
        self.btn = QPushButton(tr("create"))
        self.btn.clicked.connect(self.create)
        lay.addWidget(self.btn)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        self.w1 = Worker(lambda rep: versions.list_versions(self.settings))
        self.w1.done.connect(self.fill)
        self.w1.failed.connect(lambda m: self.status.setText(m))
        self.w1.start()
        self.w2 = None

    def fill(self, vs):
        self.mc.blockSignals(True)
        self.mc.clear()
        for v in vs:
            self.mc.addItem(f"{v['id']}  ({v['type']})", v["id"])
        self.mc.blockSignals(False)
        self.status.setText(tr("versions_count", n=len(vs)))
        self.load_loader_versions()

    def load_loader_versions(self):
        """Живой список версий Forge/NeoForge/Fabric/Quilt для выбранной версии Minecraft."""
        loader, mc = self.loader.currentText(), self.mc.currentData()
        self._lv_token += 1
        token = self._lv_token
        self.lver.clear()
        self.lver.addItem(tr("auto"), None)
        if not mc or loader == "vanilla":
            return

        def done(vs):
            if token != self._lv_token:
                return
            for v in vs[:40]:
                self.lver.addItem(v["id"] + (f"  ({v['tag']})" if v["tag"] else ""), v["id"])

        self.w3 = Worker(lambda rep: repos.loader_versions(loader, mc))
        self.w3.done.connect(done)
        self.w3.failed.connect(lambda m: self.status.setText(m))
        self.w3.start()

    def create(self):
        name, mc, loader, ram = self.name.text().strip(), self.mc.currentData(), self.loader.currentText(), self.ram.value()
        if not name or not mc:
            self.status.setText(tr("enter_name_version"))
            return
        self.btn.setEnabled(False)
        lv = self.lver.currentData()
        self.w2 = Worker(lambda rep: profiles.create_profile(name, mc, loader, rep, ram, self.settings, lv))
        self.w2.log.connect(self.status.setText)
        self.w2.progress.connect(lambda c, t: set_bar(self.bar, c, t))
        self.w2.done.connect(lambda p: (self.status.setText(tr("profile_ready", name=p["name"])), self.accept()))
        self.w2.failed.connect(lambda m: (self.btn.setEnabled(True), self.status.setText(tr("err_generic", e=m))))
        self.w2.start()



class LaunchOptionsDialog(RoundedDialog):
    """Свои параметры запуска сборки. Всё необязательно: пустые поля - запуск по умолчанию."""

    def __init__(self, win, profile):
        super().__init__(win)
        self.win, self.profile = win, profile
        self.setWindowTitle(f"{tr('launch_options')} — {profile['name']}")
        self.setMinimumWidth(580)
        lay = QVBoxLayout()
        self.set_content(lay)
        lay.addWidget(muted(tr("launch_hint")))
        form = QFormLayout()
        self.jvm = QLineEdit(profile.get("jvm_args", ""))
        self.jvm.setPlaceholderText("-XX:+UseZGC -Dfoo=bar")
        self.game = QLineEdit(profile.get("game_args", ""))
        self.game.setPlaceholderText("--width 1280 --height 720")
        self.ram = QSpinBox()
        self.ram.setRange(1024, 65536)
        self.ram.setSingleStep(512)
        self.ram.setSuffix(" MB")
        self.ram.setValue(int(profile.get("ram_mb") or win.settings.get("default_ram")))
        self.java = QComboBox()
        self.java.addItem(tr("auto"), 0)
        for v in (8, 17, 21, 25):
            self.java.addItem(f"Java {v}", v)
        self.java.setCurrentIndex(max(self.java.findData(int(profile.get("java_major") or 0)), 0))
        form.addRow(tr("jvm_args"), self.jvm)
        form.addRow(tr("game_args"), self.game)
        form.addRow(tr("memory"), self.ram)
        form.addRow(tr("java_version_sel"), self.java)
        lay.addLayout(form)
        row = QHBoxLayout()
        b_reset = QPushButton(tr("reset"))
        b_reset.setObjectName("ghost")
        b_reset.clicked.connect(lambda: (self.jvm.clear(), self.game.clear(), self.java.setCurrentIndex(0)))
        b_save = QPushButton(tr("save"))
        b_save.setObjectName("accent")
        b_save.clicked.connect(self.save)
        row.addStretch(1)
        row.addWidget(b_reset)
        row.addWidget(b_save)
        lay.addLayout(row)

    def save(self):
        self.profile["jvm_args"] = self.jvm.text().strip()
        self.profile["game_args"] = self.game.text().strip()
        self.profile["ram_mb"] = self.ram.value()
        self.profile["java_major"] = int(self.java.currentData() or 0)
        profiles.save_profile(self.profile)
        self.accept()


# ==================================================================== МОДЫ ===
class ModsPage(Page):
    KIND_KEYS = {"plugin": "kind_plugin", "mod": "kind_mod", "resourcepack": "kind_resourcepack", "datapack": "kind_datapack",
                 "shader": "kind_shader", "world": "kind_world", "modpack": "kind_modpack"}
    SRC_KEYS = {"modrinth": "src_modrinth", "curseforge": "src_curseforge", "all": "src_all"}

    def __init__(self, win):
        super().__init__(win)
        self.header("mods_title")
        sub = muted()
        self.bind(sub.setText, "mods_sub")
        self.root.addWidget(sub)
        top = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.currentIndexChanged.connect(self.on_profile_changed)
        self.kind_combo = QComboBox()
        for k, lk in self.KIND_KEYS.items():
            if k != "plugin":
                self.kind_combo.addItem(tr(lk), k)
        self.kind_combo.setCurrentIndex(max(self.kind_combo.findData(self.settings.get("catalog_kind")), 0))
        self.src_combo = QComboBox()
        for k, lk in self.SRC_KEYS.items():
            self.src_combo.addItem(tr(lk), k)
        self.src_combo.setCurrentIndex(max(self.src_combo.findData(self.settings.get("catalog_source")), 0))
        self.kind_combo.currentIndexChanged.connect(self.on_filters_changed)
        self.src_combo.currentIndexChanged.connect(self.on_filters_changed)
        self.query = QLineEdit()
        self.bind(self.query.setPlaceholderText, "search")
        self.query.returnPressed.connect(self.search)
        self.cat = QComboBox()
        self.cat.addItems(list(modrinth.CATEGORIES.keys()))
        self.cat.currentIndexChanged.connect(lambda _: self.search())
        b = QPushButton()
        self.bind(b.setText, "find")
        b.clicked.connect(self.search)
        for w, s in ((self.profile, 1), (self.kind_combo, 0), (self.src_combo, 0), (self.query, 2), (self.cat, 0), (b, 0)):
            top.addWidget(w, s)
        self.root.addLayout(top)

        mid = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        l1 = QLabel()
        l1.setObjectName("h2")
        self.bind(l1.setText, "results")
        left.addWidget(l1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.cards_lay = QVBoxLayout(self.inner)
        self.cards_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.inner)
        left.addWidget(self.scroll, 1)
        pager = QHBoxLayout()
        self.pg_btns = []
        for txt, fn in (("«", lambda: self.go_page(-10**6)), ("‹", lambda: self.go_page(-1)),
                        ("›", lambda: self.go_page(1)), ("»", lambda: self.go_page(10**6))):
            pb = QPushButton(txt)
            pb.setObjectName("ghost")
            pb.setFixedWidth(40)
            pb.clicked.connect(lambda _=False, f=fn: f())
            self.pg_btns.append(pb)
        self.pg_label = QLabel("")
        self.pg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pager.addWidget(self.pg_btns[0])
        pager.addWidget(self.pg_btns[1])
        pager.addWidget(self.pg_label, 1)
        pager.addWidget(self.pg_btns[2])
        pager.addWidget(self.pg_btns[3])
        left.addLayout(pager)

        l2 = QLabel()
        l2.setObjectName("h2")
        self.bind(l2.setText, "installed_in")
        right.addWidget(l2)
        self.inst_search = QLineEdit()
        self.bind(self.inst_search.setPlaceholderText, "search_installed")
        self.inst_search.textChanged.connect(lambda _: self.reload_installed())
        right.addWidget(self.inst_search)
        self.installed = QListWidget()
        right.addWidget(self.installed, 1)
        row = QHBoxLayout()
        b_rm = QPushButton()
        b_rm.setObjectName("danger")
        self.bind(b_rm.setText, "remove")
        b_rm.clicked.connect(self.remove)
        self.b_dep = QPushButton()
        self.b_dep.setObjectName("ghost")
        self.bind(self.b_dep.setText, "check_deps")
        self.b_dep.clicked.connect(self.check_deps)
        row.addWidget(b_rm)
        row.addWidget(self.b_dep)
        right.addLayout(row)
        mid.addLayout(left, 3)
        mid.addLayout(right, 2)
        self.root.addLayout(mid, 1)
        self.bar = QProgressBar()
        self.status = muted()
        self.root.addWidget(self.bar)
        self.root.addWidget(self.status)
        self.page, self.total, self.pages, self.psize, self.cards = 0, 0, 1, 20, {}
        self._searched_for = None
        self._icon_loader = None
        self._token = 0
        self.cat.setEnabled(self.kind() == "mod")

    def retranslate(self):
        for combo, keys in ((self.kind_combo, self.KIND_KEYS), (self.src_combo, self.SRC_KEYS)):
            combo.blockSignals(True)
            for i in range(combo.count()):
                combo.setItemText(i, tr(keys[combo.itemData(i)]))
            combo.blockSignals(False)
        super().retranslate()

    def kind(self):
        return self.kind_combo.currentData() or "mod"

    def source(self):
        return self.src_combo.currentData() or "modrinth"

    def on_filters_changed(self):
        self.settings.set("catalog_kind", self.kind())
        self.settings.set("catalog_source", self.source())
        self.cat.setEnabled(self.kind() == "mod")
        self.b_dep.setVisible(self.kind() == "mod")
        self.reload_installed()
        self.search()

    # ------------------------------------------------------------ профиль / список
    def refresh(self):
        cur = self.profile.currentData()
        self.profile.blockSignals(True)
        self.profile.clear()
        for p in profiles.list_targets():
            label = f"🖥 {p['name'][4:]} ({p['loader']} {p['mc_version']})" if p.get("is_server") else f"{p['name']} ({p['loader']} {p['mc_version']})"
            self.profile.addItem(label, p["name"])
        self.profile.setCurrentIndex(max(self.profile.findData(cur), 0))
        self.profile.blockSignals(False)
        self.rebuild_kinds()
        self.reload_installed()
        name = self.profile.currentData()
        if name and self._searched_for != name:      # при первом открытии показываем популярное
            self.search()

    def rebuild_kinds(self):
        """Виды контента зависят от цели: у сборки - моды/ресурспаки/..., у сервера - плагины или моды и датапаки."""
        p = self.current()
        if p and p.get("is_server"):
            allowed = servers.kinds_for(p["loader"])
        else:
            allowed = ["mod", "resourcepack", "datapack", "shader", "world", "modpack"]
        cur = self.kind_combo.currentData() or self.settings.get("catalog_kind")
        self.kind_combo.blockSignals(True)
        self.kind_combo.clear()
        for k in allowed:
            self.kind_combo.addItem(tr(self.KIND_KEYS[k]), k)
        self.kind_combo.setCurrentIndex(max(self.kind_combo.findData(cur), 0))
        self.kind_combo.blockSignals(False)
        self.cat.setEnabled(self.kind() == "mod")
        self.b_dep.setVisible(self.kind() == "mod")

    def on_profile_changed(self):
        self.rebuild_kinds()
        self.reload_installed()
        self.search()

    def select_profile(self, name):
        i = self.profile.findData(name)
        if i >= 0:
            self.profile.setCurrentIndex(i)

    def current(self):
        n = self.profile.currentData()
        return profiles.get_profile(n) if n else None

    def reload_installed(self):
        self.installed.clear()
        p = self.current()
        if p:
            q = self.inst_search.text().strip().lower() if hasattr(self, "inst_search") else ""
            for m in catalog.list_installed(p, self.kind()):
                if q and q not in m["title"].lower():
                    continue
                it = QListWidgetItem(m["title"])
                it.setData(Qt.ItemDataRole.UserRole, m["rel"])
                self.installed.addItem(it)

    def go(self, fn, done):
        self.w = self.run(fn, done, lambda m: self.status.setText(tr("err_generic", e=m)),
                          self.status.setText, lambda c, t: set_bar(self.bar, c, t))

    # --------------------------------------------------------- поиск и страницы
    def per_page(self):
        return max(5, int(self.settings.get("mods_per_page") or 20))

    def search(self):
        self.page = 0
        self.load_page()

    def go_page(self, delta):
        new = max(0, min(self.pages - 1, self.page + delta))
        if new != self.page:
            self.page = new
            self.load_page()

    def load_page(self):
        p = self.current()
        if not p:
            self.status.setText(tr("no_profile_hint"))
            return
        self._searched_for = p["name"]
        kind, source = self.kind(), self.source()
        cat, q = modrinth.CATEGORIES.get(self.cat.currentText()), self.query.text().strip()
        self._token += 1
        token = self._token
        self.status.setText(tr("loading"))
        self.go(lambda rep: catalog.search(source, kind, q, p, cat, self.per_page(), self.page, self.settings),
                lambda r: token == self._token and self.show_results(r))

    def note_text(self, code):
        if code.startswith("cf_error:"):
            return tr("note_cf_error", e=code.split(":", 1)[1])
        return tr("note_" + code)

    def show_results(self, r):
        from .mods_ui import ImageLoader, ModCard
        p = self.current()
        while self.cards_lay.count():
            w = self.cards_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cards = {}
        self.total, self.pages, self.psize = r["total"], r["pages"], r["page_size"]
        rich = bool(self.settings.get("mods_rich"))
        have = set(modrinth.load_db(p).keys()) if p else set()
        icons = []
        for hit in r["hits"]:
            card_w = ModCard(hit, rich, hit["project_id"] in have, self.install_project, self.open_detail, self.remove_project)
            self.cards[hit["project_id"]] = card_w
            self.cards_lay.addWidget(card_w)
            if rich and hit.get("icon_url"):
                icons.append((hit["project_id"], hit["icon_url"]))
        notes = [self.note_text(c) for c in r.get("notes", [])]
        if not r["hits"]:
            self.cards_lay.addWidget(muted("\n".join(notes) or tr("no_results")))
        self.pg_label.setText(tr("page_x_of_y", page=self.page + 1, pages=self.pages, total=self.total))
        self.status.setText("   ".join([tr("found_n", n=self.total)] + notes))
        if icons:
            self._icon_loader = ImageLoader(icons, 128)
            self._icon_loader.loaded.connect(lambda key, path: self.cards[key].set_icon(path) if key in self.cards else None)
            self._icon_loader.start()
        self.scroll.verticalScrollBar().setValue(0)

    def open_detail(self, hit):
        from .mods_ui import ModDetailDialog
        p = self.current()
        if p:
            ModDetailDialog(self.win, hit, p, self.install_project).exec()

    # ----------------------------------------------------------- установка / удаление
    def install_project(self, hit):
        p = self.current()
        if not p:
            return
        kind, world = hit["kind"], None
        if kind == "datapack" and not p.get("is_server"):
            worlds = catalog.list_worlds(p)
            if worlds:
                world = choose(self, tr("pick_world_title"), tr("pick_world"), worlds)
                if world is None:
                    return
            else:
                self.status.setText(tr("datapack_no_world"))
        elif kind == "modpack":
            if not confirm(self, tr("modpack_confirm", name=hit["title"])):
                return

        def done(res):
            msgs = []
            if res["installed"]:
                msgs.append(tr("installed_n", list=", ".join(res["installed"])))
            if res.get("note"):
                msgs.append(tr("note_" + res["note"]))
            if res.get("new_profile"):
                msgs.append(tr("modpack_done", name=res["new_profile"]))
                self.win.refresh_all()
            self.status.setText("   ".join(msgs) or tr("already_installed"))
            if res.get("blocked") and confirm(self, tr("blocked_download")):
                QDesktopServices.openUrl(QUrl(res["blocked"]))
            self.reload_installed()
            card_w = self.cards.get(hit["project_id"])
            if card_w and res["installed"]:
                card_w.set_installed(True)
        self.go(lambda rep: catalog.install(hit, p, self.settings, rep, world), done)

    def remove_project(self, hit):
        """Кнопка «Удалить» на карточке уже установленного."""
        p = self.current()
        if not p:
            return
        if hit["kind"] == "world" and not confirm(self, tr("confirm_remove_world", name=hit["title"])):
            return
        catalog.remove_project(p, hit["project_id"])
        card_w = self.cards.get(hit["project_id"])
        if card_w:
            card_w.set_installed(False)
        self.reload_installed()

    def remove(self):
        p, it = self.current(), self.installed.currentItem()
        if not p or not it:
            return
        rel, kind = it.data(Qt.ItemDataRole.UserRole), self.kind()
        if kind == "world" and not confirm(self, tr("confirm_remove_world", name=it.text())):
            return
        catalog.remove_item(p, kind, rel)
        self.reload_installed()

    def check_deps(self):
        p = self.current()
        if p:
            self.go(lambda rep: modrinth.resolve_local_dependencies(
                        p, rep, lambda mid: catalog.find_by_mod_id(mid, p, self.settings, rep)),
                    lambda got: (self.status.setText(tr("installed_n", list=", ".join(got) or tr("already_installed"))),
                                 self.reload_installed()))


# ==================================================================== ДРУЗЬЯ ===
STATUS_COLORS = {"online": "#4ade80", "menu": "#4ade80", "playing": "#4ade80", "open_world": "#22d3ee", "offline": "#6b7280"}


def status_text(f: dict) -> str:
    s = f.get("status", "offline")
    if s == "playing":
        return tr("st_playing", profile=f.get("profile", ""))
    if s == "open_world":
        return tr("st_open")
    return tr({"online": "st_online", "menu": "st_menu"}.get(s, "st_offline"))


class FriendsPage(Page):
    def __init__(self, win):
        super().__init__(win)
        self.header("friends_title")
        top = QHBoxLayout()
        self.add_edit = QLineEdit()
        self.bind(self.add_edit.setPlaceholderText, "friend_ph")
        self.add_edit.returnPressed.connect(self.add)
        b = QPushButton()
        b.setObjectName("accent")
        self.bind(b.setText, "add_friend")
        b.clicked.connect(self.add)
        self.server_edit = QLineEdit(self.settings.get("p2p_server"))
        self.bind(self.server_edit.setPlaceholderText, "server_label")
        bc = QPushButton()
        bc.setObjectName("ghost")
        self.bind(bc.setText, "server_connect")
        bc.clicked.connect(self.connect_server)
        top.addWidget(self.add_edit, 2)
        top.addWidget(b)
        top.addSpacing(14)
        top.addWidget(self.server_edit, 2)
        top.addWidget(bc)
        self.root.addLayout(top)
        hrow = QHBoxLayout()
        self.btn_host = QPushButton()
        self.btn_host.setObjectName("ghost")
        self.bind(self.btn_host.setText, "host_server")
        self.btn_host.clicked.connect(self.toggle_host)
        self.host_info = muted()
        hrow.addWidget(self.btn_host)
        hrow.addWidget(self.host_info, 1)
        self.root.addLayout(hrow)
        self.info = muted()
        self.root.addWidget(self.info)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.rows = QVBoxLayout(self.inner)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.inner)
        body.addWidget(self.scroll, 3)

        right = QVBoxLayout()
        sel = card()
        sl = QHBoxLayout(sel)
        self.sel_skin = QLabel()
        self.sel_skin.setFixedSize(90, 180)
        self.sel_skin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(self.sel_skin)
        col = QVBoxLayout()
        self.sel_name = QLabel("—")
        self.sel_name.setObjectName("nick")
        self.sel_sub = muted("Ely.by 3D")
        self.sel_prof = QLabel("")
        self.sel_prof.setWordWrap(True)
        lab = muted()
        self.bind(lab.setText, "stability")
        self.sel_stab = QLabel("")
        self.sel_stab.setObjectName("ok")
        for w in (self.sel_name, self.sel_sub, self.sel_prof, lab, self.sel_stab):
            col.addWidget(w)
        col.addStretch(1)
        sl.addLayout(col, 1)
        right.addWidget(sel)
        c1 = card()
        l1 = QHBoxLayout(c1)
        ic = QLabel()
        ic.setPixmap(icon("home").pixmap(32, 32))
        l1.addWidget(ic)
        v = QVBoxLayout()
        self.p2p_title = QLabel()
        self.p2p_title.setObjectName("h2")
        self.p2p_sub = muted()
        v.addWidget(self.p2p_title)
        v.addWidget(self.p2p_sub)
        l1.addLayout(v, 1)
        right.addWidget(c1)
        c2 = card()
        l2 = QVBoxLayout(c2)
        self.id_label = QLabel()
        self.id_label.setObjectName("h2")
        self.id_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.srv_label = muted()
        self.bar = QProgressBar()
        l2.addWidget(self.id_label)
        l2.addWidget(self.srv_label)
        l2.addWidget(self.bar)
        right.addWidget(c2)
        right.addStretch(1)
        body.addLayout(right, 2)
        self.root.addLayout(body, 1)
        self.selected = None
        self.tunnel = None
        self.latency = None
        self.timer = QTimer(self)
        self.timer.setInterval(8000)
        self.timer.timeout.connect(self.measure)
        self.timer.start()

    def refresh(self):
        self.info.setText(tr("p2p_hint"))
        cid = self.settings.get("cd_id")
        self.id_label.setText(tr("your_cdid", id=cid or "—"))
        self.show_state()
        self.rebuild()

    def connect_server(self):
        self.settings.set("p2p_server", self.server_edit.text().strip())
        self.win.ensure_p2p(force=True)
        self.refresh()

    def toggle_host(self):
        """Сервер друзей прямо в лаунчере: один из игроков включает его у себя, остальные подключаются к его адресу."""
        h = self.win.hosted
        if h.running:
            h.stop()
            self.btn_host.setText(tr("host_server"))
            self.host_info.setText("")
            return
        port = 25585
        if not h.start(port):
            self.host_info.setText(tr("host_failed", e=h.error or "?"))
            return
        self.btn_host.setText(tr("host_stop"))
        ips = netinfo.local_ips()
        self.host_info.setText(tr("host_running", port=port, addr=", ".join(f"{i}:{port}" for i in ips) or f"127.0.0.1:{port}"))
        self.server_edit.setText(f"127.0.0.1:{port}")
        self.connect_server()

        def done(addr):
            if addr and h.running:
                self.host_info.setText(tr("host_running", port=port, addr=f"{addr[0]}:{port}  (LAN: {', '.join(ips)})"))
        self.run(lambda rep: netinfo.stun_public_address(), done)

    def add(self):
        q = self.add_edit.text().strip()
        if q and self.win.p2p:
            self.win.p2p.add_friend(q)
            self.add_edit.clear()
        elif not self.win.p2p:
            self.info.setText(tr("server_off"))

    def show_state(self):
        online = self.win.p2p_connected
        acc = self.win.accounts.current()
        self.srv_label.setText(tr("server_on", name=acc["name"] if acc else "?") if online else tr("server_off"))
        if self.tunnel:
            self.p2p_title.setText(tr("p2p_active"))
            ms = f" · {self.latency} ms" if self.latency is not None else ""
            self.p2p_sub.setText(f"127.0.0.1:{self.tunnel}{ms}")
            self.bar.setValue(100)
        else:
            self.p2p_title.setText(tr("conn_server"))
            self.p2p_sub.setText((f"{tr('latency')}: {self.latency} ms" if self.latency is not None else tr("ready_conn")) if online else tr("server_off"))
            self.bar.setValue(100 if online else 0)

    def rebuild(self):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        fl = self.win.friend_list
        if not fl:
            self.rows.addWidget(muted(tr("no_friends")))
            return
        order = {"open_world": 0, "playing": 1, "menu": 2, "online": 3, "offline": 4}
        for f in sorted(fl, key=lambda x: order.get(x["status"], 5)):
            row = ClickFrame()
            row.setObjectName("cardSel" if self.selected == f["id"] else "card2")
            hl = QHBoxLayout(row)
            av = QLabel()
            av.setFixedSize(40, 40)
            hl.addWidget(av)
            self.load_avatar(av, f["name"])
            col = QVBoxLayout()
            col.addWidget(QLabel(f"<b>{f['name']}</b>"))
            st = QHBoxLayout()
            st.addWidget(dot(STATUS_COLORS.get(f["status"], "#6b7280")))
            st.addWidget(muted(status_text(f)), 1)
            col.addLayout(st)
            hl.addLayout(col, 1)
            if f["status"] == "open_world":
                bj = QPushButton(tr("p2p_connect"))
                bj.setObjectName("accent")
                bj.clicked.connect(lambda _=False, fr=f: self.join(fr))
                hl.addWidget(bj)
            m = QToolButton()
            m.setText("⋮")
            m.setObjectName("dots")
            m.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(m)
            add_action(menu, tr("friend_remove"), lambda fid=f["id"]: self.win.p2p and self.win.p2p.remove_friend(fid))
            m.setMenu(menu)
            hl.addWidget(m)
            row.clicked.connect(lambda fr=f: self.select(fr))
            self.rows.addWidget(row)

    def load_avatar(self, label, name):
        self.run(lambda rep: skin_render.render_for(name, "head", 40),
                 lambda data: label.setPixmap(pixmap_from(data).scaled(40, 40)))

    def select(self, f):
        self.selected = f["id"]
        self.sel_name.setText(f["name"])
        self.sel_prof.setText(f"{tr('current_profile')} {f.get('profile') or '—'}")
        self.sel_stab.setText(tr("ready_conn") if f["status"] != "offline" else tr("st_offline"))
        self.run(lambda rep: skin_render.render_for(f["name"], "front", 6),
                 lambda data: self.sel_skin.setPixmap(pixmap_from(data).scaled(90, 180)))
        self.rebuild()

    def join(self, f):
        if not self.win.p2p:
            return
        self.info.setText(tr("p2p_connecting", name=f["name"]))
        port = self.win.p2p.open_local_listener(f["id"])
        self.tunnel = port
        QApplication.clipboard().setText(f"127.0.0.1:{port}")
        self.info.setText(tr("p2p_joined", port=port))
        self.show_state()
        self.win.join_world(port)

    def on_error(self, code):
        key = "err_" + str(code)
        self.info.setText(tr(key) if tr(key) != key else tr("err_generic", e=code))

    def measure(self):
        addr = p2p.parse_address(self.settings.get("p2p_server")) if self.settings.get("p2p_server") else None
        if not addr or not self.isVisible():
            return

        def ping(rep):
            t = time.time()
            socket.create_connection(addr, timeout=4).close()
            return int((time.time() - t) * 1000)

        self.run(ping, lambda ms: (setattr(self, "latency", ms), self.show_state()))



# ================================================================== СЕРВЕРЫ ===
EULA_URL = "https://aka.ms/MinecraftEULA"


class ServerBridge(QObject):
    """Выводит события потоков сервера в GUI-поток."""
    line = pyqtSignal(str, str)
    ready = pyqtSignal(str)
    exited = pyqtSignal(str, int)


class NewServerDialog(RoundedDialog):
    def __init__(self, win):
        super().__init__(win, tr("create_server"))
        self.win = win
        self.setMinimumWidth(500)
        lay = QVBoxLayout()
        self.set_content(lay)
        form = QFormLayout()
        self.name = QLineEdit()
        self.core = QComboBox()
        for c in servers.CORES:
            self.core.addItem(icon(c if c in ("vanilla", "forge", "neoforge", "fabric", "paper", "purpur") else "paper"), c)
        self.version = QComboBox()
        self.ram = QSpinBox()
        self.ram.setRange(1024, 65536)
        self.ram.setSingleStep(512)
        self.ram.setSuffix(" MB")
        self.ram.setValue(2048)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(25565)
        form.addRow(tr("server_name"), self.name)
        form.addRow(tr("core"), self.core)
        form.addRow(tr("mc_version"), self.version)
        form.addRow(tr("memory"), self.ram)
        form.addRow(tr("server_port"), self.port)
        lay.addLayout(form)
        self.eula = QCheckBox(tr("eula_accept"))
        self.eula.toggled.connect(lambda v: self.btn.setEnabled(v))
        link = QPushButton(EULA_URL)
        link.setObjectName("ghost")
        link.clicked.connect(lambda _=False: QDesktopServices.openUrl(QUrl(EULA_URL)))
        lay.addWidget(self.eula)
        lay.addWidget(link)
        self.bar = QProgressBar()
        self.status = muted(tr("wait"))
        self.btn = QPushButton(tr("create"))
        self.btn.setObjectName("accent")
        self.btn.setEnabled(False)
        self.btn.clicked.connect(self.create)
        lay.addWidget(self.btn)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        self._token = 0
        self.core.currentIndexChanged.connect(lambda _: self.load_versions())
        self.load_versions()

    def load_versions(self):
        core = self.core.currentText()
        self._token += 1
        token = self._token
        self.version.clear()
        self.status.setText(tr("loading"))

        def done(vs):
            if token != self._token:
                return
            for v in vs[:80]:
                self.version.addItem(v, v)
            self.status.setText(tr("versions_count", n=len(vs)))
        self.w1 = Worker(lambda rep: servers.core_versions(core))
        self.w1.done.connect(done)
        self.w1.failed.connect(lambda m: self.status.setText(tr("err_generic", e=m)))
        self.w1.start()

    def create(self):
        name, core, mc = self.name.text().strip(), self.core.currentText(), self.version.currentData()
        if not name or not mc:
            self.status.setText(tr("enter_name_version"))
            return
        ram, port = self.ram.value(), self.port.value()
        self.btn.setEnabled(False)
        self.w2 = Worker(lambda rep: servers.create_server(name, core, mc, ram, port, rep, self.win.settings))

        def done(s):
            servers.accept_eula(s)                  # согласие дано галочкой в этом окне
            self.accept()
        self.w2.log.connect(self.status.setText)
        self.w2.progress.connect(lambda c, t: set_bar(self.bar, c, t))
        self.w2.done.connect(done)
        self.w2.failed.connect(lambda m: (self.btn.setEnabled(True), self.status.setText(tr("err_generic", e=m))))
        self.w2.start()


class ServerSettingsDialog(RoundedDialog):
    def __init__(self, win, s):
        super().__init__(win, f"{tr('server_settings')} — {s['name']}")
        self.s = s
        self.setMinimumWidth(480)
        lay = QVBoxLayout()
        self.set_content(lay)
        form = QFormLayout()
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(s["port"]))
        self.ram = QSpinBox()
        self.ram.setRange(1024, 65536)
        self.ram.setSingleStep(512)
        self.ram.setSuffix(" MB")
        self.ram.setValue(int(s["ram_mb"]))
        self.motd = QLineEdit(f"CD Launcher: {s['name']}")
        props = servers.server_dir(s) / "server.properties"
        if props.exists():
            for line in props.read_text("utf-8").splitlines():
                if line.startswith("motd="):
                    self.motd.setText(line[5:])
        form.addRow(tr("server_port"), self.port)
        form.addRow(tr("memory"), self.ram)
        form.addRow("MOTD", self.motd)
        lay.addLayout(form)
        self.online = QCheckBox(tr("server_online_mode"))
        self.online.setChecked(bool(s.get("online_mode")))
        warn = muted(tr("server_online_warn"))
        self.p2p = QCheckBox(tr("server_p2p_share"))
        self.p2p.setChecked(bool(s.get("p2p", True)))
        for w in (self.online, warn, self.p2p):
            lay.addWidget(w)
        b = QPushButton(tr("save"))
        b.setObjectName("accent")
        b.clicked.connect(self.save)
        lay.addWidget(b)

    def save(self):
        self.s.update({"port": self.port.value(), "ram_mb": self.ram.value(), "online_mode": self.online.isChecked(),
                       "p2p": self.p2p.isChecked()})
        servers.save_server(self.s)
        servers.write_properties(self.s, {"motd": self.motd.text()})
        self.accept()


class ServersPage(Page):
    def __init__(self, win):
        super().__init__(win)
        new_btn = QPushButton()
        new_btn.setObjectName("accent")
        new_btn.setIcon(icon("plus"))
        self.bind(new_btn.setText, "create_server")
        new_btn.clicked.connect(self.new_server)
        self.header("servers_title", new_btn)
        body = QHBoxLayout()
        body.setSpacing(16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.rows = QVBoxLayout(self.inner)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.inner)
        body.addWidget(self.scroll, 2)
        self.detail = card()
        dl = QVBoxLayout(self.detail)
        dl.setContentsMargins(18, 16, 18, 16)
        self.d_title = QLabel("—")
        self.d_title.setObjectName("nick")
        self.d_info = muted()
        self.d_status = QLabel()
        self.d_addr = QLabel()
        self.d_addr.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.d_p2p = muted()
        for w in (self.d_title, self.d_info, self.d_status, self.d_addr, self.d_p2p):
            dl.addWidget(w)
        dl.addStretch(1)
        self.b_start = QPushButton()
        self.b_start.setObjectName("play")
        self.b_start.clicked.connect(self.start_stop)
        dl.addWidget(self.b_start)
        grid = QGridLayout()
        self.btns = {}
        for i, (key, ic, fn) in enumerate((("server_console", "console", self.open_console), ("server_plugins", "plus", self.open_plugins),
                                           ("server_settings", "settings", self.open_settings), ("open_folder", "folder", self.open_folder),
                                           ("delete", "trash", self.delete_server))):
            b = QPushButton()
            b.setObjectName("danger" if key == "delete" else "ghost")
            b.setIcon(icon(ic))
            self.bind(b.setText, key)
            b.clicked.connect(lambda _=False, f=fn: f())
            grid.addWidget(b, i // 2, i % 2)
            self.btns[key] = b
        dl.addLayout(grid)
        body.addWidget(self.detail, 2)
        self.root.addLayout(body, 1)
        self.selected = None
        self.procs, self.logs, self.consoles = {}, {}, {}
        self.bridge = ServerBridge()
        self.bridge.line.connect(self.on_line)
        self.bridge.ready.connect(self.on_ready)
        self.bridge.exited.connect(self.on_exit)

    # ------------------------------------------------------------------ список
    def running(self, name):
        p = self.procs.get(name)
        return bool(p and p.running)

    def refresh(self):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        items = servers.list_servers()
        if not items:
            self.rows.addWidget(muted(tr("no_servers")))
        for s in items:
            row = ClickFrame()
            row.setObjectName("cardSel" if s["name"] == self.selected else "card2")
            hl = QHBoxLayout(row)
            ic = QLabel()
            ic.setPixmap(icon(s["core"] if s["core"] in ("vanilla", "forge", "neoforge", "fabric", "paper", "purpur") else "paper").pixmap(32, 32))
            hl.addWidget(ic)
            col = QVBoxLayout()
            col.addWidget(QLabel(f"<b>{s['name']}</b>"))
            col.addWidget(muted(f"{s['core']} {s['mc_version']} · :{s['port']}"))
            hl.addLayout(col, 1)
            hl.addWidget(dot("#4ade80" if self.running(s["name"]) else "#6b7280"))
            row.clicked.connect(lambda n=s["name"]: self.select(n))
            self.rows.addWidget(row)
        if self.selected not in [s["name"] for s in items]:
            self.selected = items[0]["name"] if items else None
        self.update_detail()

    def select(self, name):
        self.selected = name
        self.refresh()

    def update_detail(self):
        s = servers.get_server(self.selected) if self.selected else None
        for w in list(self.btns.values()) + [self.b_start]:
            w.setEnabled(s is not None)
        if not s:
            self.d_title.setText("—")
            for w in (self.d_info, self.d_status, self.d_addr, self.d_p2p):
                w.setText("")
            self.b_start.setText(tr("server_start"))
            return
        run = self.running(s["name"])
        proc = self.procs.get(s["name"])
        self.d_title.setText(s["name"])
        self.d_info.setText(f"{s['core']} {s['mc_version']} · {s['ram_mb']} MB · online-mode: {'on' if s.get('online_mode') else 'off'}")
        self.d_status.setText(tr("server_status_ready") if run and proc.ready else tr("server_status_starting") if run else tr("server_status_stopped"))
        self.d_addr.setText(tr("server_address", addr=f"127.0.0.1:{s['port']}"))
        if run and proc.ready and s.get("p2p") and self.win.p2p_connected:
            self.d_p2p.setText(tr("server_p2p_on"))
        else:
            self.d_p2p.setText(tr("server_p2p_hint") if s.get("p2p") else "")
        self.b_start.setText(tr("server_stop") if run else tr("server_start"))
        self.btns["server_plugins"].setVisible(True)

    # ----------------------------------------------------------- запуск / остановка
    def new_server(self):
        NewServerDialog(self.win).exec()
        self.refresh()

    def start_stop(self):
        name = self.selected
        if not name:
            return
        if self.running(name):
            self.b_start.setEnabled(False)
            proc = self.procs[name]
            self.w = self.run(lambda rep: proc.stop(30), lambda _: (self.b_start.setEnabled(True), self.update_detail()))
            return
        s = servers.get_server(name)
        if not s.get("eula"):
            if not confirm(self, tr("eula_confirm") + f"\n{EULA_URL}"):
                return
            servers.accept_eula(s)
        self.logs.setdefault(name, deque(maxlen=6000)).append(f"===== {name} =====")
        proc = servers.ServerProcess(s, lambda l: self.bridge.line.emit(name, l), lambda: self.bridge.ready.emit(name),
                                     lambda c: self.bridge.exited.emit(name, c))
        self.procs[name] = proc
        self.b_start.setEnabled(False)

        def done(_):
            self.b_start.setEnabled(True)
            self.refresh()
        self.w = self.run(lambda rep: proc.start(self.settings, rep), done,
                          lambda m: (self.b_start.setEnabled(True), info(self, tr("err_generic", e=m))))

    def on_line(self, name, text):
        self.logs.setdefault(name, deque(maxlen=6000)).append(text)
        dlg = self.consoles.get(name)
        if dlg is not None and dlg.isVisible():
            dlg.append(text)

    def on_ready(self, name):
        s = servers.get_server(name)
        if s and s.get("p2p"):          # друзья увидят кнопку «P2P CONNECT» и подключатся через туннель
            self.win.set_presence("open_world", name, int(s["port"]))
        self.refresh()

    def on_exit(self, name, code):
        self.logs.setdefault(name, deque(maxlen=6000)).append(f"===== exit code {code} =====")
        self.win.set_presence("online")
        self.refresh()

    # ------------------------------------------------------------------ действия
    def open_console(self):
        name = self.selected
        if not name:
            return
        dlg = self.consoles.get(name)
        if dlg is None or not dlg.isVisible():
            dlg = ConsoleDialog(self.win, list(self.logs.get(name, [])), f"{tr('console')} — {name}",
                                lambda cmd, n=name: self.procs[n].send(cmd) if self.running(n) else None)
            self.consoles[name] = dlg
            dlg.show()

    def open_plugins(self):
        if self.selected:
            self.win.switch("mods")
            self.win.pages["mods"].select_profile(f"srv:{self.selected}")

    def open_settings(self):
        s = servers.get_server(self.selected) if self.selected else None
        if s:
            ServerSettingsDialog(self.win, s).exec()
            self.refresh()

    def open_folder(self):
        s = servers.get_server(self.selected) if self.selected else None
        if s:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(servers.server_dir(s))))

    def delete_server(self):
        name = self.selected
        if name and confirm(self, tr("server_confirm_delete", name=name)):
            if self.running(name):
                self.procs[name].stop(15)
            servers.delete_server(name)
            self.refresh()

    def shutdown(self):
        """Закрытие лаунчера: останавливаем серверы штатно, чтобы миры сохранились."""
        for name, p in list(self.procs.items()):
            if p.running:
                p.stop(20)


# ======================================================================== ИИ ===
class AIPage(Page):
    def __init__(self, win):
        super().__init__(win)
        self.header("ai_title")
        body = QHBoxLayout()
        body.setSpacing(14)
        left = card()
        ll = QVBoxLayout(left)
        t = QLabel()
        t.setObjectName("h2")
        self.bind(t.setText, "recent_ops")
        ll.addWidget(t)
        self.ops = QListWidget()
        ll.addWidget(self.ops, 1)
        body.addWidget(left, 2)

        mid = card()
        ml = QVBoxLayout(mid)
        mt = QLabel()
        mt.setObjectName("h2")
        self.bind(mt.setText, "copilot")
        ml.addWidget(mt)
        self.profile = QComboBox()
        self.profile.currentIndexChanged.connect(self.health)
        ml.addWidget(self.profile)
        self.chat = QTextBrowser()
        ml.addWidget(self.chat, 1)
        self.bar = QProgressBar()
        ml.addWidget(self.bar)
        self.input = QLineEdit()
        self.bind(self.input.setPlaceholderText, "ai_input_ph")
        self.input.returnPressed.connect(self.build)
        ml.addWidget(self.input)
        self.b_fix = QPushButton()
        self.b_fix.setObjectName("accent")
        self.bind(self.b_fix.setText, "apply_fix")
        self.b_fix.clicked.connect(lambda: self.start_fix(None))
        self.b_logs = QPushButton()
        self.b_logs.setObjectName("accent")
        self.bind(self.b_logs.setText, "view_logs")
        self.b_logs.clicked.connect(self.view_logs)
        self.b_build = QPushButton()
        self.bind(self.b_build.setText, "build_btn")
        self.b_build.clicked.connect(self.build)
        self.b_ask = QPushButton()
        self.b_ask.setObjectName("ghost")
        self.bind(self.b_ask.setText, "ask_btn")
        self.b_ask.clicked.connect(self.ask)
        ml.addWidget(self.b_fix)
        ml.addWidget(self.b_logs)
        row = QHBoxLayout()
        row.addWidget(self.b_build, 1)
        row.addWidget(self.b_ask)
        ml.addLayout(row)
        body.addWidget(mid, 5)

        right = card()
        rl = QVBoxLayout(right)
        rt = QLabel()
        rt.setObjectName("h2")
        self.bind(rt.setText, "system_health")
        rl.addWidget(rt)
        self.h_java, self.h_loader = QLabel(), QLabel()
        rl.addWidget(self.h_java)
        rl.addWidget(self.h_loader)
        rl.addStretch(1)
        body.addWidget(right, 2)
        self.root.addLayout(body, 1)
        self.worker = None
        self.greeted = False

    def refresh(self):
        cur = self.profile.currentData()
        self.profile.blockSignals(True)
        self.profile.clear()
        for p in profiles.list_profiles():
            self.profile.addItem(p["name"], p["name"])
        self.profile.setCurrentIndex(max(self.profile.findData(cur), 0))
        self.profile.blockSignals(False)
        self.health()
        if not self.greeted:
            self.say("CD AI", tr("ai_greeting"), False)
            self.greeted = True

    def health(self):
        p = profiles.get_profile(self.profile.currentData()) if self.profile.currentData() else None
        if not p:
            self.h_java.setText("—")
            self.h_loader.setText("—")
            return
        try:
            major = p.get("java_major") or java_manager.required_major(versions.resolve(p["version_id"]), p["mc_version"])
        except Exception:
            major = java_manager.required_major({}, p["mc_version"])
        ok = java_manager.find_installed(int(major)) is not None
        self.h_java.setText(("✅ " if ok else "⬇ ") + tr("java_version", v=major))
        self.h_loader.setText("✅ " + tr("modloader", v=p["loader"].capitalize()))

    def say(self, who, text, me):
        text = str(text).replace("\n", "<br>")
        side = "right" if me else "left"
        color = "#22d3ee" if me else "rgba(255,255,255,0.25)"
        self.chat.append(f"<div align='{side}' style='margin:6px'><span style='border:1px solid {color};'>"
                         f"&nbsp;<b>{who}</b>: {text}&nbsp;</span></div>")

    def busy(self, on):
        for b in (self.b_build, self.b_fix, self.b_ask, self.b_logs):
            b.setEnabled(not on)
        if on:
            self.bar.setValue(0)

    def go(self, fn, done):
        self.busy(True)
        self.worker = self.run(fn, lambda r: (self.busy(False), done(r)),
                               lambda m: (self.busy(False), self.say("CD AI", tr("err_generic", e=m), False)),
                               lambda m: self.say("•", m, False), lambda c, t: set_bar(self.bar, c, t))

    def add_op(self, key):
        self.ops.addItem(tr(key))

    def build(self):
        text = self.input.text().strip()
        if not text:
            return
        self.say(tr("you"), text, True)
        self.input.clear()
        self.add_op("op_generating")
        self.go(lambda rep: ai.build_from_prompt(text, self.settings, rep), self.built)

    def built(self, r):
        p = r["profile"]
        msg = tr("ai_built", name=p["name"], loader=p["loader"], mc=p["mc_version"], mods=", ".join(r["installed"]) or "—")
        if r["skipped"]:
            msg += "\n" + tr("ai_skipped", m=", ".join(r["skipped"]))
        self.say("CD AI", msg, False)
        self.win.refresh_all()

    def start_fix(self, name):
        name = name or self.profile.currentData()
        p = profiles.get_profile(name) if name else None
        if not p:
            self.say("CD AI", tr("no_profile_hint"), False)
            return
        i = self.profile.findData(name)
        if i >= 0:
            self.profile.setCurrentIndex(i)
        self.say(tr("you"), tr("fix_with_ai") + f" — {p['name']}", True)
        self.add_op("op_analyzing")
        def job(rep):
            notes = [tr("act_removed", f=n) for n in crashinfo.quarantine_known_bad(p)]
            notes += preflight.run(p, self.settings, rep)          # моды не того загрузчика, дубли, зависимости, память
            if crashinfo.lower_ram_if_heap_failed(p, rep):
                notes.append(tr("pre_ram_cap", n=p["ram_mb"], t=0))
            r = ai.fix_profile(p, self.settings, rep)
            if r["diagnosis"] == tr("fix_nolog") and crashinfo.context(p):
                r["diagnosis"] = " ".join(crashinfo.summarize(p, 1)[1:])
            r["applied"] = notes + list(r["applied"])
            return r
        self.go(job, self.fixed)

    def fixed(self, r):
        applied = "\n".join("✔ " + a for a in r["applied"]) or tr("ai_none")
        self.say("CD AI", f"{tr('ai_diag')}: {r['diagnosis']}\n{applied}\n{tr('ai_retry')}", False)
        self.win.refresh_all()

    def ask(self):
        text = self.input.text().strip()
        if text:
            self.say(tr("you"), text, True)
            self.input.clear()
            self.go(lambda rep: ai.free_chat(text, self.settings, rep), lambda r: self.say("CD AI", r, False))

    def view_logs(self):
        n = self.profile.currentData()
        if not n:
            return
        log = PROFILES_DIR / n / "logs" / "latest.log"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log if log.exists() else log.parent)))


# ================================================================ НАСТРОЙКИ ===
def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)


class SettingsPage(Page):
    COLORS = [("accent", "c_accent"), ("sidebar", "c_sidebar"), ("button", "c_button"),
              ("card", "c_card"), ("text", "c_text"), ("background", "c_bg")]

    def __init__(self, win):
        super().__init__(win)
        self.header("settings_title")
        body = QHBoxLayout()
        body.setSpacing(14)
        nav = QVBoxLayout()
        self.stack = QStackedWidget()
        self.subs = []
        builders = [("s_game", self.tab_game), ("s_java", self.tab_java), ("s_accounts", self.tab_accounts),
                    ("s_theme", self.tab_theme), ("s_appearance", self.tab_appearance), ("s_language", self.tab_language),
                    ("s_advanced", self.tab_advanced)]
        for i, (key, build) in enumerate(builders):
            b = QPushButton()
            b.setObjectName("sub")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            self.bind(b.setText, key)
            b.clicked.connect(lambda _=False, n=i: self.stack.setCurrentIndex(n))
            nav.addWidget(b)
            self.subs.append(b)
            self.stack.addWidget(build())
        nav.addStretch(1)
        navw = QWidget()
        navw.setLayout(nav)
        navw.setFixedWidth(190)
        body.addWidget(navw)
        body.addWidget(self.stack, 1)
        self.root.addLayout(body, 1)
        self.subs[3].setChecked(True)
        self.stack.setCurrentIndex(3)

    def panel(self):
        f = card()
        l = QVBoxLayout(f)
        l.setContentsMargins(18, 16, 18, 16)
        return f, l

    # --- игра
    def tab_game(self):
        f, l = self.panel()
        for key, label in [("show_old", "f_old"), ("show_snapshots", "f_snap"), ("show_modloaders", "f_loaders"),
                           ("only_downloaded", "f_only"), ("skin_bridge_auto", "skin_bridge"),
                           ("mods_rich", "mods_rich_opt"), ("skin_3d", "skin_3d_opt"), ("skin_autorotate", "skin_rot_opt")]:
            cb = QCheckBox()
            self.bind(cb.setText, label)
            cb.setChecked(bool(self.settings.get(key)))
            cb.toggled.connect(lambda v, k=key: (self.settings.set(k, v), self.win.prefs_changed(k)))
            l.addWidget(cb)
        form = QFormLayout()
        sp = QSpinBox()
        sp.setRange(1024, 32768)
        sp.setSingleStep(512)
        sp.setSuffix(" MB")
        sp.setValue(int(self.settings.get("default_ram")))
        sp.valueChanged.connect(lambda v: self.settings.set("default_ram", v))
        lab = QLabel()
        self.bind(lab.setText, "ram_default")
        form.addRow(lab, sp)
        pp = QSpinBox()
        pp.setRange(5, 50)
        pp.setSingleStep(5)
        pp.setValue(int(self.settings.get("mods_per_page")))
        pp.valueChanged.connect(lambda v: self.settings.set("mods_per_page", v))
        lab2 = QLabel()
        self.bind(lab2.setText, "mods_per_page")
        form.addRow(lab2, pp)
        for key, label in (("default_jvm_args", "default_jvm"), ("default_game_args", "default_game")):
            e = QLineEdit(str(self.settings.get(key) or ""))
            e.editingFinished.connect(lambda k=key, ed=e: self.settings.set(k, ed.text().strip()))
            lb = QLabel()
            self.bind(lb.setText, label)
            form.addRow(lb, e)
        l.addLayout(form)
        # --- проверка и ремонт файлов игры
        vrow = QHBoxLayout()
        vb = QPushButton()
        vb.setIcon(icon("check"))
        self.bind(vb.setText, "verify_all")
        vb.clicked.connect(self.verify_all)
        self.verify_bar = QProgressBar()
        vrow.addWidget(vb)
        vrow.addWidget(self.verify_bar, 1)
        l.addLayout(vrow)
        # --- анимации Blockbench для персонажа на главной
        ah = QLabel()
        ah.setObjectName("h2")
        self.bind(ah.setText, "anim_title")
        l.addWidget(ah)
        hint = muted()
        self.bind(hint.setText, "anim_hint")
        l.addWidget(hint)
        acb = QCheckBox()
        self.bind(acb.setText, "anim_enabled")
        acb.setChecked(bool(self.settings.get("anim_enabled")))
        acb.toggled.connect(lambda v: (self.settings.set("anim_enabled", v), self.win.prefs_changed("anim_enabled")))
        l.addWidget(acb)
        arow = QFormLayout()
        self.anim_spins = {}
        for key, label in (("anim_min", "anim_min"), ("anim_max", "anim_max")):
            sp = QSpinBox()
            sp.setRange(1, 600)
            sp.setValue(int(self.settings.get(key)))
            sp.valueChanged.connect(lambda v, k=key: self.on_anim_interval(k, v))
            lb = QLabel()
            self.bind(lb.setText, label)
            arow.addRow(lb, sp)
            self.anim_spins[key] = sp
        l.addLayout(arow)
        self.anim_list = QListWidget()
        self.anim_list.setMaximumHeight(110)
        l.addWidget(self.anim_list)
        brow = QHBoxLayout()
        b_load = QPushButton()
        b_load.setIcon(icon("import"))
        self.bind(b_load.setText, "anim_load")
        b_load.clicked.connect(self.load_animation)
        b_del = QPushButton()
        b_del.setObjectName("danger")
        self.bind(b_del.setText, "anim_remove")
        b_del.clicked.connect(self.remove_animation)
        brow.addWidget(b_load)
        brow.addWidget(b_del)
        brow.addStretch(1)
        l.addLayout(brow)
        l.addStretch(1)
        return f

    def verify_all(self):
        self.verify_bar.setValue(0)
        self.w = self.run(lambda rep: verify.check_all_versions(rep),
                          lambda r: info(self, f"{tr('verify_done')}\n\n{r.summary()}" + ("\n\n" + "\n".join(r.unfixable[:8]) if r.unfixable else "")),
                          lambda m: info(self, tr("err_generic", e=m)), None, lambda c, t: set_bar(self.verify_bar, c, t))

    def on_anim_interval(self, key, value):
        self.settings.set(key, value)
        lo, hi = self.anim_spins["anim_min"], self.anim_spins["anim_max"]
        if lo.value() > hi.value():                       # минимум не может быть больше максимума
            (hi if key == "anim_min" else lo).setValue(value)
        self.win.prefs_changed(key)

    def refresh_anims(self):
        self.anim_list.clear()
        if ANIMATIONS_DIR.exists():
            for f in sorted(ANIMATIONS_DIR.glob("*.json")):
                try:
                    n = len(skin_anim.load_file(f))
                    it = QListWidgetItem(tr("anim_loaded", n=n, f=f.name))
                except skin_anim.AnimError:
                    it = QListWidgetItem(f.name + " ⚠")
                it.setData(Qt.ItemDataRole.UserRole, f.name)
                self.anim_list.addItem(it)

    def load_animation(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr("anim_load"), "", "Blockbench animation (*.json)")
        added = 0
        for p in paths:
            try:
                n = len(skin_anim.load_file(Path(p)))         # проверяем ДО копирования
            except skin_anim.AnimError as e:
                info(self, f"{Path(p).name}: {e}")
                continue
            ANIMATIONS_DIR.mkdir(parents=True, exist_ok=True)
            dest = ANIMATIONS_DIR / Path(p).name
            i = 1
            while dest.exists():
                i += 1
                dest = ANIMATIONS_DIR / f"{Path(p).stem}_{i}{Path(p).suffix}"
            dest.write_bytes(Path(p).read_bytes())
            added += n
        if added:
            self.win.reload_animations()
            self.refresh_anims()
            info(self, tr("anim_added", n=added))

    def remove_animation(self):
        it = self.anim_list.currentItem()
        if it:
            (ANIMATIONS_DIR / it.data(Qt.ItemDataRole.UserRole)).unlink(missing_ok=True)
            self.win.reload_animations()
            self.refresh_anims()

    # --- java
    def tab_java(self):
        f, l = self.panel()
        info = muted()
        self.bind(info.setText, "java_info")
        l.addWidget(info)
        h = QLabel()
        h.setObjectName("h2")
        self.bind(h.setText, "java_installed")
        l.addWidget(h)
        self.java_list = QListWidget()
        self.java_list.setMaximumHeight(110)
        l.addWidget(self.java_list)
        row = QHBoxLayout()
        self.java_btns = []
        for v in (8, 17, 21, 25):
            b = QPushButton()
            self.bind(b.setText, "java_get", v=v)
            b.clicked.connect(lambda _=False, n=v: self.get_java(n))
            row.addWidget(b)
            self.java_btns.append(b)
        l.addLayout(row)
        self.java_status = muted()
        self.java_bar = QProgressBar()
        l.addWidget(self.java_bar)
        l.addWidget(self.java_status)
        lab = QLabel()
        self.bind(lab.setText, "java_custom")
        e = QLineEdit(self.settings.get("java_path") or "")
        e.editingFinished.connect(lambda: self.settings.set("java_path", e.text().strip()))
        l.addWidget(lab)
        l.addWidget(e)
        l.addStretch(1)
        return f

    def get_java(self, v):
        self.java_status.setText(tr("wait"))
        self.w = self.run(lambda rep: java_manager.ensure_java(v, rep), lambda p: (self.java_status.setText(p), self.refresh()),
                          lambda m: self.java_status.setText(tr("err_generic", e=m)), self.java_status.setText,
                          lambda c, t: set_bar(self.java_bar, c, t))

    # --- аккаунты
    def tab_accounts(self):
        f, l = self.panel()
        self.acc_list = QListWidget()
        l.addWidget(self.acc_list, 1)
        b = QPushButton()
        self.bind(b.setText, "accounts_manage")
        b.clicked.connect(self.win.open_accounts)
        l.addWidget(b)
        return f

    # --- тема
    def tab_theme(self):
        f, l = self.panel()
        t = QLabel()
        t.setObjectName("h2")
        self.bind(t.setText, "presets")
        l.addWidget(t)
        grid = QHBoxLayout()
        self.presets = {}
        for name, c in THEMES.items():
            pf = ClickFrame()
            pf.setObjectName("preset")
            pl = QVBoxLayout(pf)
            sw = QLabel()
            sw.setFixedSize(44, 44)
            sw.setStyleSheet(f"background:{c['accent']}; border-radius:10px; border:2px solid {c['button']};")
            pl.addWidget(sw, 0, Qt.AlignmentFlag.AlignCenter)
            nm = QLabel(name + (f"\n{tr('default_tag')}" if name == "CD Purple" else ""))
            nm.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pl.addWidget(nm)
            pf.clicked.connect(lambda n=name: self.apply_preset(n))
            grid.addWidget(pf)
            self.presets[name] = pf
        l.addLayout(grid)
        cg = QGridLayout()
        self.color_btns = {}
        for i, (key, label) in enumerate(self.COLORS):
            lab = QLabel()
            self.bind(lab.setText, label)
            b = QPushButton()
            b.clicked.connect(lambda _=False, k=key: self.pick(k))
            self.color_btns[key] = b
            cg.addWidget(lab, (i // 3) * 2, i % 3)
            cg.addWidget(b, (i // 3) * 2 + 1, i % 3)
        l.addLayout(cg)
        prev = card("card2")
        pv = QHBoxLayout(prev)
        pb = QPushButton("PLAY")
        pb.setObjectName("play")
        bar = QProgressBar()
        bar.setValue(70)
        pv.addWidget(pb)
        pv.addWidget(bar, 1)
        l.addWidget(prev)
        l.addStretch(1)
        self.mark_presets()
        return f

    def mark_presets(self):
        cur = self.settings.get("theme")
        for n, pf in self.presets.items():
            pf.setObjectName("presetSel" if n == cur else "preset")
            repolish(pf)
        for key, _ in self.COLORS:
            c = self.settings.get("colors").get(key, "#000000")
            fg = "#000" if QColor(c).lightness() > 128 else "#fff"
            self.color_btns[key].setText(c)
            self.color_btns[key].setStyleSheet(f"background:{c}; color:{fg}; border:1px solid rgba(255,255,255,.35);")

    def apply_preset(self, name):
        self.settings.set("theme", name, save=False)
        self.settings.set("colors", dict(THEMES[name]))
        self.win.apply_theme()
        self.mark_presets()

    def pick(self, key):
        colors = dict(self.settings.get("colors"))
        c = QColorDialog.getColor(QColor(colors.get(key, "#ffffff")), self, tr("presets"))
        if c.isValid():
            colors[key] = c.name()
            self.settings.set("colors", colors)
            self.win.apply_theme()
            self.mark_presets()

    # --- оформление: шрифт, скругления, фон, прозрачность, размеры
    UI_DEFAULT_KEYS = ["ui_radius", "ui_win_radius", "ui_font", "ui_font_size", "ui_bg_mode", "ui_bg_path",
                       "ui_bg_blur", "ui_bg_dim", "ui_glass", "ui_sidebar_w", "ui_anim", "versions_view"]

    def set_ui(self, key, value):
        self.settings.set(key, value)
        self.win.schedule_theme()

    def tab_appearance(self):
        f, l = self.panel()
        form = QFormLayout()
        self.ui_controls = {}

        def add(label_key, widget, key):
            lab = QLabel()
            self.bind(lab.setText, label_key)
            form.addRow(lab, widget)
            self.ui_controls[key] = widget

        self.font_box = QFontComboBox()
        self.font_box.setCurrentFont(QFont(str(self.settings.get("ui_font"))))
        self.font_box.currentFontChanged.connect(lambda fnt: self.set_ui("ui_font", fnt.family()))
        add("ui_font", self.font_box, "ui_font")
        for key, label, lo, hi, suf in (("ui_font_size", "ui_font_size", 10, 20, " px"), ("ui_radius", "ui_radius", 0, 28, " px"),
                                        ("ui_win_radius", "ui_win_radius", 0, 26, " px"), ("ui_sidebar_w", "ui_sidebar_w", 80, 170, " px")):
            sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setSuffix(suf)
            sp.setValue(int(self.settings.get(key)))
            sp.valueChanged.connect(lambda v, k=key: self.set_ui(k, v))
            add(label, sp, key)
        for key, label, lo, hi in (("ui_glass", "ui_glass", 20, 100), ("ui_bg_blur", "ui_bg_blur", 0, 20), ("ui_bg_dim", "ui_bg_dim", 0, 90)):
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(int(self.settings.get(key)))
            sl.valueChanged.connect(lambda v, k=key: self.set_ui(k, v))
            add(label, sl, key)
        self.bg_mode = QComboBox()
        for code, lk in (("default", "bg_default"), ("custom", "bg_custom"), ("none", "bg_none")):
            self.bg_mode.addItem(tr(lk), code)
        self.bg_mode.setCurrentIndex(max(self.bg_mode.findData(self.settings.get("ui_bg_mode")), 0))
        self.bg_mode.currentIndexChanged.connect(lambda _: self.set_ui("ui_bg_mode", self.bg_mode.currentData()))
        add("ui_bg", self.bg_mode, "ui_bg_mode")
        pick = QPushButton()
        pick.setObjectName("ghost")
        self.bind(pick.setText, "bg_pick")
        pick.clicked.connect(self.pick_bg)
        form.addRow("", pick)
        vv = QComboBox()
        vv.addItem(tr("view_cards"), "cards")
        vv.addItem(tr("view_list"), "list")
        vv.setCurrentIndex(max(vv.findData(self.settings.get("versions_view")), 0))
        vv.currentIndexChanged.connect(lambda _: (self.settings.set("versions_view", vv.currentData()),
                                                  self.win.pages["versions"].refresh()))
        add("versions_view", vv, "versions_view")
        anim = QCheckBox()
        anim.setChecked(bool(self.settings.get("ui_anim")))
        anim.toggled.connect(lambda v: self.set_ui("ui_anim", v))
        add("ui_anim", anim, "ui_anim")
        l.addLayout(form)
        reset = QPushButton()
        reset.setObjectName("danger")
        self.bind(reset.setText, "ui_reset")
        reset.clicked.connect(self.reset_appearance)
        l.addWidget(reset)
        l.addStretch(1)
        return f

    def pick_bg(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("bg_pick"), "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.settings.set("ui_bg_path", path)
            self.bg_mode.setCurrentIndex(self.bg_mode.findData("custom"))
            self.set_ui("ui_bg_mode", "custom")

    def reset_appearance(self):
        from .config import DEFAULTS
        for k in self.UI_DEFAULT_KEYS:
            self.settings.set(k, DEFAULTS[k], save=False)
        self.settings.set("theme", "CD Purple", save=False)
        self.settings.set("colors", dict(THEMES["CD Purple"]))
        for key, w in self.ui_controls.items():
            w.blockSignals(True)
            v = self.settings.get(key)
            if isinstance(w, QFontComboBox):
                w.setCurrentFont(QFont(str(v)))
            elif isinstance(w, QComboBox):
                w.setCurrentIndex(max(w.findData(v), 0))
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            else:
                w.setValue(int(v))
            w.blockSignals(False)
        self.win.apply_theme()
        self.mark_presets()

    # --- язык
    def tab_language(self):
        f, l = self.panel()
        t = QLabel()
        t.setObjectName("h2")
        self.bind(t.setText, "lang_choose")
        l.addWidget(t)
        grid = QGridLayout()
        self.lang_cards = {}
        for i, (code, name, flag) in enumerate(lang.available()):
            c = ClickFrame()
            c.setObjectName("preset")
            cl = QHBoxLayout(c)
            fl = QLabel()
            flag_file = {"uk_UA": "flag_uk", "ru_RU": "flag_ru", "en_US": "flag_us", "de_DE": "flag_de", "es_ES": "flag_es",
                         "fr_FR": "flag_fr", "pl_PL": "flag_pl", "zh_CN": "flag_cn"}.get(code)
            if flag_file:   # флаги 96x64 -> 48x32 ровно вдвое, без сглаживания
                fl.setPixmap(QPixmap(str(resource_path(f"assets/icons/{flag_file}.png"))).scaled(
                    48, 32, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation))
            lab = QLabel(name)
            lab.setStyleSheet("font-size:15px; background:transparent; border:none;")
            cl.addWidget(fl)
            cl.addWidget(lab, 1)
            c.clicked.connect(lambda cd=code: self.win.change_language(cd))
            grid.addWidget(c, i // 2, i % 2)
            self.lang_cards[code] = c
        l.addLayout(grid)
        l.addStretch(1)
        self.mark_langs()
        return f

    def mark_langs(self):
        for code, c in self.lang_cards.items():
            c.setObjectName("presetSel" if code == lang.code else "preset")
            repolish(c)

    # --- дополнительно
    def tab_advanced(self):
        f, l = self.panel()
        form = QFormLayout()
        for key, label in [("ai_model", "adv_ai"), ("ollama_url", "adv_ollama"), ("ely_client_id", "adv_ely_id"),
                           ("ely_client_secret", "adv_ely_secret"), ("ms_client_id", "adv_ms_id"), ("curseforge_api_key", "adv_cf_key"),
                           ("p2p_server", "server_label")]:
            e = QLineEdit(str(self.settings.get(key) or ""))
            if key in ("curseforge_api_key", "ely_client_secret"):
                e.setEchoMode(QLineEdit.EchoMode.Password)
            e.editingFinished.connect(lambda k=key, ed=e: self.win.advanced_changed(k, ed.text().strip()))
            lab = QLabel()
            self.bind(lab.setText, label)
            form.addRow(lab, e)
        l.addLayout(form)
        # --- папка данных лаунчера
        dh = QLabel()
        dh.setObjectName("h2")
        self.bind(dh.setText, "datadir_title")
        l.addWidget(dh)
        drow = QHBoxLayout()
        self.data_path = QLineEdit(str(app_config.ROOT))
        self.data_path.setReadOnly(True)
        b_chg = QPushButton()
        self.bind(b_chg.setText, "datadir_change")
        b_chg.clicked.connect(self.change_data_dir)
        b_open = QPushButton()
        b_open.setObjectName("ghost")
        b_open.setIcon(icon("folder"))
        self.bind(b_open.setText, "open_folder")
        b_open.clicked.connect(lambda _=False: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_config.ROOT))))
        drow.addWidget(self.data_path, 1)
        drow.addWidget(b_chg)
        drow.addWidget(b_open)
        l.addLayout(drow)
        self.data_bar = QProgressBar()
        l.addWidget(self.data_bar)
        l.addStretch(1)
        return f

    def change_data_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, tr("datadir_change"))
        if not chosen:
            return
        target = Path(chosen)
        if target.name.lower() not in (".cdlauncher", "cdlauncher"):
            target = target / "CDLauncher"                 # не смешиваем с чужими файлами выбранной папки
        if not confirm(self, tr("datadir_move_q", old=str(app_config.ROOT), new=str(target))):
            return
        delete_old = confirm(self, tr("delete_old_q"))
        self.data_bar.setValue(0)
        self.w = self.run(lambda rep: datadir.migrate(app_config.ROOT, target, rep, delete_old),
                          lambda p: (info(self, tr("datadir_moved", path=str(p))), restart_app()),
                          lambda m: info(self, tr("err_generic", e=m)), None, lambda c, t: set_bar(self.data_bar, c, t))

    def refresh(self):
        self.refresh_anims()
        self.java_list.clear()
        found = sorted(RUNTIME_DIR.glob("java*")) if RUNTIME_DIR.exists() else []
        for d in found:
            self.java_list.addItem(d.name.replace("java", "Java "))
        if not found:
            self.java_list.addItem(tr("java_none"))
        self.acc_list.clear()
        st = self.win.accounts
        for a in st.accounts:
            it = QListWidgetItem(icon(f"status_{a['type']}"),
                                 f"{'● ' if st.account_id(a) == st.active else '   '}{a['name']} ({a['type']})")
            self.acc_list.addItem(it)
        self.mark_presets()
        self.mark_langs()


# ================================================================ АККАУНТЫ ===
class AccountsDialog(RoundedDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(tr("accounts_title"))
        self.setMinimumWidth(520)
        lay = QVBoxLayout()
        self.set_content(lay)
        self.list = QListWidget()
        self.list.setMaximumHeight(130)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        b_use = QPushButton(tr("set_active"))
        b_use.clicked.connect(self.use)
        b_del = QPushButton(tr("delete"))
        b_del.setObjectName("danger")
        b_del.clicked.connect(self.delete)
        row.addWidget(b_use)
        row.addWidget(b_del)
        lay.addLayout(row)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        w = QWidget()
        f = QFormLayout(w)
        self.ely_user, self.ely_pass, self.ely_2fa = QLineEdit(), QLineEdit(), QLineEdit()
        self.ely_pass.setEchoMode(QLineEdit.EchoMode.Password)
        f.addRow(tr("login_label"), self.ely_user)
        f.addRow(tr("password"), self.ely_pass)
        f.addRow(tr("code_2fa"), self.ely_2fa)
        b = QPushButton(tr("login_ely"))
        b.clicked.connect(lambda: self.go(lambda rep: auth.ely_login(self.ely_user.text().strip(), self.ely_pass.text(), self.ely_2fa.text().strip())))
        b2 = QPushButton(tr("login_ely_oauth"))
        b2.setObjectName("ghost")
        b2.clicked.connect(lambda: self.go(lambda rep: auth.ely_oauth_login(win.settings.get("ely_client_id"), win.settings.get("ely_client_secret"), rep)))
        f.addRow(b)
        f.addRow(b2)
        tabs.addTab(w, icon("status_ely"), "Ely.by")

        w = QWidget()
        f = QFormLayout(w)
        b = QPushButton(tr("login_ms_browser"))
        b.clicked.connect(lambda: self.go(lambda rep: auth.microsoft_browser_login(win.settings.get("ms_client_id"), rep)))
        b2 = QPushButton(tr("login_ms_code"))
        b2.setObjectName("ghost")
        b2.clicked.connect(lambda: self.go(lambda rep: auth.microsoft_login(win.settings.get("ms_client_id"), rep)))
        f.addRow(b)
        f.addRow(b2)
        tabs.addTab(w, icon("status_microsoft"), "Microsoft")

        w = QWidget()
        f = QFormLayout(w)
        self.nick = QLineEdit()
        b = QPushButton(tr("offline_play"))
        b.clicked.connect(self.offline)
        f.addRow(tr("nick"), self.nick)
        f.addRow(b)
        tabs.addTab(w, icon("status_offline"), "Offline")
        self.status = muted()
        lay.addWidget(self.status)
        self.reload()

    def reload(self):
        self.list.clear()
        st = self.win.accounts
        for a in st.accounts:
            it = QListWidgetItem(icon(f"status_{a['type']}"),
                                 f"{'● ' if st.account_id(a) == st.active else '   '}{a['name']} ({a['type']})")
            it.setData(Qt.ItemDataRole.UserRole, st.account_id(a))
            self.list.addItem(it)
        self.win.after_account_change()

    def use(self):
        it = self.list.currentItem()
        if it:
            self.win.accounts.set_active(it.data(Qt.ItemDataRole.UserRole))
            self.reload()

    def delete(self):
        it = self.list.currentItem()
        if it:
            self.win.accounts.remove(it.data(Qt.ItemDataRole.UserRole))
            self.reload()

    def offline(self):
        try:
            self.win.accounts.add(auth.offline_account(self.nick.text()))
            self.reload()
        except Exception as e:
            self.status.setText(str(e))

    def go(self, fn):
        self.status.setText(tr("wait"))
        self.w = Worker(fn)
        self.w.log.connect(self.status.setText)
        self.w.done.connect(self.added)
        self.w.failed.connect(self.status.setText)
        self.w.start()

    def added(self, acc):
        self.win.accounts.add(acc)
        self.status.setText(tr("signed_in", name=acc["name"]))
        self.reload()


# ============================================================== ГЛАВНОЕ ОКНО ===
class P2PBridge(QObject):
    event = pyqtSignal(object)


FRAME_M, RADIUS = 6, 14
PAGE_TITLES = {"home": "nav_home", "versions": "nav_versions", "friends": "nav_friends", "servers": "nav_servers", "ai": "nav_ai",
               "settings": "nav_settings", "mods": "nav_mods"}


class BgWidget(QWidget):
    """Фон окна: размытый Minecraft-фон, скруглённые углы, рамка и изменение размера за края (окно без системной рамки)."""

    def __init__(self, color_getter, settings):
        super().__init__()
        self.color_getter = color_getter
        self.settings = settings
        self.radius, self.dim, self.pix = RADIUS, 120, None
        self.setMouseTracking(True)
        self.reload()

    def reload(self):
        """Читает из настроек: картинку фона (свою / стандартную / без неё), размытие, затемнение, скругление окна."""
        s = self.settings
        mode = s.get("ui_bg_mode")
        path = resource_path("assets/bg.jpg") if mode == "default" else (s.get("ui_bg_path") if mode == "custom" else None)
        self.pix = None
        if path and Path(str(path)).exists():
            data = skin_render.blurred_background(path, 1100, int(s.get("ui_bg_blur")))
            if data:
                self.pix = pixmap_from(data)
        self.dim = int(int(s.get("ui_bg_dim")) * 2.55)
        win = self.window()
        maxed = win.isMaximized() if win is not self else False
        self.radius = 0 if maxed else int(s.get("ui_win_radius"))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = FRAME_M if self.radius else 0
        rect = QRectF(self.rect()).adjusted(m + 0.5, m + 0.5, -m - 0.5, -m - 0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor(self.color_getter()))
        if self.pix is not None and not self.pix.isNull():
            s = self.pix.scaled(rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(int(rect.x() + (rect.width() - s.width()) / 2), int(rect.y() + (rect.height() - s.height()) / 2), s)
            p.fillRect(self.rect(), QColor(0, 0, 0, self.dim))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 46), 1))
        p.drawPath(path)
        p.end()

    def _edges(self, pos):
        win = self.window()
        if win.isMaximized() or win.isFullScreen():
            return None
        x, y, w, h, m = pos.x(), pos.y(), self.width(), self.height(), FRAME_M + 2
        e = Qt.Edge(0)
        if x < m:
            e |= Qt.Edge.LeftEdge
        if x > w - m:
            e |= Qt.Edge.RightEdge
        if y < m:
            e |= Qt.Edge.TopEdge
        if y > h - m:
            e |= Qt.Edge.BottomEdge
        return e or None

    def mouseMoveEvent(self, ev):
        e = self._edges(ev.position())
        if not e:
            self.unsetCursor()
            return
        L, R, T, B = (Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge)
        if e in (L | T, R | B):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif e in (R | T, L | B):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif e in (L, R):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeVerCursor)

    def mousePressEvent(self, ev):
        e = self._edges(ev.position())
        if e and ev.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemResize(e)


class TitleBar(QFrame):
    """Своя шапка: логотип и название вкладки слева, свернуть / развернуть / закрыть справа; перетаскивание окна."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("titlebar")
        self.setFixedHeight(42)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 8, 0)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(resource_path("assets/app_icon_32.png"))))      # 32x32 как есть, без масштабирования
        self.title = QLabel(APP_NAME)
        self.title.setObjectName("h2")
        h.addWidget(logo)
        h.addSpacing(8)
        h.addWidget(self.title)
        h.addStretch(1)
        self.b_min, self.b_max, self.b_close = QToolButton(), QToolButton(), QToolButton()
        for b, txt, name, fn in ((self.b_min, "—", "wbtn", win.showMinimized), (self.b_max, "▢", "wbtn", win.toggle_max),
                                 (self.b_close, "✕", "wbtnClose", win.close)):
            b.setText(txt)
            b.setObjectName(name)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, f=fn: f())
            h.addWidget(b)

    def set_title(self, text):
        self.title.setText(f"{APP_NAME}  —  {text}")

    def update_max_icon(self, maximized):
        self.b_max.setText("❐" if maximized else "▢")

    def mousePressEvent(self, e):
        wh = self.window().windowHandle()
        if e.button() == Qt.MouseButton.LeftButton and wh:
            wh.startSystemMove()

    def mouseDoubleClickEvent(self, e):
        self.win.toggle_max()


class MainWindow(QMainWindow):
    lan_opened = pyqtSignal(str, int)

    NAV = [("home", "nav_home", "home"), ("versions", "nav_versions", "versions"), ("friends", "nav_friends", "friends"),
           ("servers", "nav_servers", "server"),
           ("ai", "nav_ai", "ai"), ("settings", "nav_settings", "settings")]

    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.accounts = AccountStore()
        self.p2p = None
        self.p2p_key = None
        self.p2p_connected = False
        self.friend_list = []
        self.bridge = P2PBridge()
        self.bridge.event.connect(self.on_p2p_event)
        self.lan_opened.connect(self.on_lan_opened)

        code = self.settings.get("language") or detect_system_language()   # авто-язык ОС при первом старте
        lang.set_language(code)
        self.settings.set("language", lang.code)

        self.hosted = hosting.HostedServer()
        self.animations = skin_anim.load_dir(ANIMATIONS_DIR)
        self.current_page = "home"
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1120, 720)
        self.setMinimumSize(960, 620)
        self.bg = BgWidget(lambda: self.settings.get("colors")["background"], self.settings)
        self.setCentralWidget(self.bg)
        self.outer = QVBoxLayout(self.bg)
        self.outer.setContentsMargins(FRAME_M, FRAME_M, FRAME_M, FRAME_M)
        self.outer.setSpacing(0)
        self.titlebar = TitleBar(self)
        self.outer.addWidget(self.titlebar)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.outer.addLayout(h, 1)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(108)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 16, 8, 14)
        self.pages = {"home": HomePage(self), "versions": VersionsPage(self), "friends": FriendsPage(self),
                      "servers": ServersPage(self), "ai": AIPage(self), "settings": SettingsPage(self), "mods": ModsPage(self)}
        self.nav = {}
        for key, label, ic in self.NAV:
            b = QToolButton()
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setIcon(icon(ic))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.clicked.connect(lambda _=False, k=key: self.switch(k))
            if key == "settings":
                sl.addStretch(1)
            sl.addWidget(b)
            self.nav[key] = (b, label)
        self.sidebar = side
        h.addWidget(side)

        self.stack = QStackedWidget()
        for p in self.pages.values():
            self.stack.addWidget(p)
        h.addWidget(self.stack, 1)

        self.anim = None
        self.apply_theme()
        self.retranslate_nav()
        self.refresh_all()
        self.switch("home")
        if not self.settings.get("csl_cleanup_v1"):
            try:
                from . import skins
                skins.cleanup_all_profiles()
            finally:
                self.settings.set("csl_cleanup_v1", True)
        self.ensure_p2p()
        repos.refresh_async()   # свежие списки Forge/NeoForge/Fabric/Quilt при каждом старте, в фоне

    # ---------------------------------------------------------------- тема/язык
    def style_opts(self) -> dict:
        s = self.settings
        return {"radius": int(s.get("ui_radius")), "win_radius": int(s.get("ui_win_radius")), "font": str(s.get("ui_font")),
                "font_size": int(s.get("ui_font_size")), "glass": int(s.get("ui_glass"))}

    def apply_theme(self):
        QApplication.instance().setStyleSheet(build_qss(self.settings.get("colors"), self.style_opts()))
        self.sidebar.setFixedWidth(int(self.settings.get("ui_sidebar_w")))
        self.bg.reload()

    def schedule_theme(self):
        """Ползунки шлют десятки событий - применяем тему один раз после паузы."""
        if not hasattr(self, "_theme_timer"):
            self._theme_timer = QTimer(self)
            self._theme_timer.setSingleShot(True)
            self._theme_timer.timeout.connect(self.apply_theme)
        self._theme_timer.start(120)

    def prefs_changed(self, key):
        if key in ("skin_3d", "skin_autorotate", "anim_enabled", "anim_min", "anim_max"):
            self.pages["home"].apply_skin_prefs()
        elif key == "mods_rich":
            self.pages["mods"].search()

    def reload_animations(self):
        self.animations = skin_anim.load_dir(ANIMATIONS_DIR)
        self.pages["home"].apply_skin_prefs()

    def open_launch_options(self, name):
        p = profiles.get_profile(name) if name else None
        if not p:
            info(self, tr("no_profile_hint"))
            return
        LaunchOptionsDialog(self, p).exec()

    def retranslate_nav(self):
        for key, (b, label) in self.nav.items():
            b.setText(tr(label))
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.titlebar.set_title(tr(PAGE_TITLES[self.current_page]))

    def change_language(self, code):
        lang.set_language(code)
        self.settings.set("language", code)
        self.retranslate_nav()
        for p in self.pages.values():
            p.retranslate()

    # ---------------------------------------------------------------- навигация
    def switch(self, key):
        if key in self.nav:
            self.nav[key][0].setChecked(True)
        else:
            for b, _ in self.nav.values():
                b.setChecked(False)
        page = self.pages[key]
        self.current_page = key
        self.titlebar.set_title(tr(PAGE_TITLES[key]))
        page.refresh()
        self.stack.setCurrentWidget(page)
        if not self.settings.get("ui_anim"):
            return
        eff = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(eff)
        self.anim = QPropertyAnimation(eff, b"opacity", self)
        self.anim.setDuration(240)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(lambda: page.setGraphicsEffect(None))
        self.anim.start()

    def toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def changeEvent(self, e):
        if e.type() == QEvent.Type.WindowStateChange:
            self.sync_frame()
        super().changeEvent(e)

    def sync_frame(self):
        maxed = self.isMaximized() or self.isFullScreen()
        m = 0 if maxed else FRAME_M
        self.outer.setContentsMargins(m, m, m, m)
        self.bg.radius = 0 if maxed else int(self.settings.get("ui_win_radius"))
        for w in (self.titlebar, self.sidebar):
            w.setProperty("maxed", maxed)
            repolish(w)
        self.titlebar.update_max_icon(maxed)
        self.bg.update()

    def open_skin_editor(self):
        acc = self.accounts.current()
        if not acc:
            self.open_accounts()
            acc = self.accounts.current()
            if not acc:
                return
        SkinEditorDialog(self, acc).exec()
        self.pages["home"].reload_skin()

    def refresh_all(self):
        for p in self.pages.values():
            p.refresh()

    def select_profile(self, name):
        self.pages["home"].select_profile(name)
        self.settings.set("last_profile", name)
        self.switch("home")

    def open_accounts(self):
        AccountsDialog(self).exec()
        self.after_account_change()

    def after_account_change(self):
        self.pages["home"].refresh()
        self.ensure_p2p()

    def advanced_changed(self, key, value):
        self.settings.set(key, value)
        if key == "p2p_server":
            self.ensure_p2p(force=True)

    # ---------------------------------------------------------------------- P2P
    def ensure_p2p(self, force=False):
        server = (self.settings.get("p2p_server") or "").strip()
        acc = self.accounts.current()
        if not server or not acc:
            self.stop_p2p()
            return
        if not self.settings.get("cd_id"):
            cid, secret = p2p.new_identity()
            self.settings.set("cd_id", cid, save=False)
            self.settings.set("cd_secret", secret)
        key = (server, acc["name"])
        if self.p2p and self.p2p_key == key and not force:
            return
        self.stop_p2p()
        try:
            self.p2p = p2p.P2PClient(server, self.settings.get("cd_id"), self.settings.get("cd_secret"),
                                     acc["name"], self.bridge.event.emit)
            self.p2p_key = key
            self.p2p.start()
        except Exception as e:
            self.p2p = None
            self.pages["friends"].info.setText(tr("err_generic", e=e))

    def stop_p2p(self):
        if self.p2p:
            self.p2p.stop()
        self.p2p, self.p2p_key, self.p2p_connected, self.friend_list = None, None, False, []

    def on_p2p_event(self, msg):
        op = msg.get("op")
        fp = self.pages["friends"]
        if op == "status":
            self.p2p_connected = bool(msg.get("connected"))
            fp.show_state()
        elif op == "friends":
            self.friend_list = msg.get("list", [])
            fp.rebuild()
        elif op == "friend_added":
            fp.info.setText(tr("friend_added", name=msg.get("name", "")))
        elif op == "error":
            fp.on_error(msg.get("code", "generic"))

    def set_presence(self, status, profile="", port=0):
        if self.p2p:
            self.p2p.set_presence(status, profile, port)

    def on_lan_opened(self, profile, port):
        self.set_presence("open_world", profile, port)
        self.pages["friends"].info.setText(tr("open_world_detected", port=port))

    def join_world(self, port):
        """Гость: запускаем выбранную сборку и сразу подключаем её к 127.0.0.1:<порт> туннеля."""
        home = self.pages["home"]
        p = home.current_profile()
        if not p or home.running:
            return
        if mc_minor(p["mc_version"]) >= 20:
            args = ["--quickPlayMultiplayer", f"127.0.0.1:{port}"]
        else:
            args = ["--server", "127.0.0.1", "--port", str(port)]
        self.switch("home")
        home.play(args)

    def closeEvent(self, e):
        self.pages["servers"].shutdown()
        self.stop_p2p()
        self.hosted.stop()
        super().closeEvent(e)
