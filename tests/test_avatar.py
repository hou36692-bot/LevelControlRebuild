import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from lc.avatar import AvatarError, AvatarSettings, AvatarView, inspect_image
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication


def png(path):
    image = QImage(96, 96, QImage.Format.Format_ARGB32)
    image.fill(QColor("#3388cc"))
    assert image.save(str(path), "PNG")
    return path


def test_inspect_and_commit_roundtrip(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = png(tmp_path / "portrait.png")
    info = inspect_image(source)
    assert info["format"] == "png" and not info["animated"]
    settings = AvatarSettings(tmp_path / "avatar-settings.json", tmp_path / "avatars")
    config = settings.stage(source, "我的形象")
    assert config.width == 96 and config.height == 96
    settings.commit(config)
    assert settings.active == config.id
    resolved = settings.resolve(settings.active_config())
    assert resolved is not None and resolved.is_file()
    assert settings.path.exists()
    again = AvatarSettings(tmp_path / "avatar-settings.json", tmp_path / "avatars")
    assert again.active == config.id
    assert again.resolve(again.active_config()) == resolved
    again.delete_current()
    assert again.active == "default"
    assert not resolved.exists()
    app.processEvents()


def test_content_detection_rejects_misnamed_and_traversal(tmp_path):
    app = QApplication.instance() or QApplication([])
    disguised = tmp_path / "fake.png"
    disguised.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", "utf-8")
    with pytest.raises(AvatarError):
        inspect_image(disguised)
    settings = AvatarSettings(tmp_path / "avatar-settings.json", tmp_path / "avatars")
    settings.path.write_text(json.dumps({
        "version": 1, "active": "x",
        "avatars": [{"id": "x", "name": "x", "file": "../outside.png"}],
    }), "utf-8")
    loaded = AvatarSettings(settings.path, tmp_path / "avatars")
    assert loaded.resolve(loaded.active_config()) is None
    app.processEvents()


def test_corrupt_settings_and_default_view(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "avatar-settings.json"
    path.write_text("{not json", "utf-8")
    settings = AvatarSettings(path, tmp_path / "avatars")
    assert settings.active == "default"
    assert "读取失败" in settings.error
    view = AvatarView()
    view.resize(200, 260)
    view.apply(None, None)
    app.processEvents()
    assert not view.grab().isNull()
    view.close()
    app.processEvents()
