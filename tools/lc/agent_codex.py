"""Codex app-server transport for the unified companion conversation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .agent_capabilities import AgentCapabilities
from .agent_models import AgentModelSelection
from .images import ImageAttachment
from .resident import ResidentAgent


class CodexSession:
    """One persistent Codex thread, owned by a conversation controller."""

    def __init__(
        self,
        port: int,
        *,
        workspace: Path | None = None,
        data_dir: Path | None = None,
        project_id: str | None = None,
        design_file: Path | None = None,
        model: str | None = None,
        effort: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        capabilities: AgentCapabilities | None = None,
    ) -> None:
        self.agent = ResidentAgent(
            port=port,
            model=model,
            effort=effort,
            provider=None,
            on_event=on_event,
            workspace=workspace,
            data_dir=data_dir,
            project_id=project_id,
            design_file=design_file,
            native=True,
            capabilities=capabilities,
        )
        self._configured_effort = self.agent.effort

    @property
    def thread_id(self) -> str:
        return self.agent.thread_id

    @property
    def models(self) -> list[dict[str, Any]]:
        return self.agent.models

    @property
    def model(self) -> str:
        return self.agent.model

    def model_state(self) -> dict[str, Any]:
        return {"model": self.model, "resolved_model": self.model,
                "effort": self.agent.effort, "models": self.models}

    def configure_model(self, selection: AgentModelSelection) -> None:
        metadata = selection.validate(self.models)
        # Null inherits the previous turn override; send the native default explicitly.
        effort = selection.effort
        if effort is None:
            native = getattr(self, "_configured_effort", None)
            modes = [item["reasoningEffort"]
                     for item in metadata.get("supportedReasoningEfforts", [])]
            effort = native if native in modes else metadata.get("defaultReasoningEffort")
        if effort is not None:
            AgentModelSelection(selection.model, effort).validate(self.models)
        self.agent.model, self.agent.effort = selection.model, effort

    @property
    def turn_metrics(self) -> dict[str, Any]:
        return self.agent.turn_metrics

    def respond(self, request_id: str, answer: dict[str, Any]) -> None:
        self.agent.respond(request_id, answer)

    def turn(self, prompt: str, submitted_at: float | None = None, *,
             images: tuple[ImageAttachment, ...] = ()) -> dict[str, Any]:
        return self.agent.turn(prompt, submitted_at=submitted_at, images=images)

    def interrupt(self) -> None:
        self.agent.interrupt()

    def close(self) -> None:
        self.agent.close()
