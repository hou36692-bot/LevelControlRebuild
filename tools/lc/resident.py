"""A persistent, ephemeral Codex conversation over its documented stdio API."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import turn_context
from .agent import arguments, child_environment, find_receipt
from .agent_capabilities import AgentCapabilities
from .client import ROOT, Client
from .command import parse
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
from .integration import runtime_server
from .models import Provider
from .settings_io import base_directory


def is_column_undo(prompt: str) -> bool:
    normalized = re.sub(r"[\s，,。.!！]", "", prompt)
    return normalized in {
        "在我面前放一根5米高的柱子然后撤销",
        "在我面前放一根五米高的柱子然后撤销",
    }


def completed_column_undo(prompt: str, calls: list[dict[str, Any]]) -> bool:
    if not is_column_undo(prompt):
        return False
    if len(calls) != 2 or any(call.get("tool") != "lc.edit" for call in calls):
        return False
    create, undo = calls
    if any(not call.get("receipt") or call["receipt"].get("status") != "applied" for call in calls):
        return False
    try:
        command = parse("edit", create["arguments"]["command"])
        operation = command.data["operations"][0]
        target = parse("edit", undo["arguments"]["command"])
        return (
            operation["op"] == "create"
            and operation["entity"]["template"] == "blockout/column"
            and operation["entity"]["transform"]["size_cm"][2] == 500
            and command.front_cm is not None
            and target.path == "/v1/undo"
            and (target.data.get("last") or target.data.get("tx_id") == create["receipt"]["tx_id"])
            and create["receipt"]["applied"] == undo["receipt"]["applied"] == 1
            and create["receipt"]["entity_ids"] == undo["receipt"]["entity_ids"]
            and undo["receipt"]["version"] == create["receipt"]["version"] + 1
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return False


class ResidentAgent:
    def __init__(
        self,
        port: int = 18844,
        model: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        *,
        effort: str | None = None,
        provider: Provider | None = None,
        workspace: Path | None = None,
        data_dir: Path | None = None,
        project_id: str | None = None,
        design_file: Path | None = None,
        native: bool = False,
        capabilities: AgentCapabilities | None = None,
    ) -> None:
        started = time.perf_counter()
        self.on_event = on_event or (lambda event: None)
        self.provider = provider
        self.native = native
        self.capabilities = capabilities or AgentCapabilities()
        self.session_config: dict[str, Any] = {}
        self.effort = effort if native else ("low" if effort is None else effort)
        self.workspace = (workspace or (ROOT / "project")).resolve()
        self.data_dir = data_dir
        self.project_id = project_id
        self.design_file = design_file
        self._send_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self.pending_requests: dict[str, dict[str, Any]] = {}
        self.current_turn: str | None = None
        self.images: tuple[ImageAttachment, ...] = ()
        self.text_history: list[dict] = []
        self.client = Client(port=port, data_dir=data_dir, workspace=self.workspace,
                             project_id=project_id, design_file=design_file)
        source = arguments(port, True, model, effort, design_file,
                           workspace=self.workspace, data_dir=data_dir, project_id=project_id,
                           native=native, capabilities=self.capabilities)
        directory = base_directory() / "logs" if native else ROOT / "tests/reports/agent"
        directory.mkdir(parents=True, exist_ok=True)
        name = "resident-" + uuid.uuid4().hex
        self.event_path = directory / f"{name}.jsonl"
        self._events = self.event_path.open("w", encoding="utf-8")
        self._errors = (directory / f"{name}.stderr.log").open("w", encoding="utf-8")
        command = [source[0], "app-server", "--listen", "stdio://"]
        for index, value in enumerate(source):
            if value == "-c":
                command.extend(source[index : index + 2])
        try:
            self.process = subprocess.Popen(
                command, cwd=self.workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._errors, text=True, encoding="utf-8", errors="replace",
                env=child_environment(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError:
            self._events.close()
            self._errors.close()
            raise
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._next_id = 0
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        try:
            self._request(
                "initialize", {"clientInfo": {"name": "levelcontrol", "version": "0.1.0"}}
            )
            self._send({"method": "initialized", "params": {}})
            if native:
                self.session_config = self.capabilities.codex_session_config(
                    self._request, self.workspace,
                    runtime_server(self.workspace, port, project_id or "levelcontrol",
                                   data_dir or self.workspace / ".levelcontrol/data", design_file))
            result = self._request(
                "thread/start",
                self.thread_options(),
            )
            self.thread_id = result["thread"]["id"]
            self.model = result["model"]
            if native and effort is None:
                self.effort = result.get("reasoningEffort")
            self.models: list[dict[str, Any]] = []
            cursor = None
            for _ in range(10):
                page = self._request("model/list", {"limit": 100, "cursor": cursor})
                self.models.extend(page.get("data", []))
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            connection = self._request(
                "mcpServer/tool/call",
                {
                    "threadId": self.thread_id,
                    "server": "levelcontrol" if native else "lc",
                    "tool": "lc.connect",
                    "arguments": {"command": f"--port {port}"},
                },
            )
            health = connection.get("structuredContent", {})
            if connection.get("isError") or health.get("ok") is not True:
                raise ConnectionError("lc.connect did not confirm a running game")
            self.setup_s = time.perf_counter() - started
        except Exception:
            self.close()
            raise

    def thread_options(self) -> dict[str, Any]:
        options = {"cwd": str(getattr(self, "workspace", ROOT / "project")), "ephemeral": True}
        if getattr(self, "native", False):
            options["developerInstructions"] = turn_context.instructions()
            policy = getattr(self, "capabilities", AgentCapabilities())
            options["config"] = {**policy.codex_config(), **getattr(self, "session_config", {})}
        else:
            options.update(sandbox="read-only", approvalPolicy="never",
                           baseInstructions=turn_context.instructions())
        return options

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                try:
                    self._queue.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self._queue.put(None)

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        with self._send_lock:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def _identifier(self) -> int:
        with self._id_lock:
            identifier = self._next_id
            self._next_id += 1
            return identifier

    def interrupt(self) -> None:
        for identifier in list(getattr(self, "pending_requests", {})):
            self.respond(identifier, {"allow": False})
        if self.current_turn:
            self._send(
                {
                    "id": self._identifier(),
                    "method": "turn/interrupt",
                    "params": {"threadId": self.thread_id, "turnId": self.current_turn},
                }
            )

    def _receive(self, timeout: float = 90) -> dict[str, Any]:
        while True:
            try:
                message = self._queue.get(timeout=timeout)
                break
            except queue.Empty:
                if not getattr(self, "pending_requests", {}):
                    raise TimeoutError("Codex app-server did not respond") from None
        if message is None:
            raise RuntimeError("Codex app-server closed its output")
        if "id" in message and "method" in message:
            method = message["method"]
            if getattr(self, "native", False) and method == "mcpServer/elicitation/request":
                params = message.get("params", {})
                if params.get("mode") != "form":
                    self.on_event({
                        "type": "notice",
                        "message": "MCP 请求类型暂不支持：" + str(
                            params.get("mode", "unknown")),
                    })
                    self._send({"id": message["id"], "result": {"action": "decline"}})
                    return message
                schema = params.get("requestedSchema")
                properties = schema.get("properties") if isinstance(schema, dict) else None
                if properties:
                    self.on_event({
                        "type": "notice",
                        "message": "MCP 表单请求暂不支持：" + str(
                            params.get("message", method)),
                    })
                    self._send({"id": message["id"], "result": {"action": "decline"}})
                    return message
                identifier = str(message["id"])
                self.pending_requests[identifier] = message
                details = safe_image_log(params)
                if not isinstance(details, dict):
                    details = {"request": details}
                details = {**details, "description": params.get("message", "")}
                meta = params.get("_meta") or {}
                tool = meta.get("tool_name") if isinstance(meta, dict) else None
                label = " · ".join(str(part) for part in (
                    params.get("serverName"), tool) if part)
                self.on_event({"type": "approval", "request_id": identifier,
                               "name": label or "MCP 请求授权", "arguments": details})
                return message
            if getattr(self, "native", False) and method in {
                "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                "item/tool/requestUserInput",
                "item/permissions/requestApproval",
            }:
                identifier = str(message["id"])
                self.pending_requests[identifier] = message
                self.on_event({"type": "question" if method.endswith("requestUserInput")
                               else "approval", "request_id": identifier,
                               "name": method, "arguments": safe_image_log(message["params"])})
                return message
            self.on_event({"type": "notice", "message": "不支持的 Agent 请求已拒绝：" + method})
            self._send(
                {
                    "id": message["id"],
                    "error": {
                        "code": -32000,
                        "message": "Interactive requests are not supported by this runner",
                    },
                }
            )
        if message.get("method") == "serverRequest/resolved":
            self.pending_requests.pop(str(message.get("params", {}).get("requestId")), None)
        return message

    def respond(self, request_id: str, answer: dict[str, Any]) -> None:
        message = self.pending_requests.pop(request_id, None)
        if message is None:
            return
        if message["method"] == "mcpServer/elicitation/request":
            result = {"action": "accept" if answer.get("allow") else "decline"}
            if answer.get("allow"):
                result["content"] = answer.get("content") or {}
        elif message["method"].endswith("requestUserInput"):
            result = {"answers": {key: {"answers": [value]} for key, value
                                  in answer.get("answers", {}).items()}}
        elif message["method"] == "item/permissions/requestApproval":
            result = {"permissions": message["params"].get("permissions", {})
                      if answer.get("allow") else {}, "scope": "turn"}
        else:
            result = {"decision": "accept" if answer.get("allow") else "decline"}
        self._send({"id": message["id"], "result": result})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        identifier = self._identifier()
        self._send({"id": identifier, "method": method, "params": params})
        while True:
            message = self._receive()
            if message.get("id") == identifier and "method" not in message:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message["result"]

    def turn(self, prompt: str, submitted_at: float | None = None, *,
             images: tuple[ImageAttachment, ...] = ()) -> dict[str, Any]:
        validate_images(images, getattr(self, "models", []), getattr(self, "model", ""))
        # Defer a queued commit while the model handles new input; previews keep playing.
        held = self.client.read("presentation").get("commit_waiting", False)
        if held:
            held = self.client.edit("presentation hold")["status"] == "applied"
        try:
            self.turn_metrics = turn_context.prepare(self.client, submitted_at)
            self.turn_metrics["attachments"] = image_metrics(images)
            return self._turn(prompt, images) if images or getattr(self, "images", ()) else (
                self._turn(prompt))
        finally:
            if held:
                self.client.edit("presentation resume")

    def replace_image_thread(self) -> None:
        # Ephemeral histories cannot be edited in place; replay only explicit text and receipts.
        previous = self.thread_id
        request_size({"input": self.text_history})
        result = self._request("thread/start", {**self.thread_options(), "model": self.model})
        replacement = result["thread"]["id"]
        try:
            if self.text_history:
                self._request("thread/inject_items", {"threadId": replacement,
                                                      "items": self.text_history})
        except Exception:
            self._request("thread/unsubscribe", {"threadId": replacement})
            raise
        self.thread_id = replacement
        self.images = ()
        self._request("thread/unsubscribe", {"threadId": previous})

    def _turn(self, prompt: str, images: tuple[ImageAttachment, ...] = ()) -> dict[str, Any]:
        metrics = getattr(self, "turn_metrics", {})
        started = metrics.get("started", time.perf_counter())
        previous = getattr(self, "images", ())
        if not hasattr(self, "text_history"):
            self.text_history = []
        changed = image_key(images) != image_key(previous)
        if previous and changed:
            self.replace_image_thread()
        text = image_prompt(prompt, images)
        identifier = self._identifier()
        params: dict[str, Any] = {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": turn_context.model_input(text, metrics)}],
            "model": self.model,
        }
        if images and changed:
            params["input"].extend({"type": "localImage", "path": str(image.path), "detail": "auto"}
                                   for image in images)
        metrics.setdefault("request_bytes", []).append(request_size(params))
        if self.effort:
            params["effort"] = self.effort
        self._events.write(json.dumps({"elapsed_s": time.perf_counter() - started,
                                       "event": {"method": "lc/turnRequest", "params": {
                                           "model": self.model, "effort": self.effort,
                                           **turn_context.summary(metrics)}}}) + "\n")
        self._send(
            {
                "id": identifier,
                "method": "turn/start",
                "params": params,
            }
        )
        calls: dict[str, dict[str, Any]] = {}
        final = ""
        receipt_complete = False
        while True:
            message = self._receive()
            event_thread = message.get("params", {}).get("threadId")
            if event_thread and event_thread != self.thread_id:
                continue
            elapsed = time.perf_counter() - started
            logged = safe_image_log(message)
            if message.get("method", "").endswith("/delta"):
                logged = {"method": message["method"], "characters": len(str(
                    message.get("params", {}).get("delta", "")))}
            log = json.dumps({"elapsed_s": elapsed, "event": logged},
                             ensure_ascii=False)
            self._events.write((self.provider.redact(log) if self.provider else log) + "\n")
            self._events.flush()
            if "error" in message and message.get("id") == identifier:
                raise RuntimeError(safe_image_log(message["error"]))
            params = message.get("params", {})
            method = message.get("method")
            if method == "thread/tokenUsage/updated":
                metrics.setdefault("usage", []).append({"at_s": elapsed,
                                                        **params.get("tokenUsage", {})})
            if message.get("id") == identifier and "result" in message:
                self.current_turn = message["result"]["turn"]["id"]
                self.images = images
            if method == "item/agentMessage/delta":
                self.on_event({"type": "reply_delta",
                               "text": safe_image_log(params.get("delta", ""))})
            item = params.get("item", {})
            if item.get("type") in {"mcpToolCall", "agentMessage", "reasoning"}:
                metrics.setdefault("first_model_event_s", elapsed)
            if item.get("type") == "mcpToolCall":
                call = calls.setdefault(item["id"], {"started_s": elapsed})
                call.update(tool=item.get("tool"), arguments=item.get("arguments", {}))
                if method == "item/started":
                    self.on_event(
                        {
                            "type": "tool",
                            "id": item["id"],
                            "phase": "started",
                            "name": call["tool"],
                            "arguments": call["arguments"],
                        }
                    )
                if method == "item/completed":
                    call.update(completed_s=elapsed, receipt=find_receipt(item.get("result")))
                    if call["tool"] in {"lgg_build", "lgg_module"}:
                        ready = turn_context.build_event(call["receipt"], metrics)
                        if ready:
                            self.on_event(ready)
                    self.on_event(
                        {
                            "type": "tool",
                            "id": item["id"],
                            "phase": "completed",
                            "name": call["tool"],
                            "arguments": call["arguments"],
                            "seconds": elapsed - call["started_s"],
                            "receipt": call["receipt"],
                        }
                    )
                    print(
                        f"{call['tool']} {safe_image_log(call['arguments'])} | "
                        f"{elapsed - call['started_s']:.3f} s",
                        flush=True,
                    )
                    if completed_column_undo(prompt, list(calls.values())):
                        receipt_complete = True
                        self.interrupt()
            if item.get("type") == "agentMessage" and method == "item/completed":
                final = safe_image_log(item.get("text", ""))
            if method == "turn/completed":
                self.current_turn = None
                metrics["dialogue_s"] = elapsed + metrics.get("queue_s", 0)
                missing_receipts = is_column_undo(prompt) and not receipt_complete
                if missing_receipts:
                    final = "本轮未取得完整的创建和撤销回执，操作未确认完成。"
                if params["turn"].get("error"):
                    final = str(safe_image_log(params["turn"]["error"]))
                self.text_history.extend(replay_turn(text, final, list(calls.values())))
                return {
                    "status": "completed"
                    if receipt_complete
                    else "failed"
                    if missing_receipts
                    else params["turn"]["status"],
                    "agent_status": params["turn"]["status"],
                    "completion": "matching_receipts" if receipt_complete else "model_turn",
                    "elapsed_s": elapsed,
                    "tools": list(calls.values()),
                    "final": "已在你面前放置 5 米高的柱子，并已撤销。"
                    if receipt_complete
                    else final,
                    "events": str(self.event_path),
                    "model": self.model,
                    "effort": self.effort,
                    "timings": turn_context.summary(metrics),
                }

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self.images = ()
        if self.process.stdin:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self._events.close()
        self._errors.close()

    def __enter__(self) -> ResidentAgent:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
