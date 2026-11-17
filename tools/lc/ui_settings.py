"""Persistent desktop UI state: theme, fold state and panel/pill anchors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .settings_io import default_path, load_versioned, save_atomic

THEMES = ("dark", "light")


@dataclass
class UiSettings:
    path: Path
    theme: str = "dark"
    harness: str = "codex"
    collapsed: bool = False
    model_bar_open: bool = True
    panel: tuple[int, int] | None = None
    pill: tuple[int, int] | None = None
    error: str = ""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path("ui-settings.json", "LC_UI_SETTINGS")
        data = load_versioned(self.path)
        if data is None:
            if self.path.exists():
                self.error = "界面设置读取失败，原文件已保留；已使用默认外观。"
            return
        theme = data.get("theme", "dark")
        if theme not in THEMES:
            self.error = "界面主题设置无效；已使用深色主题。"
            theme = "dark"
        self.theme = theme
        self.harness = data.get("harness", "codex")
        if self.harness not in ("codex", "claude"):
            self.harness = "codex"
        self.collapsed = bool(data.get("collapsed", False))
        self.model_bar_open = bool(data.get("model_bar_open", True))
        self.panel = _point(data.get("panel"))
        self.pill = _point(data.get("pill"))

    def save(self) -> None:
        save_atomic(self.path, {
            "version": 1,
            "theme": self.theme,
            "harness": self.harness,
            "collapsed": self.collapsed,
            "model_bar_open": self.model_bar_open,
            "panel": _point_dict(self.panel),
            "pill": _point_dict(self.pill),
        })
        self.error = ""


def _point(value: object) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    x, y = value.get("x"), value.get("y")
    if isinstance(x, bool) or isinstance(y, bool):
        return None
    if not isinstance(x, int) or not isinstance(y, int):
        return None
    return x, y


def _point_dict(value: tuple[int, int] | None) -> dict[str, int] | None:
    return {"x": value[0], "y": value[1]} if value else None


def screen_rects() -> list[tuple[int, int, int, int]]:
    """Available geometries of all screens, as (x, y, width, height)."""
    from PySide6.QtGui import QGuiApplication

    return [(rect.x(), rect.y(), rect.width(), rect.height())
            for screen in QGuiApplication.screens()
            for rect in [screen.availableGeometry()]]


def clamp_point(point: tuple[int, int] | None, size: tuple[int, int],
                rects: Iterable[tuple[int, int, int, int]] | None = None,
                margin: int = 8) -> tuple[int, int] | None:
    """Clamp a saved top-left point into a visible screen rectangle."""
    if point is None:
        return None
    width, height = size
    available = list(rects if rects is not None else screen_rects())
    if not available:
        return point
    best = max(available, key=lambda rect: _overlap(rect, point, size))
    x, y, sw, sh = best
    return (min(max(point[0], x + margin), x + max(margin, sw - width - margin)),
            min(max(point[1], y + margin), y + max(margin, sh - height - margin)))


def _overlap(rect: tuple[int, int, int, int], point: tuple[int, int],
             size: tuple[int, int]) -> int:
    x, y, width, height = rect
    left, top = max(x, point[0]), max(y, point[1])
    right = min(x + width, point[0] + size[0])
    bottom = min(y + height, point[1] + size[1])
    return max(0, right - left) * max(0, bottom - top)
