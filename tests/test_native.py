import base64
import hashlib
import json

import pytest
from lc.client import Client
from lc.native import RuntimeSession, tool_result
from lgg.build import BuildService


def test_bound_clients_do_not_share_connection_or_design_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_BIND_PORT", "18871")
    left = Client(18871, data_dir=tmp_path / "left", workspace=tmp_path / "workspace")
    monkeypatch.setattr(left, "_request", lambda *a, **k: {"ok": True, "port": 18871})
    left.connect("--port 18871")
    monkeypatch.delenv("LC_BIND_PORT")
    right = Client(18872, data_dir=tmp_path / "right")
    assert left.connection_file != right.connection_file
    assert not right.connection_file.exists()
    assert BuildService(left).directory != BuildService(right).directory
    with pytest.raises(ValueError, match="cannot switch"):
        left.connect("--port 18872")


def test_workspace_batch_boundary(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = Client(18871, workspace=workspace)
    (workspace / "ok.json").write_text(json.dumps({"operations": []}))
    monkeypatch.setattr(client, "_write", lambda *a, **k: {"status": "applied"})
    assert client.edit("apply --path ok.json")["status"] == "applied"
    with pytest.raises(ValueError, match="workspace"):
        client.edit("apply --path ../outside.json")


def test_game_restart_requires_new_context(tmp_path, monkeypatch):
    client = Client(18871, data_dir=tmp_path)
    session = RuntimeSession(client)
    identity = ["first"]
    monkeypatch.setattr(session, "current_session", lambda: identity[0])
    session.check()
    identity[0] = "second"
    with pytest.raises(ConnectionError, match="lc.context"):
        session.check()
    monkeypatch.setattr(client, "health", lambda: {
        "ok": True, "port": 18871, "protocol": 4, "semantic_core": True})
    monkeypatch.setattr(client, "read", lambda _: {"version": 9})
    monkeypatch.setattr(client, "query", lambda _: {"status": "ok", "version": 9})
    first = session.context()
    second = session.context()
    assert first["session_id"] == "second" and first["world_version"] == 9
    assert first["request_id"] != second["request_id"]
    session.check()


def test_receipt_recovery_finalizes_only_known_requests(tmp_path, monkeypatch):
    client = Client(18871, data_dir=tmp_path)
    request = "pending-build"
    path = BuildService(client).directory / "requests" / (
        hashlib.sha256(request.encode()).hexdigest() + ".json")
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    monkeypatch.setattr(client, "receipt", lambda _: {"status": "applied", "req_id": request})
    seen = []
    monkeypatch.setattr(BuildService, "finalize_receipt",
                        lambda self, rid, value: seen.append(rid) or value)
    assert RuntimeSession(client).receipt(request)["status"] == "applied"
    assert seen == [request]
    monkeypatch.setattr(client, "receipt", lambda _: {"status": "unknown"})
    assert RuntimeSession(client).receipt(request)["status"] == "unknown"
    assert seen == [request]


def test_mcp_image_keeps_receipt_and_actual_pixels(tmp_path):
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jFuoAAAAASUVORK5CYII=")
    path = tmp_path / "capture.png"
    path.write_bytes(png)
    receipt = {"status": "applied", "req_id": "photo", "path": str(path)}
    result = tool_result(receipt)
    assert result.structuredContent == receipt
    assert base64.b64decode(result.content[1].data) == png
    assert path.read_bytes() == png
    assert len(tool_result({**receipt, "status": "scheduled"}).content) == 1
    assert tool_result({"status": "unknown"}).isError


def test_bad_image_does_not_destroy_successful_receipt(tmp_path):
    path = tmp_path / "bad.png"
    path.write_text("not a png")
    result = tool_result({"status": "applied", "path": str(path)})
    assert not result.isError
    assert all(item.type == "text" for item in result.content)
    assert "Invalid PNG" in result.content[-1].text
