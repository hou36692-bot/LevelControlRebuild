"""Desktop reference-image controls; decoding never runs on the GUI thread."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .images import MAX_IMAGES, ImageAttachment, ImageStore


def mime_sources(mime: Any) -> list[Any]:
    if mime.hasUrls():
        return [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
    if mime.hasImage():
        data = mime.imageData()
        return [data.toImage().copy() if isinstance(data, QPixmap) else QImage(data).copy()]
    return []


class ImageLineEdit(QLineEdit):
    images_added = Signal(list)

    def keyPressEvent(self, event: Any) -> None:
        if event.matches(QKeySequence.StandardKey.Paste):
            sources = mime_sources(QApplication.clipboard().mimeData())
            if sources:
                self.images_added.emit(sources)
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasImage() or any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: Any) -> None:
        sources = mime_sources(event.mimeData())
        if sources:
            self.images_added.emit(sources)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def contextMenuEvent(self, event: Any) -> None:
        menu = self.createStandardContextMenu()
        if QApplication.clipboard().mimeData().hasImage():
            action = menu.addAction("粘贴参考图")
            action.triggered.connect(lambda: self.images_added.emit(
                mime_sources(QApplication.clipboard().mimeData())))
        menu.exec(event.globalPos())


class ReferenceImages(QWidget):
    changed = Signal()
    prepared = Signal(int, object, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.store = ImageStore()
        self.images: tuple[ImageAttachment, ...] = ()
        self.submitted = False
        self.processing = False
        self.failed = False
        self.closed = False
        self.generation = 0
        self.cancelled = threading.Event()
        self.sources: list[Any] = []
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reference-images")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.add = QPushButton("添加图片")
        self.add.setAccessibleName("添加参考图")
        self.add.clicked.connect(self.choose)
        self.clear = QPushButton("清空")
        self.clear.clicked.connect(self.clear_images)
        self.cancel = QPushButton("取消处理")
        self.cancel.clicked.connect(self.cancel_import)
        self.retry = QPushButton("重试图片处理")
        self.retry.clicked.connect(lambda: self.add_sources(self.sources))
        for button in (self.add, self.clear, self.cancel, self.retry):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #91a8b7; font-size: 11px;")
        layout.addWidget(self.status)
        self.thumbnails = QWidget()
        self.thumbnail_row = QHBoxLayout(self.thumbnails)
        self.thumbnail_row.setContentsMargins(0, 0, 0, 0)
        self.thumbnail_row.setSpacing(5)
        layout.addWidget(self.thumbnails)
        self.prepared.connect(self.finish)
        self.refresh()

    @property
    def blocked(self) -> bool:
        return self.processing or self.failed

    def choose(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择参考图片", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if paths:
            self.add_sources([Path(path) for path in paths])

    def add_sources(self, sources: list[Any]) -> None:
        if self.closed or self.processing or not sources:
            return
        base = () if self.submitted else self.images
        if len(base) + len(sources) > MAX_IMAGES:
            self.status.setText("每组最多 4 张参考图，请减少选择数量。")
            return
        self.sources = list(sources)
        self.generation += 1
        generation = self.generation
        self.cancelled = threading.Event()
        cancelled, store = self.cancelled, self.store
        self.processing, self.failed = True, False
        self.refresh()

        def prepare() -> None:
            results = []
            error = ""
            try:
                for source in sources:
                    if cancelled.is_set():
                        return
                    results.append(store.prepare(source))
            except Exception as failure:
                error = (str(failure) if isinstance(failure, ValueError)
                         else "图片处理失败，请重试。")
                results = []
            if not cancelled.is_set():
                try:
                    self.prepared.emit(generation, base + tuple(results), error)
                except RuntimeError:
                    pass

        self.worker.submit(prepare)

    def finish(self, generation: int, images: tuple[ImageAttachment, ...], error: str) -> None:
        if self.closed or generation != self.generation:
            return
        self.processing, self.failed = False, bool(error)
        if not error:
            self.images, self.submitted, self.sources = images, False, []
        self.refresh()
        if error:
            self.status.setText(error + " 可重试或撤回本批次；文字和原参考图已保留。")

    def cancel_import(self) -> None:
        self.cancelled.set()
        self.generation += 1
        self.processing, self.failed, self.sources = False, False, []
        self.refresh()

    def clear_images(self) -> None:
        self.cancel_import()
        self.images, self.submitted = (), False
        self.refresh()

    def remove(self, index: int) -> None:
        if self.processing:
            return
        self.images = self.images[:index] + self.images[index + 1:]
        self.submitted = False
        self.refresh()

    def mark_submitted(self) -> None:
        self.submitted = bool(self.images)
        self.refresh()

    def refresh(self) -> None:
        self.add.setEnabled(not self.processing)
        self.cancel.setVisible(self.blocked)
        self.cancel.setText("取消处理" if self.processing else "撤回本批次")
        self.retry.setVisible(self.failed)
        self.clear.setEnabled(bool(self.images) or self.blocked)
        if self.processing:
            self.status.setText("正在处理图片…完成后可发送；可取消。")
        elif not self.failed:
            self.status.setText(f"参考图 {len(self.images)} 张 · " + (
                "后续文字/语音沿用；再添加将开始新组" if self.submitted else "发送时使用当前组")
                if self.images else "支持选图、拖拽、Ctrl+V · 每组最多 4 张")
        while self.thumbnail_row.count():
            widget = self.thumbnail_row.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        for index, image in enumerate(self.images):
            card = QWidget()
            column = QVBoxLayout(card)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)
            preview = QPushButton()
            preview.setIcon(QPixmap(str(image.path)))
            preview.setIconSize(QSize(65, 48))
            preview.setFixedSize(77, 54)
            preview.setToolTip(f"参考图 {index + 1} · {image.width}×{image.height} · 点击放大")
            preview.setAccessibleName(f"放大参考图 {index + 1}")
            preview.clicked.connect(lambda checked=False, value=image: self.show_image(value))
            remove = QPushButton(f"{index + 1} · 删除")
            remove.setEnabled(not self.processing)
            remove.clicked.connect(lambda checked=False, value=index: self.remove(value))
            column.addWidget(preview)
            column.addWidget(remove)
            self.thumbnail_row.addWidget(card)
        self.thumbnails.setVisible(bool(self.images))
        self.changed.emit()

    def show_image(self, image: ImageAttachment) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("参考图预览")
        dialog.resize(850, 650)
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        label = QLabel()
        label.setPixmap(QPixmap(str(image.path)).scaled(
            800, 580, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(label)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)
        dialog.exec()

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasImage() or any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:
        sources = mime_sources(event.mimeData())
        if sources:
            self.add_sources(sources)
            event.acceptProposedAction()

    def shutdown(self) -> None:
        self.closed = True
        self.cancel_import()
        self.images = ()
        self.sources = []
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.store = None
