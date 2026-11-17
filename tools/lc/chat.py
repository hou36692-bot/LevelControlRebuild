"""Scrollable plain-text conversation view for the desktop companion."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .widgets import MessageBubble


class ChatView(QScrollArea):
    MAX_MESSAGES = 300
    THROTTLE_MS = 50

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(110)
        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(7)
        self._layout.addStretch(1)
        self.setWidget(self._content)
        self._rows: list[QWidget] = []
        self._bubbles: list[MessageBubble] = []
        self._current: MessageBubble | None = None
        self._pending = ""
        self._near_bottom = True
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(self.THROTTLE_MS)
        self._flush_timer.timeout.connect(self._flush)
        self.verticalScrollBar().valueChanged.connect(self._scroll_changed)

    def _scroll_changed(self, value: int) -> None:
        bar = self.verticalScrollBar()
        self._near_bottom = value >= bar.maximum() - 24

    def _add_row(self, bubble: MessageBubble, align: str) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(0)
        if align == "right":
            layout.addStretch(1)
            layout.addWidget(bubble)
        elif align == "left":
            layout.addWidget(bubble)
            layout.addStretch(1)
        else:
            layout.addStretch(1)
            layout.addWidget(bubble)
            layout.addStretch(1)
        self._layout.insertWidget(self._layout.count() - 1, row)
        self._rows.append(row)
        self._bubbles.append(bubble)
        bubble.setMaximumWidth(self._bubble_width(bubble.role))
        while len(self._rows) > self.MAX_MESSAGES:
            old = self._rows.pop(0)
            self._bubbles.pop(0)
            self._layout.removeWidget(old)
            old.deleteLater()
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _bubble_width(self, role: str) -> int:
        ratio = 0.78 if role == "user" else 0.86
        if role in {"notice", "error"}:
            ratio = 0.94
        return max(120, int(self.viewport().width() * ratio))

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        for bubble in self._bubbles:
            bubble.setMaximumWidth(self._bubble_width(bubble.role))
        if self._near_bottom:
            QTimer.singleShot(0, self._scroll_to_bottom)

    def add_user(self, text: str) -> None:
        bubble = MessageBubble("user", text)
        self._add_row(bubble, "right")

    def begin_assistant(self, text: str = "正在思考…") -> None:
        self._flush()
        self._current = MessageBubble("assistant", text)
        self._pending = ""
        self._add_row(self._current, "left")

    def append_assistant(self, delta: str) -> None:
        if self._current is None:
            self.begin_assistant("")
        self._pending += delta
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush(self) -> None:
        if self._current is None:
            return
        self._current.set_text(self._current.text.text() + self._pending if self._pending
                               else self._current.text.text())
        self._pending = ""
        if self._near_bottom:
            QTimer.singleShot(0, self._scroll_to_bottom)

    def finish_assistant(self, text: str) -> None:
        self._flush_timer.stop()
        if self._current is None:
            self.begin_assistant(text)
        else:
            self._current.set_text(text)
        self._pending = ""
        self._current = None
        QTimer.singleShot(0, self._scroll_to_bottom)

    def add_notice(self, text: str) -> None:
        self._add_row(MessageBubble("notice", text), "center")

    def add_error(self, text: str) -> None:
        self._add_row(MessageBubble("error", text), "center")

    def clear(self) -> None:
        self._flush_timer.stop()
        self._pending = ""
        self._current = None
        for row in self._rows:
            self._layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        self._bubbles.clear()

    def text(self) -> str:
        """Plain-text contents, primarily for tests and diagnostics."""
        parts = [bubble.text.text() for bubble in self._bubbles]
        if self._pending:
            parts.append(self._pending)
        return "\n".join(parts)

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._near_bottom = True
