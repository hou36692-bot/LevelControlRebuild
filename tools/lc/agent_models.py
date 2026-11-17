"""Native Agent model choices, validated against the active backend's catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .settings_io import base_directory, load_versioned, save_atomic


@dataclass(frozen=True)
class AgentModelSelection:
    model: str
    effort: str | None = None

    def validate(self, models: list[dict[str, Any]]) -> dict[str, Any]:
        metadata = next((item for item in models if item.get("model") == self.model), None)
        if not isinstance(self.model, str) or not self.model or metadata is None:
            raise ValueError("所选模型不在当前 Agent 的模型列表中，请重新连接后选择。")
        modes = [item["reasoningEffort"]
                 for item in metadata.get("supportedReasoningEfforts", [])]
        if self.effort is not None and self.effort not in modes:
            raise ValueError("当前模型不支持所选思考强度，请选择该模型返回的档位。")
        return metadata

    @classmethod
    def load(cls, harness: str) -> AgentModelSelection | None:
        data = load_versioned(base_directory() / "agent-models.json") or {}
        agents = data.get("agents", {})
        entry = agents.get(harness) if isinstance(agents, dict) else None
        if not isinstance(entry, dict):
            return None
        model, effort = entry.get("model"), entry.get("effort")
        if not isinstance(model, str) or not model or not (
                effort is None or isinstance(effort, str)):
            return None
        return cls(model, effort)

    def save(self, harness: str) -> None:
        path = base_directory() / "agent-models.json"
        data = load_versioned(path) or {}
        agents = data.get("agents", {})
        if not isinstance(agents, dict):
            agents = {}
        agents[harness] = {"model": self.model, "effort": self.effort}
        save_atomic(path, {"version": 1, "agents": agents})
