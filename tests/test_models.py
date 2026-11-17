import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from lc.agent import arguments, child_environment
from lc.models import (
    ModelSettings,
    Provider,
    api_request,
    fetch_models,
    preset,
    probe_model,
    protect_key,
    validate_url,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_keys_are_protected_outside_the_project_and_roundtrip(tmp_path):
    path = tmp_path / "model-settings.json"
    settings = ModelSettings(path)
    provider = preset("deepseek")
    provider.api_key = "sk-only-a-test-secret"
    settings.providers.append(provider)
    settings.active = provider.id
    settings.save()
    assert provider.api_key not in path.read_text("utf-8")
    restored = ModelSettings(path)
    assert restored.provider().api_key == provider.api_key
    assert restored.provider().model == "deepseek-flash"
    assert provider.api_key not in repr(restored.provider())
    assert protect_key(protect_key(provider.api_key), decrypt=True) == provider.api_key
    broken = json.loads(path.read_text("utf-8"))
    broken["active"] = "missing-provider"
    path.write_text(json.dumps(broken), "utf-8")
    assert ModelSettings(path).error
    assert json.loads(path.read_text("utf-8"))["active"] == "missing-provider"


@pytest.mark.parametrize("url", [
    "http://remote.example/v1", "https://user:secret@example.com/v1",
    "https://example.com/v1?key=secret", "https://example.com/v1#secret",
    "https://example.com/responses", "https://bad host/v1", "https://example.com:bad",
])
def test_provider_url_boundary(url):
    with pytest.raises(ValueError):
        validate_url(url)
    assert validate_url("http://127.0.0.1:29999/v1/") == "http://127.0.0.1:29999/v1"


def test_child_environment_is_isolated_and_effort_is_configurable(monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "parent-chat")
    monkeypatch.setenv("TCODEX_ACC_PRODUCT_CONFIG_PATH", "parent-config")
    monkeypatch.setenv("CODEX_HOME", "user-settings")
    monkeypatch.setenv("LC_MODEL_API_KEY", "private-api-key")
    environment = child_environment()
    assert "CODEX_THREAD_ID" not in environment
    assert "TCODEX_ACC_PRODUCT_CONFIG_PATH" not in environment
    assert "LC_MODEL_API_KEY" not in environment
    assert environment["CODEX_HOME"] == "user-settings"
    values = arguments(18844, True, "gpt-6-luna", "max")
    assert 'model_reasoning_effort="max"' in values


@pytest.fixture
def provider_server():
    class Handler(BaseHTTPRequestHandler):
        requests = []

        def log_message(self, *args):
            pass

        def send(self, data, status=200):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.requests.append((self.path, self.headers.get("Authorization"), None))
            if self.path == "/v1/models":
                self.send({"data": [{"id": "test-model"}, {"id": "test-model"}]})
            elif self.path == "/v1/redirect":
                self.send_response(302)
                self.send_header("Location", "https://other.invalid/models")
                self.end_headers()
            else:
                self.send({"error": self.headers.get("Authorization")}, 401)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.requests.append((self.path, self.headers.get("Authorization"), body))
            self.send({"output": [{"type": "function_call", "name": "levelcontrol_probe",
                                   "arguments": '{"value":"ok"}'}]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    provider = Provider("test", "Test", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                        api_key="sk-test-secret")
    try:
        yield provider, Handler.requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_model_discovery_and_harmless_tool_probe(provider_server):
    provider, requests = provider_server
    assert fetch_models(provider) == ["test-model"]
    assert "连接成功" in probe_model(provider, "test-model", "high")
    path, authorization, body = requests[-1]
    assert path == "/v1/responses" and authorization == "Bearer sk-test-secret"
    assert body["reasoning"] == {"effort": "high"}
    assert body["tools"][0]["name"] == "levelcontrol_probe"
    assert body["store"] is False
    with pytest.raises(ValueError) as error:
        api_request(provider, "/unauthorized")
    assert "HTTP 401" in str(error.value)
    assert provider.api_key not in str(error.value)
    with pytest.raises(ValueError, match="重定向"):
        api_request(provider, "/redirect")


def test_resident_sends_selected_model_and_effort_in_same_thread():
    from lc.resident import ResidentAgent

    agent = ResidentAgent.__new__(ResidentAgent)
    agent.thread_id = "unchanged-thread"
    agent.model = "chosen-model"
    agent.effort = "max"
    agent.provider = None
    agent._next_id = 0
    agent.text_history = []
    agent._id_lock = threading.Lock()
    agent._events = io.StringIO()
    agent.event_path = "test.jsonl"
    agent.on_event = lambda event: None
    sent = []
    agent._send = sent.append
    agent._receive = lambda: {"method": "turn/completed", "params": {
        "turn": {"status": "completed"}}}
    assert agent._turn("只回复 OK")["model"] == "chosen-model"
    assert sent[0]["params"]["threadId"] == "unchanged-thread"
    assert sent[0]["params"]["model"] == "chosen-model"
    assert sent[0]["params"]["effort"] == "max"
    agent.effort = ""
    agent._turn("只回复 OK")
    assert "effort" not in sent[1]["params"]
