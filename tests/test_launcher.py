import importlib.util
import io
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest


@pytest.mark.parametrize("already_running", [True, False])
@pytest.mark.parametrize("protocol", [2, 3])
def test_client_embeds_ui_and_only_launches_game_when_needed(
    tmp_path, monkeypatch, already_running, protocol
):
    from lc import desktop

    source = Path(__file__).resolve().parents[1] / "scripts/launch_bundle.py"
    spec = importlib.util.spec_from_file_location("launcher", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = tmp_path / "bundle"
    executable = bundle / "Game/Windows/ProjectLC.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    if protocol == 3:
        (bundle / "build-info.json").write_text(json.dumps({
            "protocol": 3, "port": 18848, "resolution": [1920, 1080]}))
    monkeypatch.setattr(module, "__file__", str(bundle / "launch.py"))
    monkeypatch.setattr(module, "launch_lock", nullcontext)
    calls, ui = [], []

    class Opener:
        attempts = 0

        def open(self, *args, **kwargs):
            self.attempts += 1
            return io.BytesIO(json.dumps({"protocol": protocol, "ok": already_running
                                         or self.attempts > 1}).encode())

    monkeypatch.setattr(module, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(module.subprocess, "Popen", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(desktop, "main", lambda: ui.append(os.getpid()))
    monkeypatch.setenv("LC_PORT", os.environ.get("LC_PORT", "18844"))
    monkeypatch.setenv("PYTHONHOME", os.environ.get("PYTHONHOME", ""))
    monkeypatch.setenv("PYTHONPATH", os.environ.get("PYTHONPATH", ""))
    monkeypatch.setattr(sys, "path", list(sys.path))
    module.main()
    assert ui == [os.getpid()]
    assert len(calls) == (0 if already_running else 1)
    if calls:
        assert calls[0][0] == str(executable)
        assert f"-LCPort={18848 if protocol == 3 else 18844}" in calls[0]
        assert any("entities.sqlite3" in argument for argument in calls[0])


@pytest.mark.parametrize("feature,message", [("fast_build", "快速构建"),
                                           ("short_presentation", "短演出")])
def test_fast_bundle_refuses_same_protocol_game_without_capability(
    tmp_path, monkeypatch, feature, message
):
    source = Path(__file__).resolve().parents[1] / "scripts/launch_bundle.py"
    spec = importlib.util.spec_from_file_location("fast_launcher", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "build-info.json").write_text(json.dumps({
        "protocol": 3, "port": 18859, "fast_build": True,
        "short_presentation": feature == "short_presentation"}))
    monkeypatch.setattr(module, "__file__", str(tmp_path / "launch.py"))
    monkeypatch.setattr(module, "launch_lock", nullcontext)

    class Opener:
        def open(self, *args, **kwargs):
            return io.BytesIO(json.dumps({"protocol": 3, "ok": True,
                                          "fast_build": feature == "short_presentation"})
                              .encode())

    monkeypatch.setattr(module, "build_opener", lambda *args: Opener())
    with pytest.raises(RuntimeError, match="不支持" + message):
        module.main()
