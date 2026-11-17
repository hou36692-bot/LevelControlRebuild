"""W2/W3 integration against an explicitly disposable rendered game."""

import json
import os
import time
import uuid

import pytest
from lc.client import ROOT, Client
from lgg.compiler import compile_card, delta
from lgg.service import DesignService

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("LC_LIVE") != "1", reason="Set LC_LIVE=1"),
]

SITE = {"center_m": [18, 0, 4], "size_m": [24, 20, 8]}
MODULES = [
    dict(id=1, name="ground", kind="floor", center_m=[18, 0, 0.1], size_m=[24, 20, 0.2]),
    dict(id=2, name="enclosure", kind="wall", center_m=[18, 0, 2.2], size_m=[24, 20, 4]),
    dict(id=3, name="fountain", kind="fountain", center_m=[18, 0, 1.4], size_m=[5, 5, 2.4]),
    dict(id=4, name="colonnade", kind="colonnade", center_m=[16, 7, 1.7], size_m=[8, 1, 3]),
    dict(id=5, name="gate", kind="doorway", center_m=[12, -7, 1.7], size_m=[4, 0.5, 3]),
    dict(id=6, name="steps", kind="stairs", center_m=[10, -4, 1], size_m=[4, 2, 1.6]),
]


def draft(service, number, stage, payload, suffix=""):
    result = service.invoke(
        "lgg_draft", f"#{number} {stage} --json '{json.dumps(payload)}' {suffix}"
    )
    assert result["status"] == "applied", result
    return result


def make_design(client, scene="court"):
    service = DesignService(client)
    service.invoke("lgg_card", f"init --scene {scene}")
    draft(service, 1, "site", SITE)
    draft(service, 2, "coarse", MODULES)
    for i in range(1, 7):
        draft(service, i + 2, "module", {}, f"--id {i}" + (" --complete" if i == 6 else ""))
    return service


def terminal(client, request, timeout=30):
    started = time.perf_counter()
    while True:
        receipt = client.receipt(request)
        if receipt["status"] != "scheduled":
            return receipt
        assert time.perf_counter() - started < timeout, receipt
        time.sleep(0.02)


def completed(client):
    deadline = time.perf_counter() + 10
    while time.perf_counter() < deadline:
        state = client.read("presentation")
        if state["state"] == "completed":
            return state
        time.sleep(0.02)
    raise AssertionError(state)


def test_preview_interrupt_reconcile_checkpoint_and_undo():
    client = Client(port=int(os.getenv("LC_PORT", "18846")))
    assert client.read("objects * --count")["count"] == 0, "Use an empty disposable database"
    name = "empty-" + uuid.uuid4().hex
    save = client.edit(f"checkpoint save {name}")
    assert save["status"] == "applied", save
    assert client.edit(f"checkpoint save {name}")["status"] == "rejected"
    assert client.edit("restore --checkpoint missing")["status"] == "rejected"
    service = make_design(client)
    original = compile_card(service.load())
    assert client.read("objects * --count")["count"] == 0
    pending = service.invoke("lgg_run", "play --duration 6")
    assert pending["status"] == "scheduled", pending
    assert service.invoke("lgg_run", "play") == pending
    time.sleep(0.25)
    assert client.read("presentation")["preview_count"] > 0
    assert client.read("objects * --count")["count"] == 0
    revision = client.read("presentation")["revision"]
    invalid = client.preview(
        {"source": "court", "revision": revision, "regions": [], "entities": [], "timeline": []}
    )
    assert invalid["status"] == "rejected"
    assert client.read("presentation")["revision"] == revision
    modified = draft(service, 9, "module", {"size_m": [3, 3, 2.4]}, "--id 3 --complete")
    assert modified["delta"] == {"create": 0, "update": 19, "delete": 0}
    assert client.receipt(pending["req_id"])["status"] == "rejected"
    assert client.read("consistency")["memory_count"] == 0
    queued = service.invoke("lgg_run", "play --duration 3")
    applied = terminal(client, queued["req_id"])
    assert applied["status"] == "applied", applied
    assert applied["tx_id"] == queued["tx_id"]
    assert applied["applied"] == len(original)
    state = completed(client)
    assert state["preview_count"] == 0 and state["camera"] == "player"
    assert client.read("consistency")["ok"]
    actual = client.read("objects court-* --limit 16384")["entities"]
    assert not any(delta(actual, compile_card(service.load())).values())
    baseline = "built-" + uuid.uuid4().hex
    assert client.edit(f"checkpoint save {baseline}")["status"] == "applied"
    draft(service, 10, "module", {"size_m": [4, 4, 2.4]}, "--id 3 --complete")
    changed = terminal(client, service.invoke("lgg_run", "play --duration 1")["req_id"])
    assert changed["status"] == "applied" and changed["applied"] == 19, changed
    after = client.read("objects court-* --limit 16384")["entities"]
    before_versions = {e["id"]: e["version"] for e in actual}
    assert all(e["version"] == before_versions[e["id"]] for e in after if e["group"] != "fountain")
    assert client.edit(f"restore --checkpoint {baseline}")["status"] == "applied"
    restored = client.read("objects court-* --limit 16384")["entities"]
    assert not any(delta(actual, restored).values())
    assert client.edit("undo --last")["status"] == "applied"
    assert not any(delta(after, client.read("objects court-* --limit 16384")["entities"]).values())
    assert client.edit(f"restore --checkpoint {name}")["status"] == "applied"
    assert client.read("consistency")["memory_count"] == 0
    events = client.read("events --since 0")
    assert {
        "first_visible",
        "cancelled",
        "committed",
        "module_camera",
        "presentation_complete",
    } <= {e["type"] for e in events["events"]}
    report = {
        "presentation": state,
        "commit": applied,
        "changed": changed,
        "events": events,
        "consistent": client.read("consistency"),
    }
    path = ROOT / "tests/reports/w23-live.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    client.edit("presentation cancel")


def test_play_after_undo_rebuilds_current_world():
    client = Client(port=int(os.getenv("LC_PORT", "18846")))
    assert client.read("objects * --count")["count"] == 0
    service = make_design(client, "repeat-court")
    first = terminal(client, service.invoke("lgg_run", "play --duration 1")["req_id"])
    assert first["status"] == "applied"
    assert client.edit("undo --last")["status"] == "applied"
    assert not service.invoke("lgg_run", "status")["commit_version_current"]
    second = terminal(client, service.invoke("lgg_run", "play --duration 1")["req_id"])
    assert second["status"] == "applied" and second["req_id"] != first["req_id"]
    assert second["applied"] == first["applied"]
    assert client.edit("undo --last")["status"] == "applied"
    assert client.read("consistency")["memory_count"] == 0
    client.edit("presentation cancel")


def test_commit_conflict_hold_noop_and_ownership():
    client = Client(port=int(os.getenv("LC_PORT", "18846")))
    assert client.read("objects * --count")["count"] == 0
    source = "isolation"
    entity = {
        "id": "isolation-01-01-00",
        "template": "blockout/box",
        "source": source,
        "transform": {"position_cm": [2000, 0, 50], "size_cm": [100, 100, 100]},
    }

    def preview():
        revision = client.read("presentation")["revision"] + 1
        body = {
            "source": source,
            "revision": revision,
            "entities": [entity],
            "regions": [],
            "timeline": [{"id": entity["id"], "start_s": 0, "duration_s": 0.1}],
        }
        assert client.preview(body)["status"] == "applied"
        return {
            "source": source,
            "revision": revision,
            "entities": [entity],
            "expected_version": client.read("objects * --count")["version"],
            "mode": "reconcile",
        }

    body = preview()
    assert client.edit("presentation hold")["status"] == "applied"
    request = client.commit(body)
    time.sleep(0.3)
    assert client.receipt(request["req_id"])["status"] == "scheduled"
    assert client.read("consistency")["memory_count"] == 0
    unrelated = client.edit("apply create outside --at 30m,0m,1m --size 1m,1m,1m")
    assert unrelated["status"] == "applied"
    client.edit("presentation resume")
    assert terminal(client, request["req_id"])["status"] == "rejected"
    assert client.read("objects * --count")["count"] == 1
    assert client.edit("undo --last")["status"] == "applied"
    body = preview()
    applied = terminal(client, client.commit(body)["req_id"])
    assert applied["status"] == "applied" and applied["applied"] == 1
    assert client.read("consistency")["ok"]
    body = preview()
    noop = terminal(client, client.commit(body)["req_id"])
    assert noop["status"] == "applied" and noop["applied"] == 0
    assert noop["version"] == applied["version"]
    source = "other-owner"
    body = preview()
    conflict = terminal(client, client.commit(body)["req_id"])
    assert conflict["status"] == "rejected", conflict
    assert client.read("objects * --count")["count"] == 1
    assert client.edit("undo --last")["status"] == "applied"
    assert client.read("consistency")["memory_count"] == 0
    client.edit("presentation cancel")
