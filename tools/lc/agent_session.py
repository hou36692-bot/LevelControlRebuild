"""Shared 3.1 chat contract; no Harness implementation or conversation is created here."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

EventKind = Literal[
    "text_delta", "tool_started", "tool_completed", "approval", "question",
    "artifact", "status", "error",
]


@dataclass(frozen=True)
class Attachment:
    path: Path
    mime_type: str


@dataclass(frozen=True)
class UserMessage:
    text: str
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class SessionInfo:
    id: str
    supports_images: bool
    supports_approvals: bool


@dataclass(frozen=True)
class AgentEvent:
    kind: EventKind
    session_id: str
    turn_id: str | None = None
    request_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class AgentSession(Protocol):
    async def open(self, workspace: Path, resume_id: str | None = None) -> SessionInfo:
        """Resume only this Harness's own session; never import private reasoning state."""
        ...

    def send(self, message: UserMessage) -> AsyncIterator[AgentEvent]:
        """Allow one active turn; report tools, questions and approvals through shared events."""
        ...

    async def interrupt(self) -> None:
        """Stop inference. Game cancellation and undo remain explicit LC operations."""
        ...

    async def respond(self, request_id: str, answer: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...
