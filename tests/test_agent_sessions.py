import hashlib
import io
import json
import queue
from types import SimpleNamespace

import pytest
from lc import agent_claude, resident
from lc.agent import arguments
from lc.agent_capabilities import AgentCapabilities
from lc.agent_codex import CodexSession
from lc.agent_models import AgentModelSelection
from lc.images import ImageAttachment


class Output:
    def __init__(self):
        self.queue = queue.Queue()

    def __iter__(self):
        while (message := self.queue.get(timeout=5)) is not None:
            yield json.dumps(message) + "\n"

    def close(self):
        pass


class Process:
    def __init__(self, handler):
        self.stdout = Output()
        self.stderr = io.StringIO()
        self.messages = []
        self.returncode = None
        self.stdin = SimpleNamespace(write=self.write, flush=lambda: None, close=self.stop)
        self.handler = handler

    def write(self, text):
        message = json.loads(text)
        self.messages.append(message)
        for response in self.handler(message):
            self.stdout.queue.put(response)

    def stop(self):
        self.returncode = 0
        self.stdout.queue.put(None)

    def wait(self, **kwargs):
        self.stop()
        return 0

    terminate = stop
    kill = stop

    def poll(self):
        return self.returncode


class Game:
    def __init__(self, *args, **kwargs):
        self.commands = []

    def read(self, command):
        return {"version": 7, "commit_waiting": False}

    def edit(self, command):
        self.commands.append(command)
        return {"status": "applied"}


@pytest.fixture
def claude(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_USER_DATA_ROOT", str(tmp_path / "local"))
    monkeypatch.setattr(agent_claude, "executable", lambda name: "claude.exe")
    monkeypatch.setattr(agent_claude, "Client", Game)
    monkeypatch.setattr(agent_claude.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(stdout="[]"))
    turns, processes, commands, events = [], [], [], []

    def handler(message):
        if message["type"] == "control_request":
            data = {}
            if message["request"]["subtype"] == "initialize":
                data = {"models": [
                    {"value": "default", "resolvedModel": "native", "displayName": "Default",
                     "supportsEffort": True, "supportedEffortLevels": ["low", "high"]},
                    {"value": "alternate", "resolvedModel": "native-alternate",
                     "displayName": "Alternate", "supportsEffort": True,
                     "supportedEffortLevels": ["low", "high"]},
                ]}
            return [
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": message["request_id"],
                        "response": data,
                    },
                }
            ]
        if message["type"] == "user":
            return turns.pop(0)
        return []

    def popen(command, **kwargs):
        commands.append(command)
        process = Process(handler)
        processes.append(process)
        return process

    monkeypatch.setattr(agent_claude.subprocess, "Popen", popen)
    session = agent_claude.ClaudeSession(
        18870,
        workspace=tmp_path / "workspace",
        data_dir=tmp_path / "data",
        project_id="project-a",
        design_file=tmp_path / "design.json",
        on_event=events.append,
    )
    yield session, turns, processes, commands, events
    session.close()


def result(**kwargs):
    return {"type": "result", "subtype": "success", "result": "完成", **kwargs}


def test_claude_stream_receipts_and_project_binding(claude):
    session, turns, processes, commands, events = claude
    receipt = {"req_id": "build-one", "tx_id": "tx", "status": "scheduled", "scene": "one"}
    turns.append(
        [
            {
                "type": "system",
                "subtype": "init",
                "session_id": session.thread_id,
                "model": "native",
            },
            {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "开始"}}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "mcp__lc__lgg_build",
                            "input": {"request_id": "build-one"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": [{"type": "text", "text": json.dumps(receipt)}],
                        }
                    ]
                },
            },
            result(),
        ]
    )
    output = session.turn("建造")
    assert output["tools"][0]["receipt"]["status"] == "scheduled"
    assert [event["type"] for event in events] == ["reply_delta", "tool", "build_pending", "tool"]
    assert not any(event["type"] == "build_ready" for event in events)
    assert "--model" not in commands[0] and "--dangerously-skip-permissions" not in commands[0]
    configuration = json.loads(session.config_path.read_text("utf-8"))
    environment = configuration["mcpServers"]["levelcontrol"]["env"]
    assert environment["LC_BIND_PORT"] == "18870" and environment["LC_PROJECT_ID"] == "project-a"
    assert environment["LC_DESIGN_FILE"].endswith("design.json")
    assert processes[0].messages[-1]["message"]["content"][0]["text"].startswith("建造")


@pytest.mark.parametrize("allow", [False, True])
def test_claude_permissions_require_explicit_response(claude, allow):
    session, turns, processes, _, events = claude
    turns.append(
        [
            {
                "type": "control_request",
                "request_id": "approve-1",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Bash",
                    "input": {"command": "pwd"},
                },
            },
            result(),
        ]
    )

    def emit(event):
        events.append(event)
        if event["type"] == "approval":
            session.respond(event["request_id"], {"allow": allow})

    session.on_event = emit
    session.turn("检查目录")
    response = next(
        item["response"] for item in processes[0].messages if item["type"] == "control_response"
    )
    assert response["request_id"] == "approve-1"
    assert response["response"]["behavior"] == ("allow" if allow else "deny")
    assert not session.pending_requests
    count = len(processes[0].messages)
    session.respond("stale", {"allow": True})
    assert len(processes[0].messages) == count


def test_claude_question_interrupt_and_error(claude):
    session, turns, processes, _, _ = claude
    turns.append(
        [
            {
                "type": "control_request",
                "request_id": "question",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "AskUserQuestion",
                    "input": {"questions": [{"question": "颜色？"}]},
                },
            },
            result(),
        ]
    )
    session.on_event = lambda event: (
        session.respond(event["request_id"], {"allow": True, "answers": {"颜色？": "红色"}})
        if event["type"] == "question"
        else None
    )
    session.turn("选择")
    response = [item for item in processes[0].messages if item["type"] == "control_response"][0]
    assert response["response"]["response"]["updatedInput"]["answers"] == {"颜色？": "红色"}
    turns.append(
        [result(is_error=True, subtype="error_during_execution", result="vision unavailable")]
    )
    assert session.turn("看图")["status"] == "failed"
    turns.append(
        [
            {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "working"}}},
            result(),
        ]
    )
    session.on_event = lambda event: session.interrupt() if event["type"] == "reply_delta" else None
    assert session.turn("停止")["status"] == "interrupted"
    assert any(
        item.get("request", {}).get("subtype") == "interrupt" for item in processes[0].messages
    )


def test_claude_images_clear_and_resume(claude, tmp_path):
    session, turns, processes, commands, _ = claude
    path = tmp_path / "reference.png"
    raw = b"test-image-payload"
    path.write_bytes(raw)
    image = ImageAttachment(
        path, "image/png", 1, 1, len(raw), hashlib.sha256(raw).hexdigest(), 0, None
    )
    turns.append([result()])
    session.turn("", images=(image,))
    sent = processes[0].messages[-1]["message"]["content"]
    assert sent[1]["type"] == "image" and "仅分析" in sent[0]["text"]
    first = session.thread_id
    turns.append([result()])
    session.turn("现在只讨论文字")
    assert len(processes) == 2 and session.thread_id != first
    assert "image" not in [
        block["type"] for block in processes[1].messages[-1]["message"]["content"]
    ]
    assert "test-image-payload" not in session.event_path.read_text("utf-8")
    session._stop_process()
    session._start(session.thread_id)
    assert commands[-1][-2:] == ["--resume", session.thread_id]
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="已损坏"):
        session.turn("看图", images=(image,))


def test_claude_missing_receipts_never_confirm_column_undo(claude):
    session, turns, _, _, _ = claude
    turns.append([result(result="已创建并撤销")])
    output = session.turn("在我面前放一根 5 米高的柱子，然后撤销")
    assert output["status"] == "failed" and "未确认" in output["final"]


def test_claude_disconnect_closes_transport_without_replaying(claude):
    session, turns, processes, _, _ = claude
    turns.append([None])
    with pytest.raises(RuntimeError, match="closed its output"):
        session.turn("创建")
    assert session.process is None
    with pytest.raises(RuntimeError, match="session is closed"):
        session.turn("继续")
    assert len([item for item in processes[0].messages if item["type"] == "user"]) == 1


def test_claude_normalizes_native_mcp_tool_names(claude):
    session, turns, _, _, _ = claude
    turns.append([{"type": "assistant", "message": {"content": [{
        "type": "tool_use", "id": "edit", "name": "mcp__levelcontrol__lc_edit", "input": {}}]}},
        result()])
    assert session.turn("编辑")["tools"][0]["tool"] == "lc.edit"


def test_claude_native_model_mapping_and_parameter_switch_preserve_session(claude):
    session, turns, processes, commands, _ = claude
    first = session.thread_id
    state = session.model_state()
    assert state["model"] == "default" and state["resolved_model"] == "native"
    assert state["effort"] is None
    assert state["models"][1]["supportedReasoningEfforts"] == [
        {"reasoningEffort": "low"}, {"reasoningEffort": "high"}]
    session.configure_model(AgentModelSelection("alternate", "high"))
    assert session.thread_id == first and len(processes) == 2
    assert processes[0].poll() == 0
    assert commands[-1][-2:] == ["--session-id", first]
    assert commands[-1][commands[-1].index("--model") + 1] == "alternate"
    assert commands[-1][commands[-1].index("--effort") + 1] == "high"
    turns.append([result()])
    session.turn("保留上下文")
    session.configure_model(AgentModelSelection("alternate", "low"))
    assert commands[-1][-2:] == ["--resume", first]
    assert session.model_state()["effort"] == "low"
    assert len([message for process in processes for message in process.messages
                if message["type"] == "user"]) == 1
    with pytest.raises(ValueError):
        session.configure_model(AgentModelSelection("alternate", "max"))
    assert len(processes) == 3 and session.effort == "low"


def test_claude_failed_model_restart_restores_previous_configuration(claude, monkeypatch):
    session, _, processes, _, _ = claude
    start = session._start

    def fail_once(resume_id=None):
        if session.effort == "high":
            raise RuntimeError("new model failed to initialize")
        start(resume_id)

    monkeypatch.setattr(session, "_start", fail_once)
    with pytest.raises(RuntimeError, match="failed to initialize"):
        session.configure_model(AgentModelSelection("alternate", "high"))
    assert session.model_state()["model"] == "default" and session.effort is None
    assert not session._closed and len(processes) == 2


def test_claude_four_images_and_vision_error_keep_user_input(claude, tmp_path):
    session, turns, processes, _, _ = claude
    path = tmp_path / "image.png"
    raw = b"image"
    path.write_bytes(raw)
    attachment = ImageAttachment(path, "image/png", 1, 1, len(raw),
                                 hashlib.sha256(raw).hexdigest(), 0, None)
    turns.append([result(is_error=True, result="", errors=["image input not supported"])])
    output = session.turn("", images=(attachment,) * 4)
    assert output["status"] == "failed" and output["final"] == "image input not supported"
    assert len(processes[0].messages[-1]["message"]["content"]) == 5
    assert len(session.images) == 4 and path.exists()
    with pytest.raises(ValueError, match="最多 4 张"):
        session.turn("", images=(attachment,) * 5)


def test_codex_permission_grants_are_scoped_and_questions_are_correlated():
    agent = resident.ResidentAgent.__new__(resident.ResidentAgent)
    messages = []
    agent._send = messages.append
    requested = {"network": {"enabled": True}}
    agent.pending_requests = {
        "permission": {"id": 41, "method": "item/permissions/requestApproval",
                       "params": {"permissions": requested}},
        "question": {"id": 42, "method": "item/tool/requestUserInput"},
    }
    agent.respond("permission", {"allow": True})
    agent.respond("question", {"allow": True, "answers": {"color": "red"}})
    assert messages == [
        {"id": 41, "result": {"permissions": requested, "scope": "turn"}},
        {"id": 42, "result": {"answers": {"color": {"answers": ["red"]}}}},
    ]
    agent.respond("permission", {"allow": True})
    assert len(messages) == 2


def test_codex_mcp_elicitation_is_routed_as_approval():
    agent = resident.ResidentAgent.__new__(resident.ResidentAgent)
    agent.native = True
    agent.pending_requests = {}
    agent._queue = queue.Queue()
    events, messages = [], []
    agent.on_event = events.append
    agent._send = messages.append
    request = {
        "id": 43,
        "method": "mcpServer/elicitation/request",
        "params": {
            "serverName": "levelcontrol",
            "mode": "form",
            "_meta": {"tool_name": "lc.context"},
            "message": 'Allow the levelcontrol MCP server to run tool "lc.context"?',
            "requestedSchema": {"type": "object", "properties": {}},
        },
    }
    agent._queue.put(request)
    message = agent._receive(timeout=0.1)
    assert message is request
    assert agent.pending_requests == {"43": request}
    assert events[0]["type"] == "approval"
    assert events[0]["request_id"] == "43"
    assert events[0]["name"] == "levelcontrol · lc.context"
    assert events[0]["arguments"]["description"] == request["params"]["message"]
    agent.respond("43", {"allow": True})
    assert messages == [{"id": 43, "result": {"action": "accept", "content": {}}}]
    agent.pending_requests["44"] = {
        "id": 44, "method": "mcpServer/elicitation/request", "params": {}}
    agent.respond("44", {"allow": False})
    assert messages[-1] == {"id": 44, "result": {"action": "decline"}}
    agent._queue.put({
        "id": 45,
        "method": "mcpServer/elicitation/request",
        "params": {
            "mode": "form",
            "requestedSchema": {"properties": {"name": {"type": "string"}}},
        },
    })
    agent._receive(timeout=0.1)
    assert events[-1]["type"] == "notice"
    assert messages[-1] == {"id": 45, "result": {"action": "decline"}}
    agent._queue.put({
        "id": 46,
        "method": "mcpServer/elicitation/request",
        "params": {"mode": "url", "message": "Open this URL", "url": "https://example.com"},
    })
    agent._receive(timeout=0.1)
    assert events[-1]["type"] == "notice"
    assert messages[-1] == {"id": 46, "result": {"action": "decline"}}


def test_codex_native_process_inherits_model_and_routes_approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(resident, "Client", Game)
    processes, events, commands = [], [], []
    receipt = {"req_id": "create", "tx_id": "tx", "status": "applied"}

    def handler(message):
        method = message.get("method")
        data = {
            "thread/start": {"thread": {"id": "thread-1"}, "model": "native-model",
                             "reasoningEffort": "max"},
            "model/list": {"data": [
                {"model": "native-model", "defaultReasoningEffort": "high",
                 "supportedReasoningEfforts": [{"reasoningEffort": "high"},
                                                {"reasoningEffort": "max"}]},
                {"model": "other-model", "defaultReasoningEffort": "low",
                 "supportedReasoningEfforts": [{"reasoningEffort": "low"},
                                                {"reasoningEffort": "medium"}]},
            ]},
            "config/read": {"config": {"mcp_servers": {"foreign": {}, "levelcontrol": {}}}},
            "skills/list": {"data": [{"skills": [], "errors": []}]},
            "mcpServer/tool/call": {"structuredContent": {"ok": True}},
        }.get(method, {})
        if method == "turn/start":
            return [
                {"id": message["id"], "result": {"turn": {"id": "turn-1"}}},
                {"method": "item/agentMessage/delta", "params": {"delta": "你好"}},
                {
                    "id": 700,
                    "method": "item/commandExecution/requestApproval",
                    "params": {"command": "pwd"},
                },
                {
                    "method": "item/completed",
                    "params": {
                        "item": {
                            "type": "mcpToolCall",
                            "id": "tool-1",
                            "tool": "lc.edit",
                            "result": receipt,
                        }
                    },
                },
                {
                    "method": "item/completed",
                    "params": {"item": {"type": "agentMessage", "text": "完成"}},
                },
                {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
            ]
        return [{"id": message["id"], "result": data}] if "id" in message and method else []

    def popen(command, **kwargs):
        commands.extend(command)
        process = Process(handler)
        processes.append(process)
        return process

    monkeypatch.setattr(resident.subprocess, "Popen", popen)
    session = CodexSession(
        18870,
        workspace=tmp_path,
        data_dir=tmp_path / "data",
        project_id="codex-project",
        on_event=events.append,
    )
    session.agent.on_event = lambda event: (
        session.respond(event["request_id"], {"allow": False})
        if event["type"] == "approval"
        else events.append(event)
    )
    try:
        assert session.model_state()["effort"] == "max"
        output = session.turn("读取关卡")
        assert output["status"] == "completed" and output["model"] == "native-model"
        assert output["tools"][0]["receipt"] == receipt
        assert {"id": 700, "result": {"decision": "decline"}} in processes[0].messages
        start = next(item for item in processes[0].messages if item.get("method") == "thread/start")
        assert not {"model", "approvalPolicy", "sandbox"} & start["params"].keys()
        assert not any(value.startswith(("model=", "approval_policy=")) for value in commands)
        servers = start["params"]["config"]["mcp_servers"]
        assert "command" in servers["levelcontrol"] and not servers["foreign"]["enabled"]
        assert 'web_search="live"' in commands and 'features.view_image=true' in commands
        session.configure_model(AgentModelSelection("other-model", "medium"))
        assert session.turn("继续对话")["model"] == "other-model"
        requests = [item["params"] for item in processes[0].messages
                    if item.get("method") == "turn/start"]
        assert [(item["threadId"], item["model"], item["effort"]) for item in requests] == [
            ("thread-1", "native-model", "max"), ("thread-1", "other-model", "medium")]
    finally:
        session.close()


def test_native_configuration_keeps_model_and_permissions_with_extensions_off(tmp_path):
    command = arguments(18870, True, workspace=tmp_path, project_id="a", native=True)
    assert not {"--sandbox", "--ephemeral"} & set(command)
    assert not any(
        value.startswith(("model=", "approval_policy=")) for value in command
    )
    assert "features.plugins=false" in command
    enabled = arguments(18870, True, workspace=tmp_path, native=True,
                        capabilities=AgentCapabilities(True, True, True, True))
    assert "features.plugins=true" in enabled
    assert "features.apps=false" not in enabled
