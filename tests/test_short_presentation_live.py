import copy
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
    assert os.environ.get("LC_PORT")
    client = Client(int(os.environ["LC_PORT"]))
    assert client._request("/health").get("short_presentation")
    client.edit("presentation cancel")
    yield client
    client.edit("presentation cancel")


def structure():
    return {"intent": "new_structure", "anchor": {"at": [25, 25, 0]}, "nodes": [
        {"id": "floor", "asset": "blockout/floor", "size": [8, 8, 0.2], "at": [0, 0, 0.1]},
        {"id": "walls", "kind": "wall", "size": [8, 8, 3], "at": [0, 0, 1.5]},
    ]}


def pending(service, client, plan):
    result = []
    errors = []

    def build():
        try:
            result.append(service.build(plan, "show-" + uuid.uuid4().hex))
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=build)
    thread.start()
    deadline = time.perf_counter() + 3
    while time.perf_counter() < deadline:
        state = client.read("presentation")
        if state["commit_waiting"]:
            assert not state["instant"], state
            return thread, result, errors, state
        time.sleep(0.01)
    thread.join(10)
    raise AssertionError((result, errors))


def complete(thread, results, errors):
    thread.join(8)
    assert not thread.is_alive() and not errors, errors
    result = results[0]
    assert result["status"] == "applied" and result["presentation_ready"], result
    return result


def test_new_extension_and_edit_use_different_reveals(client, tmp_path):
    service = BuildService(client, tmp_path)
    original_count = client.read("objects * --count")["count"]
    value = structure()
    thread, results, errors, state = pending(service, client, value)
    assert state["camera"] == "player" and state["music"] == "none"
    first = complete(thread, results, errors)
    assert first["presentation"]["duration_s"] == 4
    assert 3.8 <= first["presentation"]["elapsed_s"] < 5
    version = client.read("presentation")["revision"]
    assert service.build(value, first["req_id"])["tx_id"] == first["tx_id"]
    assert client.read("presentation")["revision"] == version
    changed = copy.deepcopy(value)
    changed.update(scene=first["scene"], expected_version=first["version"], intent="extension")
    changed["nodes"].append({"id": "tower", "asset": "blockout/box",
                             "size": [2, 2, 5], "at": [6, 0, 2.5]})
    thread, results, errors, state = pending(service, client, changed)
    assert state["animated_count"] == 1
    assert state["preview_count"] <= 1
    assert client.read("objects * --count")["count"] == original_count + first["entity_count"]
    second = complete(thread, results, errors)
    assert second["presentation"]["duration_s"] == 3
    changed.update(expected_version=second["version"], intent="edit")
    changed["nodes"][-1]["at"][0] = 7
    third = service.build(changed, "edit-" + uuid.uuid4().hex)
    assert third["status"] == "applied" and third["presentation"]["duration_s"] == 0
    assert third["timings"]["tool_total_s"] < 1
    for result in (third, second, first):
        assert client.edit(f"undo --tx-id {result['tx_id']}")["status"] == "applied"
    assert client.read("objects * --count")["count"] == original_count


@pytest.mark.parametrize("action", ["skip", "cancel"])
def test_skip_commits_but_cancel_does_not(client, tmp_path, action):
    before = client.read("objects * --count")["count"]
    thread, results, errors, state = pending(BuildService(client, tmp_path), client, structure())
    if action == "skip":
        stale = client.edit(f"presentation skip --revision {state['revision'] - 1}")
        assert stale["status"] == "rejected"
    assert client.edit(f"presentation {action}")["status"] == "applied"
    thread.join(3)
    assert not thread.is_alive() and not errors
    result = results[0]
    if action == "skip":
        assert result["status"] == "applied" and result["presentation_ready"]
        assert client.read("presentation")["skip_reason"] == "requested"
        assert client.edit(f"undo --tx-id {result['tx_id']}")["status"] == "applied"
    else:
        assert result["status"] == "rejected"
    assert client.read("objects * --count")["count"] == before


def test_recording_hold_skips_auto_show_but_preserves_commit_lease(client, tmp_path):
    service = BuildService(client, tmp_path)
    thread, results, errors, _ = pending(service, client, structure())
    client.edit("presentation hold --lease-id speech-test")
    thread.join(2)
    assert not thread.is_alive() and not errors
    result = results[0]
    assert result["status"] == "scheduled"
    state = client.read("presentation")
    assert state["commit_waiting"] and state["commit_held"] and state["instant"]
    assert state["skip_reason"] == "input_held"
    client.edit("presentation resume --lease-id speech-test")
    deadline = time.perf_counter() + 3
    while client.receipt(result["req_id"])["status"] == "scheduled":
        assert time.perf_counter() < deadline
        time.sleep(0.01)
    final = service.finalize_receipt(result["req_id"], client.receipt(result["req_id"]))
    assert final["presentation_ready"]
    assert client.edit(f"undo --tx-id {final['tx_id']}")["status"] == "applied"


def test_explicit_show_and_disabled_mode(client, tmp_path):
    service = BuildService(client, tmp_path)
    value = structure()
    value["presentation"] = {"mode": "off"}
    direct = service.build(value, uuid.uuid4().hex)
    assert direct["status"] == "applied" and direct["presentation"]["duration_s"] == 0
    client.edit(f"undo --tx-id {direct['tx_id']}")
    value["presentation"] = {"mode": "on", "camera": "overview"}
    thread, results, errors, state = pending(service, client, value)
    assert state["camera"] == "overview" and not state["auto_skip"]
    result = complete(thread, results, errors)
    assert result["presentation"]["duration_s"] == 5
    assert 4.8 <= result["presentation"]["elapsed_s"] < 6
    assert client.read("presentation")["camera"] == "player"
    client.edit(f"undo --tx-id {result['tx_id']}")


def test_unknown_animated_id_is_rejected_before_preview(client):
    result = client.preview({"source": "bad-preview", "revision": 999999,
                             "entities": [], "timeline": [], "regions": [],
                             "animate_ids": ["missing"]})
    assert result["status"] == "rejected" and result["warnings"]
