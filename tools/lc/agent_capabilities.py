"""Per-companion loading policy; never rewrite the user's native Agent configuration."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .settings_io import base_directory, load_versioned, save_atomic


@dataclass(frozen=True)
class AgentCapabilities:
    plugins: bool = False
    skills: bool = False
    workflows: bool = False
    mcp: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> AgentCapabilities:
        data = load_versioned(path or base_directory() / "agent-capabilities.json") or {}
        return cls(**{key: data.get(key) is True for key in asdict(cls())})

    def save(self, path: Path | None = None) -> None:
        save_atomic(path or base_directory() / "agent-capabilities.json",
                    {"version": 1, **asdict(self)})

    def codex_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "web_search": "live", "features.view_image": True,
            "features.standalone_web_search": True,
            "features.plugins": self.plugins, "features.remote_plugin": self.plugins,
            "features.skip_host_skill_discovery": not self.skills,
            "features.skill_search": self.skills,
            "skills.include_instructions": self.skills, "skills.bundled.enabled": self.skills,
            "features.hooks": self.workflows,
        }
        if not self.plugins:
            config["plugins._default.enabled"] = False
        if not self.workflows:
            config.update({"project_doc_max_bytes": 0, "features.memories": False})
        if not self.mcp:
            config.update({"features.apps": False, "features.skill_mcp_dependency_install": False,
                           "features.plugin_discovery_prefetch": False})
        return config

    def codex_session_config(self, request: Callable[[str, dict], dict],
                             workspace: Path, core_server: dict[str, Any]) -> dict[str, Any]:
        """Resolve actual inherited names before any thread starts its MCP servers."""
        config: dict[str, Any] = {}
        if not self.mcp:
            config["mcp_servers"] = {"levelcontrol": core_server}
        if not self.skills or not self.mcp:
            effective = request("config/read", {"cwd": str(workspace), "includeLayers": False})[
                "config"]
            if not self.mcp:
                config["mcp_servers"].update({name: {"enabled": False}
                                               for name in effective.get("mcp_servers", {})
                                               if name != "levelcontrol"})
            if not self.skills:
                listing = request("skills/list", {"cwds": [str(workspace)], "forceReload": True})
                rules = list(effective.get("skills", {}).get("config", []))
                for directory in listing["data"]:
                    if directory.get("errors"):
                        raise RuntimeError("技能清单读取失败，不能确认关闭状态；"
                                           "请查看原生 Agent 配置。")
                    rules.extend({"path": skill["path"], "enabled": False}
                                 for skill in directory["skills"])
                config["skills.config"] = rules
        if self.plugins and not self.mcp:
            config["plugins"] = _without_nulls(effective.get("plugins", {}))
            listing = request("plugin/list", {"cwds": [str(workspace)], "forceRefetch": False})
            if listing.get("marketplaceLoadErrors"):
                raise RuntimeError("插件清单读取失败，不能确认插件 MCP 已关闭。")
            for marketplace in listing["marketplaces"]:
                for plugin in marketplace["plugins"]:
                    if not plugin["installed"] or not plugin["enabled"]:
                        continue
                    details = request("plugin/read", {
                        "pluginName": plugin["name"], "marketplacePath": marketplace.get("path"),
                        "remoteMarketplaceName": marketplace["name"]
                        if not marketplace.get("path") else None})["plugin"]
                    servers = config["plugins"].setdefault(plugin["id"], {}).setdefault(
                        "mcp_servers", {})
                    for server in details["mcpServers"]:
                        servers.setdefault(server, {})["enabled"] = False
        return config

    def claude_settings(self, executable: str, workspace: Path,
                        environment: dict[str, str]) -> dict[str, Any]:
        settings: dict[str, Any] = {"enableWorkflows": self.workflows}
        if not self.plugins:
            listed = subprocess.run(
                [executable, "plugin", "list", "--json"], cwd=workspace, env=environment,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                check=True)
            plugins = json.loads(listed.stdout)
            if not isinstance(plugins, list):
                raise ValueError("Claude 插件清单格式不兼容，不能确认关闭状态。")
            settings["enabledPlugins"] = {plugin["id"]: False for plugin in plugins}
        if not self.skills:
            settings["disableSkillShellExecution"] = True
            if not self.workflows:
                settings["disableBundledSkills"] = True
        if not self.workflows:
            settings.update(disableAllHooks=True, autoMemoryEnabled=False, claudeMdExcludes=["**"])
        if not self.mcp:
            settings["disableClaudeAiConnectors"] = True
        return settings

    def claude_arguments(self) -> list[str]:
        arguments = ["--tools", "default"]
        if not self.skills:
            arguments.append("--disable-slash-commands")
        if not self.mcp:
            arguments.append("--strict-mcp-config")
        return arguments


def _without_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_nulls(item) for item in value if item is not None]
    return value
