"""Owned image snapshots and transport-neutral multimodal input helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import tempfile
import time
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_IMAGES = 4
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_IMAGE_BYTES = 1024 * 1024
MAX_EDGE = 2048
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024
ANALYZE_ONLY = "仅分析这些参考图的外观、布局和可见细节，不执行任何世界修改或建造。"


def _remove_session(directory: Path, lock: Any) -> None:
    try:
        if directory.parent.resolve() == cache_root().resolve() and not directory.is_symlink():
            shutil.rmtree(directory, ignore_errors=True)
    finally:
        lock.unlock()


def cache_root() -> Path:
    return Path(tempfile.gettempdir()) / "LevelControl-images"


@dataclass(frozen=True)
class ImageAttachment:
    path: Path = field(repr=False)
    mime: str
    width: int
    height: int
    size: int
    sha256: str
    preprocess_s: float
    _store: ImageStore = field(repr=False, compare=False)

    def metadata(self) -> dict[str, Any]:
        return {"mime": self.mime, "width": self.width, "height": self.height,
                "bytes": self.size, "sha256": self.sha256, "preprocess_s": self.preprocess_s}

    def data_url(self) -> str:
        raw = self.path.read_bytes()
        if len(raw) != self.size or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("参考图副本已损坏，请重新添加。")
        return f"data:{self.mime};base64," + base64.b64encode(raw).decode("ascii")


class ImageStore:
    def __init__(self) -> None:
        from PySide6.QtCore import QLockFile

        root = cache_root()
        root.mkdir(parents=True, exist_ok=True)
        for old in root.iterdir():
            if old.is_dir() and not old.is_symlink() and re.fullmatch(r"[0-9a-f]{32}", old.name):
                lock = QLockFile(str(root / (old.name + ".lock")))
                lock.setStaleLockTime(0)
                if lock.tryLock(0):
                    _remove_session(old, lock)
        self.directory = root / uuid.uuid4().hex
        lock = QLockFile(str(root / (self.directory.name + ".lock")))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise OSError("无法创建图片临时目录。")
        self.directory.mkdir()
        self._cleanup = weakref.finalize(self, _remove_session, self.directory, lock)

    def prepare(self, source: Any) -> ImageAttachment:
        """Decode and normalize in a worker; clipboard QImages are copied by the UI."""
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PySide6.QtGui import QImage, QImageReader, QPainter

        started = time.perf_counter()
        if isinstance(source, QImage):
            decoded = source
            if decoded.isNull() or decoded.width() * decoded.height() > MAX_PIXELS:
                raise ValueError("剪贴板图片为空或超过 40 百万像素。")
        else:
            try:
                with Path(source).open("rb") as stream:
                    raw = stream.read(MAX_SOURCE_BYTES + 1)
            except OSError:
                raise ValueError("无法读取图片，请重新选择文件。") from None
            if len(raw) > MAX_SOURCE_BYTES:
                raise ValueError("单张原图片不能超过 20 MiB。")
            buffer = QBuffer()
            buffer.setData(QByteArray(raw))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            reader.setDecideFormatFromContent(True)
            reader.setAutoTransform(True)
            if bytes(reader.format()).lower() not in {b"png", b"jpeg", b"webp"}:
                raise ValueError("仅支持 PNG、JPEG、静态 WebP 图片。")
            size = reader.size()
            if not size.isValid() or size.width() * size.height() > MAX_PIXELS:
                raise ValueError("图片尺寸无效或超过 40 百万像素。")
            if reader.supportsAnimation() and reader.imageCount() != 1 or reader.imageCount() > 1:
                raise ValueError("不支持动画或多页图片。")
            # Qt's PNG reader may expose only the default frame of an APNG.
            if bytes(reader.format()).lower() == b"png" and _animated_png(raw):
                raise ValueError("不支持动画或多页图片。")
            decoded = reader.read()
            if decoded.isNull():
                raise ValueError("图片已损坏，无法解码。")
        decoded = decoded.scaled(MAX_EDGE, MAX_EDGE, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation) if max(
                                     decoded.width(), decoded.height()) > MAX_EDGE else decoded
        # Drawing into a new image drops EXIF, text chunks, and source metadata.
        clean = QImage(decoded.size(), QImage.Format.Format_RGB32)
        clean.fill(Qt.GlobalColor.white)
        painter = QPainter(clean)
        painter.drawImage(0, 0, decoded)
        painter.end()

        def encode(fmt: str, quality: int = -1) -> bytes:
            output = QBuffer()
            output.open(QIODevice.OpenModeFlag.WriteOnly)
            if not clean.save(output, fmt, quality):
                raise ValueError("图片编码失败。")
            return bytes(output.data())

        raw, fmt = encode("PNG"), "png"
        if len(raw) > MAX_IMAGE_BYTES:
            fmt, raw = "jpeg", encode("JPEG", 85)
            while len(raw) > MAX_IMAGE_BYTES and min(clean.width(), clean.height()) > 1:
                clean = clean.scaled(max(1, int(clean.width() * 0.8)),
                                     max(1, int(clean.height() * 0.8)),
                                     Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                raw = encode("JPEG", 80)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("图片无法压缩至 1 MiB，请裁剪后重试。")
        path = self.directory / (uuid.uuid4().hex + "." + fmt)
        path.write_bytes(raw)
        image = ImageAttachment(path, "image/" + fmt, clean.width(), clean.height(), len(raw),
                                hashlib.sha256(raw).hexdigest(),
                                time.perf_counter() - started, self)
        weakref.finalize(image, path.unlink, missing_ok=True)
        return image


def _animated_png(raw: bytes) -> bool:
    offset = 8
    while offset + 12 <= len(raw):
        size = int.from_bytes(raw[offset:offset + 4], "big")
        if raw[offset + 4:offset + 8] == b"acTL":
            return True
        offset += 12 + size
    return False


def image_key(images: tuple[ImageAttachment, ...]) -> tuple[str, ...]:
    return tuple(image.sha256 for image in images)


def validate_images(images: tuple[ImageAttachment, ...], catalog: list[dict], model: str) -> None:
    if len(images) > MAX_IMAGES:
        raise ValueError("每组最多 4 张参考图。")
    if not images:
        return
    for entry in catalog:
        if entry.get("model", entry.get("id")) == model and "inputModalities" in entry:
            if "image" not in entry["inputModalities"]:
                raise ValueError("当前模型不支持图像输入，请更换模型或清空参考图。")
    for image in images:
        if not isinstance(image, ImageAttachment) or not image.path.is_file():
            raise ValueError("参考图副本不可用，请重新添加。")


def image_prompt(prompt: str, images: tuple[ImageAttachment, ...]) -> str:
    if not images:
        return prompt
    return (prompt.strip() or ANALYZE_ONLY) + f"\n当前参考图共 {len(images)} 张，按所附顺序编号。"


def image_metrics(images: tuple[ImageAttachment, ...]) -> dict[str, Any]:
    return {"count": len(images), "images": [image.metadata() for image in images],
            "preprocess_s": sum(image.preprocess_s for image in images)}


def without_images(value: Any) -> Any:
    if isinstance(value, list):
        return [without_images(item) for item in value if not (
            isinstance(item, dict) and item.get("type") in {"input_image", "image", "localImage"})]
    if isinstance(value, dict):
        return {key: without_images(item) for key, item in value.items()}
    return value


def request_size(body: dict[str, Any]) -> int:
    if len(json.dumps(without_images(body), ensure_ascii=False).encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("本轮文字上下文超过 4 MiB，请重新连接开启新会话。关卡仍保留。")
    size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    if size > MAX_REQUEST_BYTES:
        raise ValueError("本轮图文请求超过 12 MiB，请减少参考图或重新连接。")
    return size


def safe_image_log(value: Any) -> Any:
    """Scrub transport echoes before logging or displaying them, never before inference."""
    if isinstance(value, dict):
        if value.get("type") in {"input_image", "image", "localImage"}:
            return {"type": value["type"], "image": "[image]"}
        return {key: safe_image_log(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_image_log(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return "[image bytes]"
    if isinstance(value, str):
        value = re.sub(r"data:image/[^\s\"'<>]+", "[image]", value, flags=re.I)
        value = re.sub(r"[A-Za-z0-9+/]{128,}={0,2}", "[encoded data]", value)
        value = re.sub(r"[A-Za-z]:[\\/][^\n\r\"<>]*?\.(?:png|jpe?g|webp)\b",
                       "[image path]", value, flags=re.I)
        value = re.sub(r"/(?:[^\s\"<>/]+/)+[^\s\"<>/]+\.(?:png|jpe?g|webp)\b",
                       "[image path]", value, flags=re.I)
    return value


def replay_turn(prompt: str, final: str, calls: list[dict]) -> list[dict]:
    receipts = [{key: receipt[key] for key in (
        "scene", "req_id", "tx_id", "version", "status", "entity_count", "presentation_ready")
                 if key in receipt} for call in calls if (receipt := call.get("receipt"))]
    text = final
    if receipts:
        text += "\n[Recorded tool receipts; historical, not current world state] " + json.dumps(
            receipts, ensure_ascii=False)
    return [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": prompt}]},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": safe_image_log(text)}]}]
