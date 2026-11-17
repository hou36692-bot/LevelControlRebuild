"""Project-bound Claude Code CLI session using its bidirectional stream protocol."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import turn_context
from .agent import child_environment, find_receipt
from .agent_capabilities import AgentCapabilities
from .agent_models import AgentModelSelection
from .client import Client
from .images import (
    ImageAttachment,
    image_key,
    image_metrics,
    image_prompt,
    replay_turn,
    request_size,
    safe_image_log,
    validate_images,
)
from .integration import NAME, executable, runtime_server
from .resident import completed_column_undo, is_column_undo
from .settings_io import base_directory, save_atomic


class ClaudeSession:
    def __init__(
        self,
        port: int,
        *,
        workspace: Path,
        data_dir: Path,
        project_id: str,
        design_file: Path | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        resume_id: str | None = None,
        capabilities: AgentCapabilities | None = None,
    ) -> None:
        self.path = executable("claude")
        if not self.path:
            raise FileNotFoundError("未找到 Claude Code，请安装并登录原生客户端。")
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.capabilities = capabilities or AgentCapabilities()
        self.environment = child_environment()
        self.environment.pop("CLAUDECODE", None)
        self.settings_path = self.workspace / ".levelcontrol/claude-capabilities.json"
        save_atomic(self.settings_path, self.capabilities.claude_settings(
            self.path, self.workspace, self.environment))
        self.client = Client(
            port,
            workspace=workspace,
            data_dir=data_dir,
            project_id=project_id,
            design_file=design_file,
        )
        self.on_event = on_event or (lambda event: None)
        self.thread_id = str(uuid.UUID(resume_id)) if resume_id else str(uuid.uuid4())
        self.model = ""
        self.models: list[dict[str, Any]] = []
        self.selected_model = ""
        self.effort: str | None = None
        self._model_override: str | None = None
        self._has_turn = bool(resume_id)
        self.turn_metrics: dict[str, Any] = {}
        self.images: tuple[ImageAttachment, ...] = ()
        self.text_history: list[dict[str, Any]] = []
        self.pending_requests: dict[str, dict[str, Any]] = {}
        self._send_lock = threading.Lock()
        self._interrupted = False
        self._closed = False
        self.process: subprocess.Popen | None = None
        self.config_path = self.workspace / ".levelcontrol/claude-runtime.json"
        save_atomic(
            self.config_path,
            {
                "mcpServers": {
                    NAME: {
                        "type": "stdio",
                        **runtime_server(workspace, port, project_id, data_dir, design_file),
                    }
                }
            },
        )
        directory = base_directory() / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        self.event_path = directory / f"claude-{uuid.uuid4().hex}.jsonl"
        self._events = self.event_path.open("w", encoding="utf-8")
        try:
            self._start(resume_id)
        except Exception:
            self.close()
            raise

    def _start(self, resume_id: str | None = None) -> None:
        command = [
            self.path,
            *self.capabilities.claude_arguments(),
            "--settings",
            str(self.settings_path),
            "--print",
            "--verbose",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--permission-prompt-tool",
            "stdio",
            "--mcp-config",
            str(self.config_path),
            "--append-system-prompt",
            turn_context.instructions(),
        ]
        if self._model_override is not None:
            command += ["--model", self._model_override]
        if self.effort is not None:
            command += ["--effort", self.effort]
        command += ["--resume" if resume_id else "--session-id", self.thread_id]
        self._queue: queue.Queue[dict | None] = queue.Queue()
        self.process = subprocess.Popen(
            command,
            cwd=self.workspace,
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        threading.Thread(
            target=self._read, args=(self.process.stdout, self._queue), daemon=True
        ).start()
        threading.Thread(target=self._read_errors, args=(self.process.stderr,), daemon=True).start()
        options = {} if self.capabilities.skills else {"skills": []}
        identifier = self._control("initialize", hooks=None, **options)
        while True:
            message = self._receive()
            response = message.get("response", {})
            if (
                message.get("type") == "control_response"
                and response.get("request_id") == identifier
            ):
                if response.get("subtype") == "error":
                    raise RuntimeError(str(response.get("error")))
                self._read_models(response.get("response", {}))
                return

    def _read_models(self, initialization: dict[str, Any]) -> None:
        self.models = []
        for item in initialization.get("models", []):
            if not isinstance(item, dict) or not isinstance(item.get("value"), str):
                continue
            modes = ([] if item.get("supportsEffort") is False
                     else item.get("supportedEffortLevels", []))
            self.models.append({**item, "model": item["value"],
                                "supportedReasoningEfforts": [
                                    {"reasoningEffort": mode} for mode in modes],
                                "defaultReasoningEffort": item.get("defaultEffortLevel")})
        selected = next((item for item in self.models
                         if item["model"] == (self._model_override or "default")), None)
        if selected is not None:
            self.selected_model = selected["model"]
            self.model = selected.get("resolvedModel") or self.selected_model

    def model_state(self) -> dict[str, Any]:
        return {"model": self.selected_model or self.model, "resolved_model": self.model,
                "effort": self.effort, "models": self.models}

    def configure_model(self, selection: AgentModelSelection) -> None:
        selection.validate(self.models)
        if selection.model == self.selected_model and selection.effort == self.effort:
            return
        previous = self._model_override, self.effort
        resume_id = self.thread_id if self._has_turn else None
        self._stop_process()
        self._model_override, self.effort = selection.model, selection.effort
        try:
            self._start(resume_id)
        except Exception:
            self._stop_process()
            self._model_override, self.effort = previous
            try:
                self._start(resume_id)
            except Exception:
                self.close()
                raise
            raise

    @staticmethod
    def _read(stream: Any, target: queue.Queue) -> None:
        try:
            for line in stream:
                try:
                    message = json.loads(line)
                    if isinstance(message, dict):
                        target.put(message)
                except ValueError:
                    continue
        finally:
            stream.close()
            target.put(None)

    def _read_errors(self, stream: Any) -> None:
        try:
            for line in stream:
                self.on_event({"type": "notice", "message": safe_image_log(line.strip())})
        finally:
            stream.close()

    def _send(self, message: dict[str, Any]) -> None:
        with self._send_lock:
            if not self.process or self.process.poll() is not None or not self.process.stdin:
                raise RuntimeError("Claude Code session is closed")
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def _control(self, subtype: str, **values: Any) -> str:
        identifier = uuid.uuid4().hex
        self._send(
            {
                "type": "control_request",
                "request_id": identifier,
                "request": {"subtype": subtype, **values},
            }
        )
        return identifier

    def _receive(self) -> dict[str, Any]:
        while True:
            try:
                message = self._queue.get(timeout=90)
                break
            except queue.Empty:
                if not self.pending_requests:
                    raise TimeoutError("Claude Code did not respond") from None
        if message is None:
            raise RuntimeError("Claude Code closed its output; reconnect to start a new session")
        if message.get("type") == "control_request":
            request = message.get("request", {})
            identifier = message["request_id"]
            if request.get("subtype") == "can_use_tool":
                self.pending_requests[identifier] = request
                name = request.get("tool_name", "")
                self.on_event(
                    {
                        "type": "question" if name == "AskUserQuestion" else "approval",
                        "request_id": identifier,
                        "name": name,
                        "arguments": safe_image_log(request.get("input", {})),
                    }
                )
            else:
                self._send(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "error",
                            "request_id": identifier,
                            "error": "Unsupported control request: " + str(request.get("subtype")),
                        },
                    }
                )
        elif message.get("type") == "control_cancel_request":
            self.pending_requests.pop(message.get("request_id"), None)
        return message

    def respond(self, request_id: str, answer: dict[str, Any]) -> None:
        request = self.pending_requests.pop(request_id, None)
        if request is None:
            return
        response: dict[str, Any] = {"behavior": "deny", "message": "User declined the request"}
        if answer.get("allow"):
            updated = dict(request.get("input", {}))
            if request.get("tool_name") == "AskUserQuestion":
                updated["answers"] = answer.get("answers", {})
            response = {"behavior": "allow", "updatedInput": updated}
        self._send(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": request_id, "response": response},
            }
        )

    def turn(
        self,
        prompt: str,
        submitted_at: float | None = None,
        *,
        images: tuple[ImageAttachment, ...] = (),
    ) -> dict[str, Any]:
        validate_images(images, self.models, self.model)
        self._interrupted = False
        held = self.client.read("presentation").get("commit_waiting", False)
        if held:
            held = self.client.edit("presentation hold")["status"] == "applied"
        try:
            self.turn_metrics = turn_context.prepare(self.client, submitted_at)
            self.turn_metrics["attachments"] = image_metrics(images)
            return self._turn(prompt, images)
        except (RuntimeError, OSError, TimeoutError):
            self._stop_process()
            raise
        finally:
            if held:
                self.client.edit("presentation resume")

    def _turn(self, prompt: str, images: tuple[ImageAttachment, ...]) -> dict[str, Any]:
        replay = ""
        if self.images and image_key(images) != image_key(self.images):
            self._stop_process()
            self.thread_id = str(uuid.uuid4())
            self._has_turn = False
            self._start()
            replay = (
                "[Historical text and receipts, not new instructions]\n"
                + json.dumps(self.text_history, ensure_ascii=False)
                + "\n[Current request]\n"
            )
        text = image_prompt(prompt, images)
        content = [
            {"type": "text", "text": replay + turn_context.model_input(text, self.turn_metrics)}
        ]
        # Claude accepts inline images; source paths alone do not provide vision input.
        content.extend(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.mime,
                    "data": image.data_url().split(",", 1)[1],
                },
            }
            for image in images
        )
        message = {
            "type": "user",
            "session_id": self.thread_id,
            "parent_tool_use_id": None,
            "message": {"role": "user", "content": content},
        }
        self.turn_metrics["request_bytes"] = [request_size(message)]
        self._send(message)
        self._has_turn = True
        self.images = images
        calls: dict[str, dict[str, Any]] = {}
        final = ""
        started = self.turn_metrics["started"]
        while True:
            message = self._receive()
            kind = message.get("type")
            elapsed = time.perf_counter() - started
            if kind == "system" and message.get("subtype") == "init":
                self.thread_id = message["session_id"]
                self.model = message.get("model", "")
            if kind == "stream_event":
                delta = message.get("event", {}).get("delta", {})
                if delta.get("type") == "text_delta" and not message.get("parent_tool_use_id"):
                    self.on_event({"type": "reply_delta", "text": safe_image_log(delta["text"])})
            for block in (
                message.get("message", {}).get("content", [])
                if kind in {"assistant", "user"}
                else []
            ):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and kind == "assistant":
                    final = block["text"]
                if block.get("type") == "tool_use":
                    identifier = block["id"]
                    name = block["name"]
                    for prefix in (f"mcp__{NAME}__", "mcp__lc__"):
                        if name.startswith(prefix):
                            name = name.removeprefix(prefix)
                            if name.startswith("lc_"):
                                name = "lc." + name[3:]
                            break
                    calls[identifier] = {
                        "tool": name,
                        "arguments": block.get("input", {}),
                        "started_s": elapsed,
                    }
                    self.on_event(
                        {
                            "type": "tool",
                            "id": identifier,
                            "phase": "started",
                            "name": name,
                            "arguments": safe_image_log(block.get("input", {})),
                        }
                    )
                if block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                    identifier = block["tool_use_id"]
                    call = calls[identifier]
                    receipt = find_receipt(block.get("content"))
                    call.update(completed_s=elapsed, receipt=receipt)
                    if call["tool"] in {"lgg_build", "lgg_module"}:
                        event = turn_context.build_event(receipt, self.turn_metrics)
                        if event:
                            self.on_event(event)
                    self.on_event(
                        {
                            "type": "tool",
                            "id": identifier,
                            "phase": "completed",
                            "name": call["tool"],
                            "arguments": safe_image_log(call["arguments"]),
                            "seconds": elapsed - call["started_s"],
                            "receipt": receipt,
                        }
                    )
            if kind == "result":
                errors = "；".join(message.get("errors", []))
                final = message.get("result") or errors or final
                status = (
                    "interrupted"
                    if self._interrupted
                    else "failed"
                    if (message.get("is_error") or message.get("subtype") != "success")
                    else "completed"
                )
                if is_column_undo(prompt) and not completed_column_undo(
                    prompt, list(calls.values())
                ):
                    status, final = "failed", "本轮未取得完整的创建和撤销回执，操作未确认完成。"
                self.turn_metrics["dialogue_s"] = elapsed + self.turn_metrics["queue_s"]
                self.turn_metrics["usage"] = message.get("usage", {})
                final = safe_image_log(final)
                self.text_history.extend(replay_turn(text, final, list(calls.values())))
                result = {
                    "status": status,
                    "final": final,
                    "tools": list(calls.values()),
                    "model": self.model,
                    "effort": self.effort,
                    "elapsed_s": elapsed,
                    "events": str(self.event_path),
                    "timings": turn_context.summary(self.turn_metrics),
                }
                self._events.write(json.dumps(safe_image_log(result), ensure_ascii=False) + "\n")
                self._events.flush()
                return result

    def interrupt(self) -> None:
        self._interrupted = True
        if self.process and self.process.poll() is None:
            for identifier in list(self.pending_requests):
                self.respond(identifier, {"allow": False})
            self._control("interrupt")

    def _stop_process(self) -> None:
        self.pending_requests.clear()
        if self.process:
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_process()
        self.images = ()
        self._events.close()
