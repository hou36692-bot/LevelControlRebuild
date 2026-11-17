import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lc import avatar_panel
from lc.avatar import AvatarSettings
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication


def image(path):
    picture = QImage(80, 80, QImage.Format.Format_ARGB32)
    picture.fill(QColor("#225577"))
    assert picture.save(str(path), "PNG")
    return path


def test_avatar_panel_import_save_and_cancel(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = image(tmp_path / "portrait.png")
    settings = AvatarSettings(tmp_path / "avatar-settings.json", tmp_path / "avatars")
    panel = avatar_panel.AvatarPanel(settings)
    monkeypatch.setattr(avatar_panel.QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: (str(source), "Images"))
    panel.import_image()
    assert len(panel.staged) == 1
    assert settings.active_config() is None or settings.active == "default"
    panel.save()
    assert settings.active_config() is not None
    assert settings.resolve(settings.active_config()).is_file()

    stored = settings.active_config()
    panel2 = avatar_panel.AvatarPanel(AvatarSettings(tmp_path / "avatar-settings.json",
                                                     tmp_path / "avatars"))
    monkeypatch.setattr(avatar_panel.QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: (str(image(tmp_path / "second.png")), "Images"))
    panel2.import_image()
    assert len(panel2.staged) == 1
    panel2.reject()
    assert not panel2.staged
    assert stored is not None and stored.file
    app.processEvents()
    panel.close()
    panel2.close()
