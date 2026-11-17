from types import SimpleNamespace

import pytest
from lc import mcp_server
from lc.api_agent import ApiAgent


def test_bound_mcp_cannot_reconnect_to_another_world(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_BIND_PORT", "18852")
    monkeypatch.setattr(mcp_server, "client", SimpleNamespace(port=18852))
    with pytest.raises(ValueError, match="bound to port 18852"):
        mcp_server.connect("--port 18844")


def test_connect_defaults_to_current_session(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setenv("LC_BIND_PORT", "18852")
    monkeypatch.setattr(mcp_server, "client", SimpleNamespace(
        port=18852, connect=lambda command: calls.append(command)))
    mcp_server.connect()
    assert calls == ["--port 18852"]


def test_api_agent_cannot_reconnect_to_another_world() -> None:
    agent = ApiAgent.__new__(ApiAgent)
    agent.client = SimpleNamespace(port=18852)
    result = agent.invoke_tool("lc.connect", {"command": "--port 18844"})
    assert result["isError"]
    assert "bound to port 18852" in result["content"][0]["text"]
