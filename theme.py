"""QSS: glass-тема из 6 настраиваемых цветов (accent, sidebar, button, card, text, background)."""


def _rgb(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def mix(a: str, b: str, t: float) -> str:
    x, y = _rgb(a), _rgb(b)
    return "#" + "".join(f"{int(x[i] + (y[i] - x[i]) * t):02x}" for i in range(3))


def rgba(h: str, alpha: float) -> str:
    r, g, b = _rgb(h)
    return f"rgba({r},{g},{b},{alpha})"


STYLE_DEFAULTS = {"radius": 14, "win_radius": 14, "font": "Segoe UI", "font_size": 13, "glass": 62}


def build_qss(c: dict, o: dict = None) -> str:
    o = {**STYLE_DEFAULTS, **(o or {})}
    r, wr, fs = int(o["radius"]), int(o["win_radius"]), int(o["font_size"])
    rb, rs = max(r - 4, 2), max(r - 2, 2)            # кнопки и малые карточки скругляются чуть меньше
    font = str(o["font"]).replace("'", "")
    acc, side, btn, text, bg = c["accent"], c["sidebar"], c["button"], c["text"], c["background"]
    card = c.get("card", mix(bg, "#ffffff", 0.08))
    hover = mix(btn, "#ffffff", 0.18)
    glass = rgba(card, round(int(o['glass']) / 100, 2))
    glass2 = rgba(card, round(int(o['glass']) / 100 * 0.56, 2))
    return f"""
* {{ font-family: '{font}', 'Segoe UI', 'Inter', 'Arial'; font-size: {fs}px; color: {text}; outline: 0; }}
QMainWindow {{ background: {bg}; }}
#sidebar {{ background: {rgba(side, 0.82)}; border-right: 1px solid rgba(255,255,255,0.07); border-bottom-left-radius: {wr}px; }}
#sidebar[maxed="true"] {{ border-bottom-left-radius: 0; }}
#titlebar {{ background: {rgba(side, 0.92)}; border-bottom: 1px solid rgba(255,255,255,0.08);
    border-top-left-radius: {wr}px; border-top-right-radius: {wr}px; }}
#titlebar[maxed="true"] {{ border-top-left-radius: 0; border-top-right-radius: 0; }}
QToolButton#wbtn, QToolButton#wbtnClose {{ background: transparent; border: none; border-radius: 8px; min-width: 42px; min-height: 28px; font-size: 14px; }}
QToolButton#wbtn:hover {{ background: rgba(255,255,255,0.14); }}
QToolButton#wbtnClose:hover {{ background: #e11d48; color: #ffffff; }}
#pageTitle {{ font-size: 24px; font-weight: 600; }}
#h2 {{ font-size: 15px; font-weight: 600; }}
#nick {{ font-size: 22px; font-weight: 700; }}
#muted {{ color: rgba(255,255,255,0.58); }}
#ok {{ color: #4ade80; }}
QFrame#card {{ background: {glass}; border: 1px solid rgba(255,255,255,0.10); border-radius: {r}px; }}
QFrame#card2 {{ background: {glass2}; border: 1px solid rgba(255,255,255,0.08); border-radius: {rs}px; }}
QFrame#card2:hover {{ border: 1px solid {acc}; }}
QFrame#cardSel {{ background: {glass}; border: 1px solid {acc}; border-radius: {rs}px; }}
QFrame#preset {{ background: {glass2}; border: 1px solid rgba(255,255,255,0.10); border-radius: {rs}px; }}
QFrame#presetSel {{ background: {glass}; border: 2px solid {acc}; border-radius: {rs}px; }}
QPushButton, QToolButton {{ qproperty-iconSize: 16px 16px; }}
QToolButton#nav {{ qproperty-iconSize: 32px 32px; background: transparent; border: none; border-radius: 12px; padding: 10px 4px; min-width: 84px; color: {text}; }}
QToolButton#nav:hover {{ background: rgba(255,255,255,0.07); }}
QToolButton#nav:checked {{ background: rgba(255,255,255,0.10); border-left: 3px solid {acc}; color: {acc}; }}
QToolButton#dots {{ background: transparent; border: none; font-size: 18px; padding: 0 4px; }}
QToolButton#dots::menu-indicator {{ image: none; }}
QPushButton {{ background: {btn}; border: none; border-radius: {rb}px; padding: 9px 16px; font-weight: 600; color: #ffffff; }}
QPushButton:hover {{ background: {hover}; }}
QPushButton:disabled {{ background: rgba(255,255,255,0.10); color: rgba(255,255,255,0.35); }}
QPushButton#play {{ font-size: 22px; font-weight: 800; padding: 13px 40px; border-radius: 12px; color: #04121a;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {acc}, stop:1 {mix(acc, '#ffffff', 0.35)}); border: 1px solid {acc}; }}
QPushButton#play:hover {{ background: {mix(acc, '#ffffff', 0.25)}; }}
QPushButton#accent {{ color: #04121a; background: {acc}; border-radius: 9px; padding: 8px 18px; font-weight: 700; }}
QPushButton#accent:hover {{ background: {mix(acc, '#ffffff', 0.3)}; }}
QPushButton#ghost {{ background: {rgba('#ffffff', 0.08)}; }}
QPushButton#ghost:hover {{ background: {rgba('#ffffff', 0.16)}; }}
QPushButton#danger {{ background: #b91c1c; }}
QPushButton#sub {{ background: {glass2}; text-align: left; padding: 11px 14px; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; }}
QPushButton#sub:hover {{ background: {glass}; }}
QPushButton#sub:checked {{ background: {glass}; border-left: 3px solid {acc}; color: {acc}; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextBrowser, QListWidget {{
    background: rgba(0,0,0,0.30); border: 1px solid rgba(255,255,255,0.10); border-radius: 9px; padding: 7px; selection-background-color: {btn}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {acc}; }}
QComboBox QAbstractItemView {{ background: {side}; border: 1px solid rgba(255,255,255,0.12); selection-background-color: {btn}; }}
QListWidget {{ padding: 4px; }}
QListWidget::item {{ padding: 8px; border-radius: 7px; }}
QListWidget::item:selected {{ background: {btn}; }}
QListWidget::item:hover {{ background: rgba(255,255,255,0.08); }}
QProgressBar {{ background: rgba(255,255,255,0.10); border: none; border-radius: 5px; max-height: 10px; min-height: 10px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 5px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {acc}, stop:1 {mix(acc, '#ffffff', 0.4)}); }}
QCheckBox {{ spacing: 9px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid rgba(255,255,255,0.3); background: rgba(0,0,0,0.3); }}
QCheckBox::indicator:checked {{ background: {acc}; border: 1px solid {acc}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.25); border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QMenu {{ background: {side}; border: 1px solid rgba(255,255,255,0.12); padding: 4px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {btn}; }}
QDialog {{ background: transparent; }}
QFrame#dlgFrame {{ background: {rgba(bg, 0.98)}; border: 1px solid rgba(255,255,255,0.16); border-radius: {wr}px; }}
QFrame#dlgBar {{ background: {rgba(side, 0.95)}; border-bottom: 1px solid rgba(255,255,255,0.08);
    border-top-left-radius: {wr}px; border-top-right-radius: {wr}px; }}
QLabel {{ background: transparent; }}
QTabWidget::pane {{ background: rgba(0,0,0,0.20); border: 1px solid rgba(255,255,255,0.10); border-radius: {rs}px; top: -1px; }}
QTabWidget > QWidget {{ background: transparent; }}
QTabBar::tab {{ background: rgba(255,255,255,0.05); color: {text}; padding: 8px 16px; margin-right: 4px;
    border-top-left-radius: {rs}px; border-top-right-radius: {rs}px; }}
QTabBar::tab:selected {{ background: rgba(255,255,255,0.12); color: {acc}; }}
QTabBar::tab:hover {{ background: rgba(255,255,255,0.09); }}
QToolTip {{ background: {side}; color: {text}; border: 1px solid {acc}; padding: 4px; }}
"""
