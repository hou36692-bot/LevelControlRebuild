"""Fast builds use the same isolated runtime and transactional authority as legacy edits."""

import copy
import json
import os
import threading
import time
import uuid

import pytest
from lc.client import Client
from lgg.build import BuildService

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("LC_LIVE") != "1",
                                                reason="Requires disposable runtime")]


@pytest.fixture
def client():
    assert os.environ.get("LC_PORT"), "Select an isolated test port"
    return Client(int(os.environ["LC_PORT"]))


def plan():
    return {"name": "fast-test", "anchor": {"at": [25, 25, 0]},
            "nodes": [{"id": "floor", "asset": "blockout/floor", "size": [12, 12, 0.2],
                       "at": [0, 0, 0.1], "surface": {"preset": "stone-limestone"}},
                      {"id": "boxes", "asset": "blockout/box", "size": [0.3, 0.3, 0.6],
                       "at": [-4, -4, 0.5], "repeat": {"count": [10, 10, 1],
                                                        "step": [0.8, 0.8, 0]},
                       "surface": {"tint": [0.2, 0.6, 0.8]}}],
            "environment": {"preset": "noon", "values": {"sky_intensity": 2}}}


def test_fast_build_replay_update_undo_and_environment(client, tmp_path):
    service = BuildService(client, tmp_path)
    client.edit("presentation cancel")
    before = client.read("environment")
    count = client.read("objects * --count")["count"]
    value, request = plan(), "fast-" + uuid.uuid4().hex
    result = service.build(value, request)
    assert result["status"] == "applied" and result["presentation_ready"], result
    assert result["entity_count"] == 101
    assert result["max_apply_frame_ms"] <= 2
    assert result["timings"]["tool_total_s"] < 2
    assert client.read("objects * --count")["count"] == count + 101
    assert client.read("environment")["committed"]["sky_intensity"] == 2
    replay = service.build(value, request)
    assert replay["tx_id"] == result["tx_id"] and replay["version"] == result["version"]
    with pytest.raises(ValueError, match="different plan"):
        service.build({**value, "name": "other"}, request)
    ids = {e["id"] for e in client.read(f"objects {result['scene']}-* --limit 1000")["entities"]}
    changed = copy.deepcopy(value)
    changed.update(scene=result["scene"], expected_version=result["version"])
    changed["nodes"][1]["repeat"]["count"] = [11, 10, 1]
    updated = service.build(changed, "fast-" + uuid.uuid4().hex)
    assert updated["status"] == "applied" and updated["entity_count"] == 111, updated
    updated_entities = client.read(f"objects {result['scene']}-* --limit 1000")["entities"]
    new_ids = {e["id"] for e in updated_entities}
    assert ids < new_ids
    service.build(value, request)
    saved = json.loads((tmp_path / "scenes" / f"{result['scene']}.json").read_text("utf-8"))
    assert saved["committed_version"] == updated["version"]
    assert len(saved["slots"]) == 111
    assert client.edit(f"undo --tx-id {updated['tx_id']}")["status"] == "applied"
    assert client.edit(f"undo --tx-id {result['tx_id']}")["status"] == "applied"
    assert client.read("objects * --count")["count"] == count
    assert client.read("environment")["committed"] == before["committed"]
    assert not service.build(value, request)["presentation_ready"]
    assert client.read("consistency")["ok"]


@pytest.mark.parametrize("patch", [
    {"environment": {"preset": "not-a-preset"}},
    {"environment": {"values": {"sun_intensity": -1}}},
    {"environment": {"action": "save"}},
    {"expected_version": -1},
    {"expected_version": 9999999},
])
def test_invalid_combined_build_keeps_world(client, tmp_path, patch):
    client.edit("presentation cancel")
    before = client.read("consistency")
    environment = client.read("environment")["committed"]
    try:
        result = BuildService(client, tmp_path).build({**plan(), **patch}, uuid.uuid4().hex)
        assert result["status"] == "rejected", result
    except ValueError:
        pass
    assert client.read("consistency") == before
    assert client.read("environment")["committed"] == environment
    assert not client.read("presentation")["commit_waiting"]


def test_cancel_held_fast_build_before_commit(client, tmp_path):
    client.edit("presentation cancel")
    before = client.read("consistency")
    client.edit("presentation hold --lease-id fast-test")
    results = []
    worker = threading.Thread(target=lambda: results.append(
        BuildService(client, tmp_path).build(plan(), uuid.uuid4().hex)))
    worker.start()
    try:
        deadline = time.perf_counter() + 5
        while time.perf_counter() < deadline:
            if client.read("presentation")["commit_waiting"]:
                break
            time.sleep(0.01)
        assert client.read("presentation")["commit_waiting"]
        worker.join(2)
        assert not worker.is_alive(), "Held construction must not block the model turn"
        assert results[0]["status"] == "scheduled"
        client.edit("presentation cancel")
        deadline = time.perf_counter() + 2
        while client.receipt(results[0]["req_id"])["status"] == "scheduled":
            assert time.perf_counter() < deadline
            time.sleep(0.01)
        assert client.receipt(results[0]["req_id"])["status"] == "rejected"
        assert client.read("consistency") == before
    finally:
        client.edit("presentation resume --lease-id fast-test")
        worker.join(5)


def test_held_build_finalizes_after_resume(client, tmp_path):
    client.edit("presentation cancel")
    service = BuildService(client, tmp_path)
    request = "held-" + uuid.uuid4().hex
    client.edit("presentation hold --lease-id voice-test")
    try:
        result = service.build(plan(), request)
        assert result["status"] == "scheduled" and not result["presentation_ready"]
        assert result["timings"]["tool_total_s"] < 2
        client.edit("presentation resume --lease-id voice-test")
        deadline = time.perf_counter() + 5
        receipt = client.receipt(request)
        while receipt["status"] == "scheduled":
            assert time.perf_counter() < deadline
            time.sleep(0.01)
            receipt = client.receipt(request)
        final = service.finalize_receipt(request, receipt)
        assert final["status"] == "applied" and final["presentation_ready"], final
        assert (tmp_path / "scenes" / f"{final['scene']}.json").exists()
        assert client.edit(f"undo --tx-id {final['tx_id']}")["status"] == "applied"
    finally:
        client.edit("presentation resume --lease-id voice-test")
