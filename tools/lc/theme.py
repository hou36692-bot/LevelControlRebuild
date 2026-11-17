"""Dark/light visual tokens and the shared Qt stylesheet."""

from __future__ import annotations

from string import Template

DARK = {
    "bg": "#0F1F2E",
    "card_top": "#142B3E",
    "card_bottom": "#0B1823",
    "surface": "#102432",
    "surface_alt": "#173246",
    "border": "#385164",
    "border_strong": "#3E6073",
    "text": "#E2EDF2",
    "muted": "#91A8B7",
    "accent": "#78DDD0",
    "accent_strong": "#376F72",
    "accent_text": "#06211F",
    "warm": "#F2C57C",
    "danger": "#E08078",
    "success": "#7EE0B5",
    "user_bubble": "#274A5E",
    "user_border": "#3D7F83",
    "ai_bubble": "#12293A",
    "ai_border": "#2F4A5D",
    "input": "#102432",
    "button": "#2B495B",
    "button_hover": "#3A6476",
    "button_disabled": "#243543",
    "disabled_text": "#708798",
    "selection": "#376F72",
    "scrollbar": "#446473",
}

LIGHT = {
    "bg": "#EEF2F5",
    "card_top": "#FFFFFF",
    "card_bottom": "#F7FAFB",
    "surface": "#F4F7F9",
    "surface_alt": "#E9F0F3",
    "border": "#D6E0E6",
    "border_strong": "#B9CAD3",
    "text": "#1B2A33",
    "muted": "#5D7280",
    "accent": "#0F766E",
    "accent_strong": "#0B5F59",
    "accent_text": "#FFFFFF",
    "warm": "#B45309",
    "danger": "#B91C1C",
    "success": "#15803D",
    "user_bubble": "#DFF3F0",
    "user_border": "#9ED8D1",
    "ai_bubble": "#F2F6F8",
    "ai_border": "#D6E0E6",
    "input": "#FFFFFF",
    "button": "#E4EDF1",
    "button_hover": "#D5E4EA",
    "button_disabled": "#EDF2F4",
    "disabled_text": "#93A6B0",
    "selection": "#B9E6E0",
    "scrollbar": "#B9CAD3",
}

PALETTES = {"dark": DARK, "light": LIGHT}

_STYLE = Template("""
QWidget {
    color: $text;
    font-family: 'Microsoft YaHei UI';
    font-size: 13px;
}
QDialog { background: $bg; color: $text; }
QTabWidget::pane { background: $surface; border: 1px solid $border; }
QWidget#settingsPage { background: $surface; }
QTabBar::tab { background: $surface_alt; color: $muted; padding: 8px 16px; }
QTabBar::tab:selected { background: $surface; color: $text; }
QFrame#card {
    background: qlineargradient(x1:0, y1:0, x2:0.9, y2:1,
                                stop:0 $card_top, stop:1 $card_bottom);
    border: 1px solid $border;
    border-radius: 22px;
}
QFrame#pill, QPushButton#pill {
    background: $surface;
    border: 1px solid $border;
    border-radius: 26px;
}
QPushButton#pill {
    padding: 4px 14px 4px 30px;
    text-align: left;
    font-size: 12px;
}
QPushButton#pill:hover { border-color: $accent; }
QLabel#brand { color: $accent; font-size: 14px; font-weight: 700; letter-spacing: 2px; }
QLabel#status { color: $warm; font-size: 12px; }
QLabel#caption, QLabel#hint { color: $muted; font-size: 11px; }
QLabel#partial { color: $accent; font-size: 12px; }
QLabel#errorText { color: $danger; font-size: 12px; }
QLabel#bubbleText { background: transparent; padding: 9px 11px; }
QLabel#tool { color: $muted; font-family: Consolas; font-size: 11px; padding: 2px 4px; }
QFrame#userBubble { background: $user_bubble; border: 1px solid $user_border; border-radius: 14px; }
QFrame#aiBubble { background: $ai_bubble; border: 1px solid $ai_border; border-radius: 14px; }
QFrame#noticeBubble { background: transparent; }
QFrame#toolTrace { background: $surface; border: 1px solid $border; border-radius: 10px; }
QPushButton {
    background: $button; border: none; border-radius: 8px; padding: 8px 12px;
    color: $text;
}
QPushButton:hover { background: $button_hover; }
QPushButton:pressed { background: $border_strong; }
QPushButton:disabled { background: $button_disabled; color: $disabled_text; }
QPushButton#iconButton {
    background: transparent; border: none; border-radius: 8px; padding: 5px 8px;
}
QPushButton#iconButton:hover { background: $surface_alt; }
QPushButton#chip {
    background: $surface_alt; border: 1px solid $border; border-radius: 13px;
    padding: 4px 10px; font-size: 12px; text-align: left;
}
QPushButton#chip:hover { border-color: $accent; }
QPushButton#talk { background: $accent_strong; color: $accent_text; font-weight: 600; }
QPushButton#talk:pressed { background: $danger; color: #FFFFFF; }
QPushButton#talk:disabled {
    background: $button_disabled; color: $disabled_text; font-weight: 400;
}
QPushButton#danger { background: $danger; color: #FFFFFF; }
QPushButton#danger:disabled { background: $button_disabled; color: $disabled_text; }
QPushButton#primary { background: $accent_strong; color: $accent_text; }
QPushButton#primary:disabled { background: $button_disabled; color: $disabled_text; }
QLineEdit, QComboBox, QPlainTextEdit, QListWidget, QSpinBox {
    background: $input; border: 1px solid $border; border-radius: 9px;
    padding: 7px; color: $text;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QListWidget:focus {
    border-color: $accent;
}
QLineEdit::placeholder { color: $muted; }
QComboBox QAbstractItemView {
    background: $surface; border: 1px solid $border; color: $text;
    selection-background-color: $selection;
}
QListWidget::item:selected { background: $selection; }
QScrollArea, QScrollArea QWidget { background: transparent; }
QScrollBar:vertical { width: 6px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: $scrollbar; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 6px; background: transparent; }
QScrollBar::handle:horizontal { background: $scrollbar; border-radius: 3px; }
QMenu { background: $surface; border: 1px solid $border; color: $text; padding: 4px; }
QMenu::item { padding: 7px 18px; border-radius: 6px; }
QMenu::item:selected { background: $selection; }
QToolTip { background: $surface; color: $text; border: 1px solid $border; padding: 5px; }
QSlider::groove:horizontal { height: 4px; background: $border; border-radius: 2px; }
QSlider::handle:horizontal {
    background: $accent; width: 14px; margin: -5px 0; border-radius: 7px;
}
QProgressBar {
    background: $surface; border: 1px solid $border; border-radius: 5px;
    text-align: center;
}
QProgressBar::chunk { background: $accent_strong; border-radius: 4px; }
""")


def colors(theme: str) -> dict[str, str]:
    return PALETTES.get(theme, DARK)


def stylesheet(theme: str) -> str:
    return _STYLE.substitute(colors(theme))
