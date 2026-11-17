"""Small reusable widgets shared by the desktop client and its dialogs."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import colors


def label(text: str, name: str = "") -> QLabel:
    result = QLabel(text)
    result.setObjectName(name)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return result


def elide(text: str, width: int, widget: QWidget) -> str:
    metrics = QFontMetrics(widget.font())
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, width))


class StatusDot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color = QColor("#7EE0B5")

    def set_color(self, value: str) -> None:
        self._color = QColor(value)
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(1, 1, 8, 8)


class StatusPill(QFrame):
    """Status dot plus a short plain-text status line."""

    STATE_TOKENS = {
        "ready": "success",
        "busy": "warm",
        "offline": "danger",
        "error": "danger",
        "recording": "danger",
    }

    def __init__(self, theme: str = "dark", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.dot = StatusDot()
        self.text = label("正在连接…", "status")
        self.text.setWordWrap(False)
        row.addWidget(self.dot)
        row.addWidget(self.text, 1)

    def set_theme(self, theme: str) -> None:
        self.theme = theme

    def set_status(self, text: str, state: str = "busy") -> None:
        self.text.setText(text)
        self.text.setToolTip(text)
        self.dot.set_color(colors(self.theme)[self.STATE_TOKENS.get(state, "warm")])


class PillButton(QPushButton):
    """The collapsed panel: click expands, dragging moves the top-level window."""

    clicked_pill = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pill")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._press_global = QPoint()
        self._start_window = QPoint()
        self._moved = False
        self._status_color = QColor("#7EE0B5")

    def set_status_color(self, value: str) -> None:
        self._status_color = QColor(value)
        self.update()

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._status_color)
        painter.drawEllipse(14, self.height() // 2 - 5, 10, 10)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._start_window = self.window().pos()
            self._moved = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self._moved = True
                self.window().move(self._start_window + delta)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._moved:
            self.clicked_pill.emit()
        super().mouseReleaseEvent(event)


class ProjectChip(QPushButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._full = ""
        self.setMinimumWidth(140)
        self.setMaximumWidth(280)

    def set_target(self, name: str, port: int, detail: str = "") -> None:
        self._full = f"{name} · {port}"
        self.setToolTip(detail or self._full)
        self.setText(elide(self._full, self.width() - 22, self))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self._full:
            self.setText(elide(self._full, self.width() - 22, self))


class MessageBubble(QFrame):
    def __init__(self, role: str, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.role = role
        self.setObjectName({
            "user": "userBubble",
            "assistant": "aiBubble",
            "notice": "noticeBubble",
            "error": "noticeBubble",
        }.get(role, "aiBubble"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.text = label(text, "bubbleText")
        if role == "error":
            self.text.setObjectName("errorText")
        if role == "notice":
            self.text.setObjectName("caption")
        self.text.setWordWrap(True)
        layout.addWidget(self.text)

    def set_text(self, text: str) -> None:
        self.text.setText(text)

    def append_text(self, text: str) -> None:
        self.text.setText(self.text.text() + text)


class ToolTrace(QFrame):
    """Collapsible, plain-text summary of the three most recent tool calls."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toolTrace")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 5, 8, 5)
        outer.setSpacing(2)
        self.header = QPushButton("工具轨迹 ▾")
        self.header.setObjectName("iconButton")
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.clicked.connect(self.toggle)
        outer.addWidget(self.header)
        self.rows = QWidget()
        row_layout = QVBoxLayout(self.rows)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(1)
        self.labels = [label("—", "tool") for _ in range(3)]
        for item in self.labels:
            item.setWordWrap(False)
            row_layout.addWidget(item)
        outer.addWidget(self.rows)
        self._open = True
        self._tools: list[dict[str, Any]] = []

    def toggle(self) -> None:
        self._open = not self._open
        self.rows.setVisible(self._open)
        self.header.setText("工具轨迹 ▾" if self._open else "工具轨迹 ▸")

    def set_tools(self, tools: list[dict[str, Any]]) -> None:
        self._tools = tools[-3:]
        for widget, item in zip(self.labels, self._tools + [None] * (3 - len(self._tools))):
            if item is None:
                widget.setText("—")
                widget.setToolTip("")
                widget.setVisible(False)
                continue
            phase = ("执行中" if item.get("phase") == "started"
                     else f"{item.get('seconds', 0):.2f} s")
            command = str(item.get("arguments", {}).get("command", ""))
            summary = command if len(command) <= 72 else command[:69] + "…"
            widget.setText(f"{item.get('name', '')} · {phase}  {summary}")
            widget.setToolTip(command)
            widget.setVisible(True)
