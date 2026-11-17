import itertools
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from lc import agent_capabilities
from lc.agent_capabilities import AgentCapabilities


def test_default_off_persistence_and_invalid_settings(tmp_path):
    path = tmp_path / "capabilities.json"
    assert not any(asdict(AgentCapabilities.load(path)).values())
    selected = AgentCapabilities(skills=True, mcp=True)
    selected.save(path)
    assert AgentCapabilities.load(path) == selected
    path.write_text('{"version":1,"plugins":"true","skills":true,"mcp":1}', "utf-8")
    assert AgentCapabilities.load(path) == AgentCapabilities(skills=True)
    path.write_text("broken", "utf-8")
    assert AgentCapabilities.load(path) == AgentCapabilities()
    assert path.read_text("utf-8") == "broken"


@pytest.mark.parametrize("values", list(itertools.product((False, True), repeat=4)))
def test_category_combinations_keep_native_tools_and_core_mcp(tmp_path, values, monkeypatch):
    policy = AgentCapabilities(*values)
    core = {"command": "python", "args": ["-m", "lc.mcp_server"]}
    called = []

    def request(method, params):
        called.append(method)
        return {
            "config/read": {"config": {
                "mcp_servers": {"foreign.with-dot": {"env": {"TOKEN": "do-not-copy"}}},
                "skills": {"config": [{"name": "already-disabled", "enabled": False}]},
                "plugins": {"fixture@local": {"enabled": True, "unset": None},
                            "user-disabled@local": {"enabled": False}},
            }},
            "skills/list": {"data": [{"skills": [{"path": str(tmp_path / "SKILL.md")}],
                                       "errors": []}]},
            "plugin/list": {"marketplaces": [{"name": "local", "path": str(tmp_path),
                "plugins": [{"id": "fixture@local", "name": "fixture",
                             "installed": True, "enabled": True}]}]},
            "plugin/read": {"plugin": {"mcpServers": ["plugin-server"]}},
        }[method]

    configuration = policy.codex_config()
    overrides = policy.codex_session_config(request, tmp_path, core)
    assert configuration["web_search"] == "live" and configuration["features.view_image"]
    assert configuration["features.plugins"] == policy.plugins
    if not policy.mcp:
        assert overrides["mcp_servers"]["levelcontrol"] == core
        assert overrides["mcp_servers"]["foreign.with-dot"] == {"enabled": False}
        assert "do-not-copy" not in json.dumps(overrides)
        assert not configuration["features.apps"]
    else:
        assert "mcp_servers" not in overrides
    if not policy.skills:
        assert overrides["skills.config"][-1] == {
            "path": str(tmp_path / "SKILL.md"), "enabled": False}
    else:
        assert "skills/list" not in called
    if policy.plugins and not policy.mcp:
        plugin = overrides["plugins"]["fixture@local"]
        assert plugin["enabled"] and "unset" not in plugin
        assert not plugin["mcp_servers"]["plugin-server"]["enabled"]
        assert not overrides["plugins"]["user-disabled@local"]["enabled"]
    else:
        assert "plugin/list" not in called
    monkeypatch.setattr(agent_capabilities.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout='[{"id":"fixture@local"}]'))
    settings = policy.claude_settings("claude", tmp_path, {})
    arguments = policy.claude_arguments()
    assert arguments[:2] == ["--tools", "default"]
    assert ("--strict-mcp-config" in arguments) is (not policy.mcp)
    assert ("--disable-slash-commands" in arguments) is (not policy.skills)
    assert "permissions" not in settings and "model" not in settings
    if not policy.plugins:
        assert settings["enabledPlugins"] == {"fixture@local": False}
    if not policy.workflows:
        assert settings["disableAllHooks"] and settings["claudeMdExcludes"] == ["**"]
        assert not settings["autoMemoryEnabled"]
    assert settings["enableWorkflows"] == policy.workflows


def test_failed_catalog_never_silently_enables_extensions(tmp_path, monkeypatch):
    def request(method, params):
        if method == "config/read":
            return {"config": {}}
        return {"data": [{"skills": [], "errors": [{"message": "unreadable skill"}]}]}

    with pytest.raises(RuntimeError, match="不能确认关闭状态"):
        AgentCapabilities().codex_session_config(request, tmp_path, {})
    monkeypatch.setattr(agent_capabilities.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout='{"unexpected":"shape"}'))
    with pytest.raises(ValueError, match="不能确认关闭状态"):
        AgentCapabilities().claude_settings("claude", tmp_path, {})


def test_switches_do_not_rewrite_native_configuration(tmp_path, monkeypatch):
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir()
    original = '{"enabledPlugins":{"fixture@local":true},"model":"native"}'
    settings.write_text(original, "utf-8")
    monkeypatch.setattr(agent_capabilities.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout='[{"id":"fixture@local"}]'))
    result = AgentCapabilities().claude_settings("claude", tmp_path, {})
    assert not result["enabledPlugins"]["fixture@local"]
    assert settings.read_text("utf-8") == original
