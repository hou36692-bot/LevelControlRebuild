import copy
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from lc.api_agent import ApiAgent
from lc.models import Provider


def agent_fixture():
    agent = ApiAgent.__new__(ApiAgent)
    agent.provider = Provider("test", "Test", base_url="http://localhost:20000",
                              api_key="sk-not-in-model-context")
    agent.model, agent.effort = "test-model", "high"
    agent.cancelled = threading.Event()
    agent._active_loop = None
    agent._active_task = None
    agent.history = []
    agent.instructions = "Use LC tools only."
    agent.tools = [SimpleNamespace(name="lc.edit", description="Edit",
                                   inputSchema={"type": "object"})]
    agent.names = {"lc_edit": "lc.edit"}
    agent.event_path = "test.jsonl"
    events = []
    agent.emit = events.append
    return agent, events


def function(identity, command):
    import json

    return {"type": "function_call", "call_id": identity, "name": "lc_edit",
            "arguments": json.dumps({"command": command})}


def test_receipts_stop_extra_calls_and_keep_deepseek_reasoning_for_tool_followups():
    agent, events = agent_fixture()
    create = function("create", "apply create front-column --template blockout/column "
                      "--size 1m,1m,5m --front 4m")
    undo = function("undo", "undo --tx-id tx-create")
    extra = function("extra", "apply delete front-column")
    reasoning = {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "thinking"}]}
    responses = iter([{"output": [reasoning, create]}, {"output": [undo, extra]}])
    requests = []

    def stream(body):
        requests.append(copy.deepcopy(body))
        return next(responses)

    calls = []

    def invoke(name, arguments):
        calls.append(arguments["command"])
        return {"structuredContent": {
            "req_id": "req-" + str(len(calls)),
            "status": "applied", "tx_id": "tx-create" if len(calls) == 1 else "tx-undo",
            "entity_ids": ["front-column"], "applied": 1, "version": len(calls)}}

    agent.stream, agent.invoke_tool = stream, invoke
    result = agent._turn("在我面前放一根 5 米高的柱子，然后撤销")
    assert result["completion"] == "matching_receipts"
    assert result["status"] == "completed" and len(calls) == 2
    assert reasoning in requests[1]["input"]
    assert agent.provider.api_key not in str(requests)
    assert requests[0]["reasoning"] == {"effort": "high"}
    assert requests[0]["tools"][0]["name"] == "lc_edit"
    assert any(item.get("call_id") == "extra" and item.get("type") == "function_call_output"
               for item in agent.history)
    assert len([event for event in events if event["type"] == "tool"]) == 4


def test_api_failure_redacts_key_and_cancellation_never_executes_pending_tool():
    agent, _ = agent_fixture()

    def failure(body):
        raise ValueError("Unauthorized " + agent.provider.api_key)

    agent.stream = failure
    result = agent._turn("只回复 OK")
    assert result["status"] == "failed"
    assert agent.provider.api_key not in result["final"]

    def cancelled(body):
        agent.cancelled.set()
        return {"output": [function("unexecuted", "apply delete front-column")]}

    agent.stream = cancelled
    agent.invoke_tool = lambda *args: (_ for _ in ()).throw(AssertionError("must not run"))
    result = agent._turn("取消测试")
    assert result["status"] == "interrupted" and not result["tools"]
    assert any(item.get("type") == "function_call_output" and item.get("call_id") == "unexecuted"
               for item in agent.history)


def test_stop_unblocks_a_real_sse_socket_before_the_next_token():
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            release.wait(5)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    agent, _ = agent_fixture()
    agent._response = None
    agent.provider.base_url = f"http://127.0.0.1:{server.server_port}"
    errors = []

    def read():
        try:
            agent.stream({"model": "test", "input": "test", "stream": True})
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while agent._response is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert agent._response is not None
        agent.interrupt()
        worker.join(timeout=1)
        assert not worker.is_alive() and errors and agent.cancelled.is_set()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
