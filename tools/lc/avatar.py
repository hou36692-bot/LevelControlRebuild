"""User-supplied image avatars and the floating desktop portrait widget."""

from __future__ import annotations

import hashlib
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QImageReader, QMovie, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from .settings_io import default_path, load_versioned, save_atomic

DEFAULT_SVG = Path(__file__).with_name("assets") / "architect.svg"
ALLOWED_FORMATS = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}
MAX_BYTES = 12 * 1024 * 1024
MAX_STATIC = 4096
MAX_ANIMATED = 2048
MAX_PIXELS = 16_000_000
MAX_FRAMES = 600
MIN_SIDE = 64


@dataclass
class AvatarConfig:
    id: str
    name: str
    file: str
    scale: float = 1.0
    opacity: float = 1.0
    width: int = 0
    height: int = 0
    animated: bool = False
    format: str = ""


class AvatarError(ValueError):
    pass


def inspect_image(path: Path) -> dict[str, Any]:
    """Validate content (not just extension) before any full decode."""
    try:
        size = path.stat().st_size
    except OSError as error:
        raise AvatarError("无法读取该图片文件。") from error
    if size > MAX_BYTES:
        raise AvatarError("图片超过 12 MiB，请先压缩。")
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    fmt = bytes(reader.format()).decode("ascii", "replace").lower()
    if fmt not in ALLOWED_FORMATS:
        raise AvatarError("无法读取该图片；支持 PNG、JPG、WebP、GIF、BMP。")
    dimensions = reader.size()
    if not dimensions.isValid() or dimensions.width() < MIN_SIDE or dimensions.height() < MIN_SIDE:
        raise AvatarError(f"图片尺寸需在 {MIN_SIDE}–{MAX_STATIC} 像素之间。")
    animated = bool(reader.supportsAnimation() and
                    (reader.imageCount() > 1 or reader.loopCount() > 0))
    limit = MAX_ANIMATED if animated else MAX_STATIC
    if dimensions.width() > limit or dimensions.height() > limit:
        raise AvatarError(f"{'动画' if animated else ''}图片尺寸需在 "
                          f"{MIN_SIDE}–{limit} 像素之间。")
    if dimensions.width() * dimensions.height() > MAX_PIXELS:
        raise AvatarError("图片像素过大，请缩小后重试。")
    frames = reader.imageCount() if animated else 1
    if frames > MAX_FRAMES:
        raise AvatarError("动画帧数过多，请使用更短的动画。")
    image = reader.read()
    if image.isNull():
        raise AvatarError("无法解码该图片。")
    return {
        "format": fmt,
        "suffix": "." + ("jpg" if fmt == "jpeg" else fmt),
        "width": image.width(),
        "height": image.height(),
        "animated": animated,
        "frames": frames,
    }


class AvatarSettings:
    """A small copy-managed store; user files never enter the repository."""

    def __init__(self, path: Path | None = None, root: Path | None = None) -> None:
        self.path = path or default_path("avatar-settings.json", "LC_AVATAR_SETTINGS")
        configured_root = os.environ.get("LC_AVATAR_ROOT")
        self.root = root or (Path(configured_root) if configured_root
                             else self.path.parent / "avatars")
        self.active = "default"
        self.avatars: list[AvatarConfig] = []
        self.error = ""
        data = load_versioned(self.path)
        if data is None:
            if self.path.exists():
                self.error = "形象设置读取失败，原文件已保留；已显示默认形象。"
        else:
            active = data.get("active", "default")
            self.active = active if isinstance(active, str) else "default"
            for item in data.get("avatars", []):
                if not isinstance(item, dict):
                    continue
                try:
                    config = AvatarConfig(
                        id=str(item["id"]), name=str(item["name"]), file=str(item["file"]),
                        scale=float(item.get("scale", 1.0)),
                        opacity=float(item.get("opacity", 1.0)),
                        width=int(item.get("width", 0)), height=int(item.get("height", 0)),
                        animated=bool(item.get("animated", False)),
                        format=str(item.get("format", "")),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                self.avatars.append(config)
        if self.active != "default" and not self.active_config():
            self.active = "default"

    def active_config(self) -> AvatarConfig | None:
        return next((item for item in self.avatars if item.id == self.active), None)

    def resolve(self, config: AvatarConfig | None) -> Path | None:
        if config is None:
            return None
        path = (self.path.parent / config.file).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            self.error = "形象文件路径无效，已显示默认形象。"
            return None
        return path if path.is_file() else None

    def stage(self, source: Path, name: str = "") -> AvatarConfig:
        source = source.resolve()
        info = inspect_image(source)
        self.root.mkdir(parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        pending = self.root / f".pending-{identifier}{info['suffix']}"
        temporary = pending.with_suffix(pending.suffix + ".tmp")
        digest = hashlib.sha256()
        try:
            with source.open("rb") as reader, temporary.open("wb") as writer:
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    writer.write(chunk)
            temporary.replace(pending)
            inspect_image(pending)
        except Exception:
            temporary.unlink(missing_ok=True)
            pending.unlink(missing_ok=True)
            raise
        display = (name.strip() or source.stem).strip()[:60]
        try:
            relative = str(pending.relative_to(self.path.parent))
        except ValueError:
            relative = str(pending)
        return AvatarConfig(identifier, display or source.stem[:60], relative,
                            width=info["width"], height=info["height"],
                            animated=info["animated"], format=info["format"])

    def commit(self, config: AvatarConfig) -> None:
        pending = (self.path.parent / config.file).resolve()
        final = self.root / f"{uuid.uuid4().hex}{pending.suffix}"
        pending.replace(final)
        try:
            config.file = str(final.relative_to(self.path.parent))
        except ValueError:
            config.file = str(final)
        previous = list(self.avatars), self.active
        self.avatars.append(config)
        self.active = config.id
        try:
            self.save()
        except Exception:
            self.avatars, self.active = previous
            final.unlink(missing_ok=True)
            raise

    def discard(self, config: AvatarConfig) -> None:
        path = (self.path.parent / config.file).resolve()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def delete_current(self) -> None:
        config = self.active_config()
        if config is None:
            return
        path = (self.path.parent / config.file).resolve()
        self.avatars = [item for item in self.avatars if item.id != config.id]
        self.active = "default"
        self.save()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            self.error = "图片正在使用，将在下次启动时清理。"

    def reset(self) -> None:
        self.active = "default"
        self.save()

    def set_style(self, scale: float, opacity: float) -> None:
        config = self.active_config()
        if config is None:
            return
        config.scale = min(1.3, max(0.6, float(scale)))
        config.opacity = min(1.0, max(0.3, float(opacity)))
        self.save()

    def save(self) -> None:
        save_atomic(self.path, {
            "version": 1,
            "active": self.active,
            "avatars": [{
                "id": item.id, "name": item.name, "file": item.file,
                "scale": item.scale, "opacity": item.opacity,
                "width": item.width, "height": item.height,
                "animated": item.animated, "format": item.format,
            } for item in self.avatars],
        })
        self.error = ""


class AvatarView(QWidget):
    """SVG/static/GIF portrait with a subtle idle float."""

    fallback_changed = Signal(bool)
    activated = Signal()

    def __init__(self, default_svg: Path = DEFAULT_SVG, parent: QWidget | None = None,
                 movie_factory: type[QMovie] = QMovie, animations: bool = True) -> None:
        super().__init__(parent)
        self.default_svg = default_svg
        self.movie_factory = movie_factory
        self._svg = QSvgRenderer(str(default_svg), self)
        self._pixmap: QPixmap | None = None
        self._movie: QMovie | None = None
        self._scale = 1.0
        self._opacity = 1.0
        self._float = 0.0
        self._fallback = False
        self.setMinimumSize(140, 190)
        self.setAccessibleName("助手形象")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("双击更换形象")
        self._animation = QPropertyAnimation(self, b"floatPhase", self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(4200)
        self._animation.setLoopCount(-1)
        if animations and not os.environ.get("LC_DISABLE_IDLE_ANIMATION"):
            self._animation.start()

    def get_float_phase(self) -> float:
        return self._float

    def set_float_phase(self, value: float) -> None:
        self._float = value
        self.update()

    floatPhase = Property(float, get_float_phase, set_float_phase)

    def apply(self, config: AvatarConfig | None, resolved: Path | None) -> None:
        self._stop_movie()
        self._pixmap = None
        self._scale = config.scale if config else 1.0
        self._opacity = config.opacity if config else 1.0
        self._fallback = False
        if config is None:
            self._load_svg()
            return
        if resolved is None:
            self._fallback = True
            self._load_svg()
            self.fallback_changed.emit(True)
            return
        if config.animated:
            movie = self.movie_factory(str(resolved))
            movie.setCacheMode(QMovie.CacheMode.CacheNone)
            if not movie.isValid():
                self._fallback = True
                self._load_svg()
                self.fallback_changed.emit(True)
                return
            movie.frameChanged.connect(self.update)
            movie.error.connect(lambda _error: self._movie_error())
            self._movie = movie
            self._movie.start()
        else:
            pixmap = QPixmap(str(resolved))
            if pixmap.isNull():
                self._fallback = True
                self._load_svg()
                self.fallback_changed.emit(True)
                return
            self._pixmap = pixmap
            self.update()

    def _movie_error(self) -> None:
        self._stop_movie()
        self._fallback = True
        self._load_svg()
        self.fallback_changed.emit(True)

    def _stop_movie(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None

    def _load_svg(self) -> None:
        self.update()

    def _source(self) -> QPixmap | None:
        if self._movie is not None:
            return self._movie.currentPixmap()
        if self._pixmap is not None:
            return self._pixmap
        size = self.size() * self.devicePixelRatioF()
        image = QPixmap(size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        self._svg.render(painter)
        painter.end()
        return image

    def paintEvent(self, event: Any) -> None:
        source = self._source()
        if source is None or source.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(self._opacity)
        box = QSize(max(1, self.width() - 16), max(1, self.height() - 16))
        scaled = source.size().scaled(box, Qt.AspectRatioMode.KeepAspectRatio)
        scaled = QSize(max(1, int(scaled.width() * self._scale)),
                       max(1, int(scaled.height() * self._scale)))
        offset = int(math.sin(self._float * math.tau) * 3)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2 + offset
        painter.drawPixmap(x, y, scaled.width(), scaled.height(), source)

    def mouseDoubleClickEvent(self, event: Any) -> None:
        self.activated.emit()
        super().mouseDoubleClickEvent(event)

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if self._movie is not None:
            self._movie.start()
        if not os.environ.get("LC_DISABLE_IDLE_ANIMATION"):
            self._animation.start()

    def hideEvent(self, event: Any) -> None:
        self._stop_movie()
        self._animation.stop()
        super().hideEvent(event)
