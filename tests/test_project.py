import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from lc.project import (
    BuildInfo,
    ProjectError,
    ProjectSettings,
    ProjectTarget,
    ProtocolMismatch,
    design_for,
    enable_plugin,
    ensure_running,
    install_plugin,
    launch_spec,
    probe,
    sanitized_environment,
    store_for,
    target_id,
    validate_fields,
    validate_target,
)


def bundle(tmp_path, protocol=3, port=18848):
    root = tmp_path / "bundle"
    companion = root / "Companion"
    game = root / "Game/Windows/ProjectLC.exe"
    game.parent.mkdir(parents=True)
    game.write_bytes(b"MZ")
    companion.mkdir()
    return BuildInfo(root, companion, game, root / "build-info.json",
                     port, protocol, 1920, 1080)


def test_target_fields_and_ids(tmp_path):
    target = ProjectTarget("uproject:abc", "uproject", "My Level",
                           str(tmp_path / "My.uproject"), 18849, 3)
    validate_fields(target)
    assert target_id("uproject", str(tmp_path / "My.uproject"), 18849).startswith("uproject:")
    with pytest.raises(ProjectError):
        validate_fields(ProjectTarget("x", "bad", "x"))
    with pytest.raises(ProjectError):
        validate_fields(ProjectTarget("x", "attach", "x", port=80))


def test_settings_seed_and_store_paths(tmp_path):
    info = bundle(tmp_path)
    settings = ProjectSettings(tmp_path / "projects.json", info)
    assert settings.active == "bundled"
    target = settings.active_target()
    assert target is not None and target.port == info.port
    assert store_for(target, info) == info.companion / "project/Saved/LevelControl/entities.sqlite3"
    assert design_for(target, info).name == f"design-{info.port}.json"
    settings.save()
    assert (tmp_path / "projects.json").exists()


def test_launch_spec_for_executable_and_sanitized_env(tmp_path, monkeypatch):
    info = bundle(tmp_path)
    executable = tmp_path / "MyGame.exe"
    executable.write_bytes(b"MZ")
    target = ProjectTarget("exe:abc", "exe", "MyGame", str(executable), 18860, 3)
    status = validate_target(target, info)
    assert status.ok, status.errors
    spec = launch_spec(target, info)
    assert spec.command[0] == str(executable)
    assert "-LCPort=18860" in spec.command
    assert any(arg.startswith("-LCStore=") for arg in spec.command)
    assert spec.design_file == spec.store.parent / "design.json"
    monkeypatch.setenv("PYTHONHOME", "bad")
    monkeypatch.setenv("PYTHONPATH", "bad")
    monkeypatch.setenv("LC_PORT", "18844")
    env = sanitized_environment()
    assert "PYTHONHOME" not in env and "PYTHONPATH" not in env
    assert "LC_PORT" not in env


def test_uproject_plugin_validation_and_launch(tmp_path):
    info = bundle(tmp_path)
    project = tmp_path / "My.uproject"
    plugin_dir = tmp_path / "Plugins/LevelControlRuntime"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "LevelControlRuntime.uplugin").write_text(json.dumps({
        "Modules": [{"Name": "LevelControlRuntime", "Type": "Runtime"}],
        "Plugins": [{"Name": "SQLiteCore", "Enabled": True}],
    }), "utf-8")
    project.write_text(json.dumps({
        "EngineAssociation": "5.8", "Plugins": [{"Name": "LevelControlRuntime",
                                                   "Enabled": True}]}), "utf-8")
    editor = tmp_path / "UE/Engine/Binaries/Win64/UnrealEditor.exe"
    editor.parent.mkdir(parents=True)
    editor.write_bytes(b"MZ")
    target = ProjectTarget("uproject:abc", "uproject", "My", str(project), 18862, 3,
                           engine=str(editor))
    status = validate_target(target, info)
    assert status.ok, status.errors
    spec = launch_spec(target, info)
    assert spec.command[0] == str(editor)
    assert spec.command[1] == str(project)
    assert "-nosplash" in spec.command and "-DisablePython" in spec.command


def test_plugin_install_and_enable(tmp_path):
    bundle(tmp_path)
    project = tmp_path / "My.uproject"
    project.write_text("{}", "utf-8")
    source = tmp_path / "source"
    source.mkdir()
    (source / "LevelControlRuntime.uplugin").write_text("{}", "utf-8")
    (source / "Source").mkdir()
    (source / "Source/File.cpp").write_text("", "utf-8")
    (source / "Intermediate").mkdir()
    (source / "Intermediate/old.obj").write_text("", "utf-8")
    destination = install_plugin(project, source)
    assert (destination / "Source/File.cpp").exists()
    assert not (destination / "Intermediate").exists()
    with pytest.raises(ProjectError):
        install_plugin(project, source)
    enable_plugin(project)
    data = json.loads(project.read_text("utf-8"))
    assert any(item["Name"] == "LevelControlRuntime" and item["Enabled"]
               for item in data["Plugins"])
    assert project.with_suffix(".uproject.bak").exists()


class Health(BaseHTTPRequestHandler):
    protocol_value = 3
    port_value = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        raw = json.dumps({"ok": True, "protocol": self.protocol_value,
                          "port": self.port_value, "world": "LC_Empty"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_probe_and_attach(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    Health.port_value = server.server_port
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        assert probe(server.server_port, 3)["world"] == "LC_Empty"
        with pytest.raises(ProtocolMismatch):
            probe(server.server_port, 2)
        info = bundle(tmp_path)
        attach = ProjectTarget(f"attach:{server.server_port}", "attach", "existing",
                               port=server.server_port, protocol=3)
        result = ensure_running(attach, info, timeout=1)
        assert result.status == "attached"
    finally:
        server.shutdown()
        server.server_close()


def test_attach_without_game_fails(tmp_path):
    info = bundle(tmp_path)
    target = ProjectTarget("attach:18999", "attach", "none", port=18999, protocol=3)
    with pytest.raises(ProjectError):
        ensure_running(target, info, timeout=0.5)


def test_project_panel_add_and_activate(tmp_path, monkeypatch):
    from lc import project_panel
    from lc.project import ProjectSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    info = bundle(tmp_path)
    executable = tmp_path / "Game.exe"
    executable.write_bytes(b"MZ")
    settings = ProjectSettings(tmp_path / "projects.json", info)
    panel = project_panel.ProjectPanel(settings)
    monkeypatch.setattr(project_panel.QFileDialog, "getOpenFileName",
                        lambda *args, **kwargs: (str(executable), "exe"))
    panel.add_executable()
    assert len(settings.targets) == 2
    panel.validate()
    panel.activate(True)
    assert settings.active_target().kind == "exe"
    assert panel.action_target is not None and panel.action_launch
    assert (tmp_path / "projects.json").exists()
    panel.close()
    app.processEvents()


def test_design_file_is_forwarded(tmp_path):
    from lc.agent import arguments
    from lc.client import Client
    from lgg.service import DesignService

    design = tmp_path / "design.json"
    args = arguments(18860, True, design_file=design)
    assert any("LC_DESIGN_FILE" in value for value in args)
    client = Client(18860, design_file=design)
    assert DesignService(client).path == design
def test_unified_store_root_preserves_existing_legacy_world(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("LC_USER_DATA_ROOT", str(tmp_path / "LevelControl"))
    target = ProjectTarget("sample", "exe", "Sample")
    bundle = SimpleNamespace()
    destination = tmp_path / "LevelControl/projects/sample/entities.sqlite3"
    assert store_for(target, bundle) == destination
    legacy = tmp_path / "LevelControl3/projects/sample/entities.sqlite3"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"existing world")
    assert store_for(target, bundle) == legacy
    destination.parent.mkdir(parents=True)
    destination.touch()
    assert store_for(target, bundle) == destination
