"""Verify standard Responses and the existing MCP tools with an isolated read-only world."""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from lc.api_agent import ApiAgent
from lc.client import CONNECTION
from lc.models import Provider


class Game(BaseHTTPRequestHandler):
    reads = 0

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/health":
            data = {"ok": True, "protocol": 2, "port": self.server.server_port}
        elif self.path == "/v1/presentation":
            data = {"state": "idle", "commit_waiting": False}
        elif self.path.startswith("/v1/entities"):
            type(self).reads += 1
            data = {"count": 0, "entities": [], "version": 0}
        else:
            self.send_error(404)
            return
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        self.send_error(405, "This isolated game fixture never accepts world edits")


class API(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []
    key = "temporary-integration-" + uuid.uuid4().hex

    def log_message(self, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        inputs = body.get("input", [])
        tool_name = next((tool["name"] for tool in body.get("tools", [])
                          if tool.get("type") == "function" and "lc_read" in tool["name"]), None)
        self.requests.append({
            "model": body["model"], "reasoning": body.get("reasoning"),
            "authorized": self.headers.get("Authorization") == "Bearer " + self.key,
            "read_tool_available": tool_name is not None,
            "input_types": [item.get("type", "message") for item in inputs],
            "tool_names": [(tool.get("type"), tool.get("name"),
                            [item.get("name") for item in tool.get("tools", [])])
                           for tool in body.get("tools", [])],
        })
        if not tool_name:
            self.send_error(400, "lc.read was not exposed as a function tool")
            return
        last_output = any(item.get("type") == "function_call_output" for item in inputs[-2:])
        identity = uuid.uuid4().hex
        if last_output:
            item = {"id": "msg_" + identity, "type": "message", "status": "completed",
                    "role": "assistant", "content": [{"type": "output_text",
                            "text": "模型连接测试通过。", "annotations": []}]}
        else:
            item = {"id": "fc_" + identity, "type": "function_call", "status": "completed",
                    "call_id": "call_" + identity, "name": tool_name,
                    "arguments": '{"command":"objects * --count"}'}
        response = {"id": "resp_" + identity, "object": "response", "created_at": 1,
                    "status": "completed", "model": body["model"], "output": [item],
                    "usage": {"input_tokens": 100, "output_tokens": 10,
                              "total_tokens": 110, "input_tokens_details": {"cached_tokens": 0},
                              "output_tokens_details": {"reasoning_tokens": 0}}}
        events = [
            {"type": "response.created", "response": {**response, "status": "in_progress",
                                                        "output": []}},
            {"type": "response.output_item.added", "output_index": 0, "item": item},
        ]
        if item["type"] == "message":
            events.append({"type": "response.output_text.delta", "item_id": item["id"],
                           "output_index": 0, "content_index": 0, "delta": "模型连接测试通过。"})
        events += [{"type": "response.output_item.done", "output_index": 0, "item": item},
                   {"type": "response.completed", "response": response}]
        raw = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                      for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    previous = CONNECTION.read_bytes() if CONNECTION.exists() else None
    game = ThreadingHTTPServer(("127.0.0.1", 0), Game)
    api = ThreadingHTTPServer(("127.0.0.1", 0), API)
    workers = [threading.Thread(target=server.serve_forever, daemon=True) for server in [game, api]]
    for worker in workers:
        worker.start()
    report: dict[str, Any] = {"passed": False, "providers": []}
    try:
        for kind, models in [("openai", ["gpt-6-luna", "gpt-6.1-sol"]),
                             ("deepseek", ["deepseek-flash", "deepseek-v4-pro"])]:
            profile = Provider(kind, kind, kind, f"http://127.0.0.1:{api.server_port}",
                               models=models, api_key=API.key)
            turns = []
            with ApiAgent(game.server_port, models[0], effort="low", provider=profile,
                          on_event=lambda event: None) as agent:
                thread_id = agent.thread_id
                for model, effort in [(models[0], "low"), (models[1], "high"), (models[0], "none")]:
                    agent.model, agent.effort = model, effort
                    result = agent.turn("只读取场景实体数量，然后简短回复。")
                    assert result["status"] == "completed", result
                    assert any(call["tool"] == "lc.read" for call in result["tools"]), result
                    assert agent.thread_id == thread_id
                    turns.append({"model": model, "effort": effort,
                                  "seconds": result["elapsed_s"], "tools": len(result["tools"])})
            report["providers"].append({"kind": kind, "same_thread": True, "turns": turns})
        assert all(request["authorized"] and request["read_tool_available"]
                   for request in API.requests)
        assert all(request["reasoning"]["effort"] in {"low", "high", "none"}
                   for request in API.requests)
        assert Game.reads == 6
        report.update(passed=True, requests=API.requests, world_reads=Game.reads, world_writes=0)
    finally:
        report["requests"] = API.requests
        for server in [game, api]:
            server.shutdown()
            server.server_close()
        if previous is None:
            CONNECTION.unlink(missing_ok=True)
        else:
            CONNECTION.write_bytes(previous)
        path = Path(__file__).parent / "reports/model-providers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"passed": report["passed"], "world_reads": Game.reads}, ensure_ascii=False))


if __name__ == "__main__":
    main()
