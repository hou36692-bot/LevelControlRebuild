import json
import sys

from lc.console import ControlWindow, client_for
from lc.project import BuildInfo, ProjectSettings, ProjectTarget


def test_console_uses_no_agent_and_overlay_is_optional(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    game = tmp_path / "Game.exe"
    game.write_bytes(b"MZ")
    bundle = BuildInfo(tmp_path, tmp_path / "Companion", game, tmp_path / "build-info.json")
    settings = ProjectSettings(tmp_path / "projects.json", bundle)
    app = QApplication.instance() or QApplication([])
    before = set(sys.modules)
    window = ControlWindow(settings)
    window.timer.stop()
    assert not {"lc.api_agent", "lc.resident", "lc.desktop"} & (set(sys.modules) - before)
    assert window.client is None
    window.toggle_overlay(True)
    assert window.overlay.isVisible()
    window.toggle_overlay(False)
    assert not window.overlay.isVisible()
    assert window.cancel_button is not None and window.undo_button is not None
    window.quit()
    window.deleteLater()
    app.processEvents()


def test_switching_target_creates_distinct_bound_clients(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    bundle = BuildInfo(tmp_path, tmp_path, tmp_path / "Game.exe", tmp_path / "build-info.json")
    settings = ProjectSettings(tmp_path / "projects.json", bundle)
    left = client_for(ProjectTarget("left", "attach", "Left", port=18871), settings)
    right = client_for(ProjectTarget("right", "attach", "Right", port=18872), settings)
    assert left.port == 18871 and right.port == 18872
    assert left.data_dir != right.data_dir and left.workspace != right.workspace


def test_native_bundle_starts_bubble_companion_without_model_or_game(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    from lc import desktop

    spec = importlib.util.spec_from_file_location(
        "native_launcher", Path(__file__).resolve().parents[1] / "scripts/launch_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "build-info.json").write_text(json.dumps({"native_harness": True}))
    monkeypatch.setattr(module, "__file__", str(tmp_path / "launch.py"))
    for key in ("PYTHONHOME", "PYTHONPATH", "LC_BUNDLE_ROOT", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    called = []
    monkeypatch.setattr(desktop, "main", lambda: called.append("desktop"))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: called.append("process"))
    module.main()
    assert called == ["desktop"]
