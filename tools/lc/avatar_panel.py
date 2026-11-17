"""Avatar picker dialog: import local images, tune scale/opacity, reset."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .avatar import AvatarConfig, AvatarError, AvatarSettings, AvatarView
from .widgets import label

DEFAULT_ID = "default"


class AvatarPanel(QDialog):
    def __init__(self, settings: AvatarSettings, theme_name: str = "dark",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.theme_name = theme_name
        self.staged: list[AvatarConfig] = []
        self.removed: list[AvatarConfig] = []
        self.setWindowTitle("LevelControl · 形象设置")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(640, 520)
        self.setStyleSheet(theme.stylesheet(theme_name))
        outer = QVBoxLayout(self)
        columns = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.selected)
        left.addWidget(self.list, 1)
        upload = QPushButton("导入图片…")
        upload.clicked.connect(self.import_image)
        left.addWidget(upload)
        self.delete = QPushButton("删除当前图片")
        self.delete.clicked.connect(self.delete_current)
        left.addWidget(self.delete)
        reset = QPushButton("恢复默认")
        reset.clicked.connect(self.reset_default)
        left.addWidget(reset)
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        self.preview = AvatarView()
        self.preview.setMinimumSize(200, 260)
        right.addWidget(self.preview, 1)
        self.name = QLineEdit()
        self.name.setPlaceholderText("形象名称")
        right.addWidget(self.name)
        right.addWidget(label("大小", "caption"))
        self.scale = QSlider(Qt.Orientation.Horizontal)
        self.scale.setRange(60, 130)
        right.addWidget(self.scale)
        right.addWidget(label("不透明度", "caption"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        right.addWidget(self.opacity)
        self.message = label("支持 PNG、JPG、GIF、WebP、BMP；最大 12 MiB。"
                             "不支持 Live2D 模型包。", "caption")
        right.addWidget(self.message)
        columns.addLayout(right, 2)
        outer.addLayout(columns, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存并使用")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        outer.addLayout(footer)
        self.refresh_list(settings.active)
        self.scale.valueChanged.connect(self.apply_preview)
        self.opacity.valueChanged.connect(self.apply_preview)
        self.name.textChanged.connect(self.apply_preview)

    def refresh_list(self, active: str = "") -> None:
        active = active or self.settings.active
        self.list.blockSignals(True)
        self.list.clear()
        item = QListWidgetItem("默认形象（建筑助手）")
        item.setData(Qt.ItemDataRole.UserRole, DEFAULT_ID)
        self.list.addItem(item)
        for config in self.settings.avatars:
            entry = QListWidgetItem(config.name)
            entry.setData(Qt.ItemDataRole.UserRole, config.id)
            self.list.addItem(entry)
        self.list.blockSignals(False)
        index = next((i for i in range(self.list.count())
                      if self.list.item(i).data(Qt.ItemDataRole.UserRole) == active), 0)
        self.list.setCurrentRow(index)
        self.selected(self.list.currentItem(), None)

    def current_config(self) -> AvatarConfig | None:
        item = self.list.currentItem()
        if item is None:
            return None
        identity = item.data(Qt.ItemDataRole.UserRole)
        return next((c for c in self.settings.avatars if c.id == identity), None)

    def selected(self, item: QListWidgetItem | None, previous: object) -> None:
        config = self.current_config()
        enabled = config is not None
        self.name.setEnabled(enabled)
        self.scale.setEnabled(enabled)
        self.opacity.setEnabled(enabled)
        self.delete.setEnabled(enabled)
        self.scale.blockSignals(True)
        self.opacity.blockSignals(True)
        if config is None:
            self.name.setText("")
            self.scale.setValue(100)
            self.opacity.setValue(100)
        else:
            self.name.setText(config.name)
            self.scale.setValue(round(config.scale * 100))
            self.opacity.setValue(round(config.opacity * 100))
        self.scale.blockSignals(False)
        self.opacity.blockSignals(False)
        self.apply_preview()

    def apply_preview(self) -> None:
        config = self.current_config()
        if config is not None:
            config.scale = self.scale.value() / 100
            config.opacity = self.opacity.value() / 100
            if self.name.text().strip():
                config.name = self.name.text().strip()[:60]
        resolved = self.settings.resolve(config)
        self.preview.apply(config, resolved)
        self.message.setText("图片文件已丢失，已显示默认形象。"
                             if config is not None and resolved is None
                             else "支持 PNG、JPG、GIF、WebP、BMP；最大 12 MiB。"
                                  "不支持 Live2D 模型包。")

    def import_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择形象图片", "",
            "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if not path:
            return
        try:
            config = self.settings.stage(Path(path))
        except (AvatarError, OSError) as error:
            self.message.setText(str(error))
            return
        self.staged.append(config)
        self.settings.avatars.append(config)
        self.refresh_list(config.id)

    def delete_current(self) -> None:
        config = self.current_config()
        if config is None:
            return
        self.settings.avatars = [item for item in self.settings.avatars
                                 if item.id != config.id]
        self.removed.append(config)
        self.refresh_list(DEFAULT_ID)

    def reset_default(self) -> None:
        self.refresh_list(DEFAULT_ID)

    def save(self) -> None:
        for config in list(self.staged):
            self.settings.avatars = [item for item in self.settings.avatars
                                     if item.id != config.id]
            try:
                self.settings.commit(config)
            except OSError as error:
                self.message.setText(f"形象保存失败：{error}")
                return
            self.staged.remove(config)
        for config in self.removed:
            try:
                (self.settings.path.parent / config.file).resolve().unlink(missing_ok=True)
            except OSError:
                self.message.setText("图片正在使用，将在下次启动时清理。")
        self.removed.clear()
        item = self.list.currentItem()
        identity = item.data(Qt.ItemDataRole.UserRole) if item else DEFAULT_ID
        self.settings.active = identity if identity != DEFAULT_ID else "default"
        config = self.settings.active_config()
        if config is not None:
            config.scale = self.scale.value() / 100
            config.opacity = self.opacity.value() / 100
            if self.name.text().strip():
                config.name = self.name.text().strip()[:60]
        try:
            self.settings.save()
        except OSError as error:
            self.message.setText(f"形象设置保存失败：{error}；原设置已保留。")
            return
        self.accept()

    def reject(self) -> None:
        for config in list(self.staged):
            self.settings.discard(config)
            self.settings.avatars = [item for item in self.settings.avatars
                                     if item.id != config.id]
            self.staged.remove(config)
        super().reject()
