import json
import tomllib

import pytest
from lc.integration import install, uninstall


def test_install_repeat_and_remove_preserve_user_settings(tmp_path):
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    original = 'model = "user-model"\n[features]\nplugins = true\n'
    config.write_text(original)
    claude = tmp_path / ".mcp.json"
    other = {"mcpServers": {"other": {"command": "custom", "args": []}}, "custom": 1}
    claude.write_text(json.dumps(other))
    agents = tmp_path / "AGENTS.md"
    agents.write_text("User project rules\n")
    first = install(tmp_path, 18871, "project-a")
    second = install(tmp_path, 18871, "project-a")
    assert first == second
    assert tomllib.loads(config.read_text())["model"] == "user-model"
    assert tomllib.loads(config.read_text())["features"] == {"plugins": True}
    assert first["server"]["env"]["LC_BIND_PORT"] == "18871"
    saved = tmp_path / ".levelcontrol/data/design.json"
    saved.parent.mkdir(parents=True)
    saved.write_text("user scene")
    uninstall(tmp_path)
    assert config.read_text() == original
    assert json.loads(claude.read_text()) == other
    assert agents.read_text() == "User project rules\n"
    assert saved.read_text() == "user scene"
    assert uninstall(tmp_path) == []


def test_install_does_not_overwrite_foreign_entry(tmp_path):
    config = tmp_path / ".mcp.json"
    original = '{"mcpServers":{"levelcontrol":{"command":"foreign"}}}'
    config.write_text(original)
    with pytest.raises(ValueError, match="preserved"):
        install(tmp_path, 18871, "project")
    assert config.read_text() == original
    assert not (tmp_path / ".codex/config.toml").exists()


def test_uninstall_refuses_changed_configuration(tmp_path):
    install(tmp_path, 18871, "project")
    config = tmp_path / ".mcp.json"
    data = json.loads(config.read_text())
    data["mcpServers"]["levelcontrol"]["command"] = "user-edited"
    config.write_text(json.dumps(data))
    before = (tmp_path / ".codex/config.toml").read_bytes()
    with pytest.raises(ValueError, match="untouched"):
        uninstall(tmp_path)
    assert (tmp_path / ".codex/config.toml").read_bytes() == before


def test_workspace_cannot_silently_switch_project(tmp_path):
    install(tmp_path, 18871, "project-a")
    with pytest.raises(ValueError, match="another"):
        install(tmp_path, 18872, "project-b")
