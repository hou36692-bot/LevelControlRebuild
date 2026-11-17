import json

from lc.ui_settings import UiSettings, clamp_point


def test_ui_settings_roundtrip_and_theme(tmp_path):
    path = tmp_path / "ui.json"
    settings = UiSettings(path)
    assert settings.theme == "dark" and settings.model_bar_open
    settings.theme = "light"
    settings.harness = "claude"
    settings.collapsed = True
    settings.panel = (120, 240)
    settings.pill = (480, 360)
    settings.save()
    loaded = UiSettings(path)
    assert loaded.theme == "light" and loaded.collapsed
    assert loaded.harness == "claude"
    assert loaded.panel == (120, 240) and loaded.pill == (480, 360)


def test_clamp_and_corrupt_settings(tmp_path):
    rects = [(0, 0, 800, 600)]
    assert clamp_point((900, 700), (200, 100), rects) == (592, 492)
    assert clamp_point(None, (200, 100), rects) is None
    path = tmp_path / "ui.json"
    path.write_text(json.dumps({"version": 9}), "utf-8")
    settings = UiSettings(path)
    assert settings.theme == "dark" and "读取失败" in settings.error
    assert path.read_text("utf-8") == json.dumps({"version": 9})
