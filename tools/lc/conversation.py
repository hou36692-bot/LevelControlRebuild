"""Project-bound conversation lifecycle for the unified companion."""

from __future__ import annotations

import hashlib
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lgg.build import BuildService

from . import turn_context
from .agent_capabilities import AgentCapabilities
from .agent_claude import ClaudeSession
from .agent_codex import CodexSession
from .agent_models import AgentModelSelection
from .client import Client
from .images import ImageAttachment, safe_image_log
from .project import ProjectSettings, ProjectTarget, design_for, store_for
from .settings_io import base_directory
from .speech import Speech


@dataclass(frozen=True)
class QueuedPrompt:
    text: str
    submitted_at: float
    images: tuple[ImageAttachment, ...] = ()


def workspace_for(target: ProjectTarget) -> Path:
    key = hashlib.sha256(target.id.encode("utf-8")).hexdigest()[:16]
    return base_directory() / "workspaces" / key


class ConversationController:
    """Own one project's speech, world polling, builds and native Agent session."""

    def __init__(self, target: ProjectTarget, settings: ProjectSettings,
                 emit: Callable[[dict[str, Any]], None], *, harness: str = "codex",
                 capabilities: AgentCapabilities | None = None) -> None:
        if harness not in ("codex", "claude"):
            raise ValueError("Unknown Agent: " + harness)
        self.harness = harness
        self.capabilities = capabilities or AgentCapabilities.load()
        self.target = target
        self.settings = settings
        self.emit = emit
        self.port = target.port
        self.workspace = workspace_for(target)
        store = store_for(target, settings.bundle)
        self.data_dir = store.parent if store else self.workspace / ".levelcontrol/data"
        self.design_file = design_for(target, settings.bundle)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.queue: queue.Queue[
            QueuedPrompt | AgentModelSelection | tuple[str, bool] | None] = queue.Queue()
        self.agent: CodexSession | ClaudeSession | None = None
        self.speech: Speech | None = None
        self.stopping = threading.Event()
        self.recording = False
        self.cancel_requested = False
        self.pending_builds: dict[str, tuple[dict, dict]] = {}
        self.pending_lock = threading.Lock()
        self.voice_lease = "voice-" + uuid.uuid4().hex
        self.thread = threading.Thread(target=self.run, name="game-dialogue", daemon=True)
        self.thread.start()
        threading.Thread(target=self.load_speech, name="load-speech", daemon=True).start()
        threading.Thread(target=self.observe, name="world-status", daemon=True).start()

    def emit_event(self, **event: Any) -> None:
        self.emit(event)

    def on_agent_event(self, event: dict[str, Any]) -> None:
        if event["type"] == "build_pending" and self.agent:
            receipt = event["receipt"]
            with self.pending_lock:
                self.pending_builds[receipt["req_id"]] = (
                    receipt, self.agent.turn_metrics)
        self.emit(event)

    def observe_builds(self, client: Client) -> None:
        with self.pending_lock:
            pending = list(self.pending_builds.items())
        for request_id, (_, metrics) in pending:
            receipt = client.receipt(request_id)
            if receipt.get("status") == "scheduled":
                continue
            with self.pending_lock:
                self.pending_builds.pop(request_id, None)
            if receipt.get("status") == "applied":
                result = BuildService(client).finalize_receipt(request_id, receipt)
                ready = turn_context.build_event(result, metrics)
                if ready:
                    self.emit(ready)
            else:
                self.emit_event(type="notice", message="构建未提交：" + "；".join(
                    receipt.get("warnings") or [receipt.get("status", "unknown")]))

    def load_speech(self) -> None:
        try:
            self.speech = Speech()
            self.emit_event(type="speech_ready")
        except Exception as error:  # speech is optional
            self.emit_event(type="speech_error", message=str(error))

    def client(self, timeout: float = 30) -> Client:
        return Client(self.port, timeout=timeout, data_dir=self.data_dir,
                      workspace=self.workspace, project_id=self.target.id,
                      design_file=self.design_file)

    def open_session(self) -> None:
        self.emit_event(type="connecting")
        try:
            session = ClaudeSession if self.harness == "claude" else CodexSession
            self.agent = session(
                self.port,
                workspace=self.workspace,
                data_dir=self.data_dir,
                project_id=self.target.id,
                design_file=self.design_file,
                on_event=self.on_agent_event,
                capabilities=self.capabilities,
            )
            saved = AgentModelSelection.load(self.harness)
            if saved is not None:
                try:
                    self.agent.configure_model(saved)
                except ValueError as error:
                    self.emit_event(type="notice", message="已沿用 Agent 配置：" + str(error))
            self.emit_event(type="ready", harness=self.harness,
                            **self.agent.model_state(), new_session=True)
        except Exception as error:
            if getattr(self, "agent", None) is not None:
                self.agent.close()
            self.agent = None
            self.emit_event(type="connection_error", message=str(error))

    def configure_model(self, selection: AgentModelSelection) -> None:
        self.emit_event(type="model_configuring")
        if self.agent is None:
            self.emit_event(type="connection_error", message="对话未连接，请重新连接。")
            return
        try:
            self.agent.configure_model(selection)
        except Exception as error:
            if getattr(self.agent, "_closed", False):
                self.agent = None
                self.emit_event(type="connection_error", message=str(error))
                return
            self.emit_event(type="model_error", harness=self.harness,
                            **self.agent.model_state(), message=str(error))
            return
        self.emit_event(type="model_changed", harness=self.harness, **self.agent.model_state())
        try:
            selection.save(self.harness)
        except OSError as error:
            self.emit_event(type="notice", message="本轮选择已生效，但保存失败：" + str(error))

    def run(self) -> None:
        self.open_session()
        try:
            while not self.stopping.is_set():
                item = self.queue.get()
                if item is None:
                    break
                if isinstance(item, AgentModelSelection):
                    self.configure_model(item)
                    continue
                if isinstance(item, tuple) and item[0] == "reconnect":
                    if self.agent:
                        self.agent.close()
                        self.agent = None
                    self.open_session()
                    continue
                if self.agent is None:
                    self.emit_event(type="error", message="对话未连接，请重新连接。",
                                    submission=item)
                    continue
                submission = item
                if isinstance(item, QueuedPrompt):
                    prompt, submitted_at, images = item.text, item.submitted_at, item.images
                else:
                    prompt, submitted_at, images = item, None, ()
                self.emit_event(type="turn_started", prompt=prompt)
                self.cancel_requested = False
                try:
                    result = self.agent.turn(prompt, submitted_at=submitted_at,
                                             images=images)
                    if self.cancel_requested:
                        self.control("presentation cancel")
                        self.control("action stop")
                    if not self.recording and self.queue.empty():
                        self.control(f"presentation resume --lease-id {self.voice_lease}")
                    self.emit_event(type="turn_finished", result=result,
                                    submission=submission)
                except Exception as error:
                    self.emit_event(type="error", message=safe_image_log(error),
                                    submission=submission)
                    self.control(f"presentation resume --lease-id {self.voice_lease}")
                    if isinstance(error, (RuntimeError, OSError, TimeoutError)):
                        self.agent.close()
                        self.agent = None
                        self.emit_event(type="connection_error",
                                        message="本轮连接中断，请重新连接。未自动重发操作。")
        finally:
            if self.agent:
                self.agent.close()
                self.agent = None

    def send(self, prompt: str, images: tuple[ImageAttachment, ...] = ()) -> None:
        self.queue.put(QueuedPrompt(prompt, time.perf_counter(), images))

    def reconnect(self) -> None:
        self.queue.put(("reconnect", True))

    def select_model(self, model: str, effort: str | None) -> None:
        self.queue.put(AgentModelSelection(model, effort))

    def respond(self, request_id: str, answer: dict[str, Any]) -> None:
        if self.agent:
            self.agent.respond(request_id, answer)

    def observe(self) -> None:
        client = self.client(timeout=2)
        while not self.stopping.wait(0.2):
            try:
                self.emit_event(type="world", state=client.read("presentation"))
                self.observe_builds(client)
            except Exception:
                self.emit_event(type="world_offline")

    def control(self, command: str) -> None:
        def send() -> None:
            try:
                result = self.client().edit(command)
                if result["status"] in {"failed", "rejected"}:
                    if (command == "action stop"
                            and "No controllable action is running"
                            in result.get("warnings", [])):
                        return
                    self.emit_event(type="notice",
                                    message="；".join(result.get("warnings", [])))
            except Exception as error:
                self.emit_event(type="notice", message=str(error))

        threading.Thread(target=send, daemon=True).start()

    def cancel(self) -> None:
        self.cancel_requested = True
        if self.agent:
            self.agent.interrupt()
        self.control("presentation cancel")
        self.control("action stop")

    def close(self) -> None:
        if self.stopping.is_set():
            return
        self.stopping.set()
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        self.queue.put(None)
        if self.speech:
            self.speech.stop()
        if self.agent:
            self.agent.interrupt()
        self.thread.join(timeout=4)
        if self.agent:
            self.agent.close()
