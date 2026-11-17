"""Standard Responses conversations using the existing, restricted LC tool registry."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx

from . import mcp_server, turn_context
from .agent import find_receipt
from .client import ROOT, Client
from .command import parse
from .images import (
    ImageAttachment,
    image_key,
    image_metrics,
    image_prompt,
    request_size,
    safe_image_log,
    validate_images,
    without_images,
)
from .models import Provider
from .resident import completed_column_undo, is_column_undo


class ApiAgent:
    def __init__(self, port: int, model: str, effort: str, provider: Provider,
                 on_event: Callable[[dict[str, Any]], None]) -> None:
        started = time.perf_counter()
        provider.validate()
        self.provider, self.model, self.effort = provider, model, effort
        self.on_event = on_event
        self.client = Client(port)
        self.client.connect(f"--port {port}")
        self.thread_id = "api-" + uuid.uuid4().hex
        self.cancelled = threading.Event()
        self._response: Any = None
        self._active_loop: asyncio.AbstractEventLoop | None = None
        self._active_task: asyncio.Task[Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.images: tuple[ImageAttachment, ...] = ()
        self.instructions = turn_context.instructions()
        self.tools = asyncio.run(mcp_server.server.list_tools())
        self.names = {tool.name.replace(".", "_"): tool.name for tool in self.tools}
        if len(self.names) != len(self.tools):
            raise ValueError("模型工具名称冲突。")
        directory = ROOT / "tests/reports/agent"
        directory.mkdir(parents=True, exist_ok=True)
        self.event_path = directory / f"{self.thread_id}.jsonl"
        self.setup_s = time.perf_counter() - started

    @property
    def models(self) -> list[dict[str, Any]]:
        return [{"model": name, "defaultReasoningEffort": self.provider.effort,
                 **({"inputModalities": self.provider.modalities[name]}
                    if name in self.provider.modalities else {}),
                 "supportedReasoningEfforts": [{"reasoningEffort": effort}
                                              for effort in self.provider.efforts]}
                for name in self.provider.models]

    def emit(self, event: dict[str, Any]) -> None:
        # Authentication material is never part of model context or the event log.
        event = {"at_monotonic_s": time.perf_counter(), "at_utc_s": time.time(), **event}
        safe = json.loads(self.provider.redact(json.dumps(
            safe_image_log(event), ensure_ascii=False)))
        self.on_event(safe)
        if safe.get("type") == "reply_delta":
            safe = {key: value for key, value in safe.items() if key != "text"} | {
                "characters": len(safe.get("text", ""))}
        with self.event_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(safe, ensure_ascii=False) + "\n")

    def interrupt(self) -> None:
        self.cancelled.set()
        loop, task = self._active_loop, self._active_task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def stream(self, body: dict[str, Any]) -> dict[str, Any]:
        size = request_size(body)
        getattr(self, "turn_metrics", {}).setdefault("request_bytes", []).append(size)
        return asyncio.run(self._stream_async(body))

    async def _stream_async(self, body: dict[str, Any]) -> dict[str, Any]:
        self._active_loop = asyncio.get_running_loop()
        self._active_task = asyncio.current_task()
        headers = {"Accept": "text/event-stream"}
        if self.provider.api_key:
            headers["Authorization"] = "Bearer " + self.provider.api_key
        parsed = urlsplit(self.provider.base_url)
        proxy = None if proxy_bypass(parsed.hostname or "") else getproxies().get(parsed.scheme)
        try:
            if self.cancelled.is_set():
                raise InterruptedError("对话已停止。")
            async with httpx.AsyncClient(proxy=proxy, timeout=90, follow_redirects=False) as client:
                async with client.stream("POST", self.provider.base_url + "/responses",
                                         json=body, headers=headers) as response:
                    self._response = response
                    if response.is_redirect:
                        raise ValueError("API 返回重定向；请在设置中填写最终 API 根地址。")
                    if response.is_error:
                        detail = (await response.aread())[:4096].decode("utf-8", errors="replace")
                        raise ValueError(safe_image_log(self.provider.redact(
                            f"HTTP {response.status_code}: {detail}"))[:500])
                    if "text/event-stream" not in response.headers.get("Content-Type", ""):
                        raw = await response.aread()
                        if len(raw) > 4 * 1024 * 1024:
                            raise ValueError("模型响应超过 4 MiB。")
                        return json.loads(raw)
                    data: list[str] = []
                    async for text in response.aiter_lines():
                        if len(text) > 4 * 1024 * 1024:
                            raise ValueError("模型流式事件超过 4 MiB。")
                        if text.startswith("data:"):
                            data.append(text[5:].lstrip())
                        elif not text and data:
                            payload, data = "\n".join(data), []
                            if payload == "[DONE]":
                                continue
                            event = json.loads(payload)
                            if event.get("type", "").startswith("response."):
                                metrics = getattr(self, "turn_metrics", {})
                                if metrics:
                                    metrics.setdefault("first_model_event_s",
                                                       time.perf_counter() - metrics["started"])
                            if event.get("type") == "response.output_text.delta":
                                self.emit({"type": "reply_delta",
                                           "text": safe_image_log(event.get("delta", ""))})
                            if event.get("type") in {
                                "response.completed", "response.incomplete", "response.failed"
                            }:
                                return event["response"]
                            if event.get("type") == "error":
                                raise ValueError(self.provider.redact(event.get("message", event)))
                    raise ValueError("模型流已结束，但未返回 response.completed。")
        except asyncio.CancelledError:
            raise InterruptedError("对话已停止。") from None
        finally:
            self._response = None
            self._active_loop = None
            self._active_task = None

    def invoke_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # All world writes still go through the exact MCP functions and lc.client boundary.
        if name == "lc.connect":
            try:
                target = parse("connect", arguments.get("command") or
                               f"--port {self.client.port}").data["port"]
                if target != self.client.port:
                    raise ValueError(f"This agent session is bound to port {self.client.port}")
            except ValueError as error:
                return {"isError": True, "content": [{"type": "text", "text": str(error)}]}
        mcp_server.client = self.client
        result = asyncio.run(mcp_server.server.call_tool(name, arguments))
        if isinstance(result, tuple):
            content, structured = result
            return {"content": [item.model_dump() for item in content],
                    "structuredContent": structured}
        if isinstance(result, dict):
            return {"structuredContent": result}
        return {"content": [item.model_dump() for item in result]}

    def turn(self, prompt: str, submitted_at: float | None = None, *,
             images: tuple[ImageAttachment, ...] = ()) -> dict[str, Any]:
        validate_images(images, self.models, self.model)
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

    def _turn(self, prompt: str, images: tuple[ImageAttachment, ...] = ()) -> dict[str, Any]:
        metrics = getattr(self, "turn_metrics", {})
        started = metrics.get("started", time.perf_counter())
        self.cancelled.clear()
        previous = getattr(self, "images", ())
        changed = image_key(images) != image_key(previous)
        text = turn_context.model_input(image_prompt(prompt, images), metrics)
        content: Any = text
        if images and changed:
            content = [{"type": "input_text", "text": text}]
            content.extend({"type": "input_image", "image_url": image.data_url(), "detail": "auto"}
                           for image in images)
        if changed:
            self.history = without_images(self.history)
        self.images = images
        self.history.append({"role": "user", "content": content})
        calls: list[dict[str, Any]] = []
        final, status, receipt_complete = "", "completed", False
        error_message = ""
        try:
            for _ in range(32):
                if self.cancelled.is_set():
                    raise InterruptedError("对话已停止。")
                body: dict[str, Any] = {
                    "model": self.model, "instructions": self.instructions,
                    "input": self.history, "stream": True, "store": False,
                    "include": ["reasoning.encrypted_content"],
                    "parallel_tool_calls": False,
                    "tools": [{"type": "function", "name": tool.name.replace(".", "_"),
                               "description": tool.description or "",
                               "parameters": tool.inputSchema}
                              for tool in self.tools],
                }
                if self.effort:
                    body["reasoning"] = {"effort": self.effort}
                if images and not prompt.strip():
                    body["tools"] = []
                response = self.stream(body)
                if response.get("error") or response.get("status") in {"failed", "incomplete"}:
                    raise ValueError(self.provider.redact(
                        response.get("error") or response.get("incomplete_details")
                        or "模型未完成响应。"))
                metrics.setdefault("first_model_event_s", time.perf_counter() - started)
                metrics.setdefault("usage", []).append(response.get("usage", {}))
                output = response.get("output", [])
                if not output:
                    raise ValueError("模型返回了空响应。")
                self.history.extend(output)
                functions = [item for item in output if item.get("type") == "function_call"]
                for item in output:
                    if item.get("type") == "message":
                        final = "".join(part.get("text", "") for part in item.get("content", [])
                                        if part.get("type") == "output_text")
                if not functions:
                    break
                for item in functions:
                    if images and not prompt.strip():
                        raise ValueError("纯图片消息仅分析，不允许执行场景工具。")
                    if self.cancelled.is_set():
                        raise InterruptedError("对话已停止。")
                    name = self.names.get(item.get("name", ""))
                    arguments: dict[str, Any] = {}
                    call = {"tool": name or item.get("name", ""),
                            "started_s": time.perf_counter() - started}
                    identifier = item.get("call_id", "")
                    try:
                        arguments = json.loads(item.get("arguments", "{}"))
                        if name is None or not isinstance(arguments, dict) or not identifier:
                            raise ValueError("模型请求了未知工具或非法参数。")
                        self.emit({"type": "tool", "id": identifier, "phase": "started",
                                   "name": name, "arguments": arguments})
                        result = self.invoke_tool(name, arguments)
                    except Exception as error:
                        result = {"isError": True, "error": self.provider.redact(error)}
                    elapsed = time.perf_counter() - started
                    call.update(arguments=arguments, completed_s=elapsed,
                                receipt=find_receipt(result))
                    calls.append(call)
                    if name in {"lgg_build", "lgg_module"}:
                        ready = turn_context.build_event(call["receipt"], metrics)
                        if ready:
                            self.emit(ready)
                    self.history.append({"type": "function_call_output", "call_id": identifier,
                                         "output": json.dumps(result, ensure_ascii=False)})
                    self.emit({"type": "tool", "id": identifier, "phase": "completed",
                               "name": call["tool"], "arguments": arguments,
                               "seconds": elapsed - call["started_s"], "receipt": call["receipt"]})
                    if completed_column_undo(prompt, calls):
                        receipt_complete = True
                        break
                if receipt_complete:
                    self.finish_tool_pairs("Skipped because the requested task is complete")
                    final = "已在你面前放置 5 米高的柱子，并已撤销。"
                    self.history.append({"role": "assistant", "content": final})
                    break
            else:
                raise ValueError("本轮超过 32 次模型响应，已停止。请缩小指令范围。")
        except Exception as error:
            status = ("interrupted" if self.cancelled.is_set()
                      or isinstance(error, InterruptedError) else "failed")
            error_message = safe_image_log(self.provider.redact(error))
            final = error_message
            # End pending tool pairs so a later turn can safely continue after interruption.
            self.finish_tool_pairs("Interrupted; tool was not executed")
        if is_column_undo(prompt) and not receipt_complete and status == "completed":
            status, final = "failed", "本轮未取得完整的创建和撤销回执，操作未确认完成。"
        result = {"status": status, "agent_status": status,
                  "elapsed_s": time.perf_counter() - started,
                  "tools": calls, "final": safe_image_log(final), "model": self.model,
                  "effort": self.effort,
                  "completion": "matching_receipts" if receipt_complete else "model_turn",
                  "events": str(self.event_path)}
        metrics["dialogue_s"] = result["elapsed_s"] + metrics.get("queue_s", 0)
        result["timings"] = turn_context.summary(metrics)
        if error_message:
            result["error"] = error_message
        self.emit({"type": "model_result", "result": result})
        return result

    def finish_tool_pairs(self, reason: str) -> None:
        pending = {item["call_id"] for item in self.history if item.get("type") == "function_call"}
        done = {item["call_id"] for item in self.history
                if item.get("type") == "function_call_output"}
        for identity in pending - done:
            self.history.append({"type": "function_call_output", "call_id": identity,
                                 "output": json.dumps({"error": reason})})

    def close(self) -> None:
        self.interrupt()
        self.images = ()
        self.history.clear()

    def __enter__(self) -> ApiAgent:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
