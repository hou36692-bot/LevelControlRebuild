"""Real height-clipped previews, incremental handoff and rollback in a disposable game."""

import copy
import os
import time
import uuid

import pytest
from lc.client import Client
from lgg.build import BuildService
from lgg.stream import StreamService

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("LC_LIVE") != "1",
                                                reason="Disposable Shipping game required")]


@pytest.fixture
def game():
    assert os.environ.get("LC_PORT") == "18872", "Use the isolated growth test port"
    client = Client(18872)
    assert client.health().get("upward_growth")
    assert client.edit("presentation cancel")["status"] == "applied"
    yield client
    client.edit("presentation cancel")


def terminal(game, request_id, timeout=20):
    until = time.perf_counter() + timeout
    while time.perf_counter() < until:
        result = game.receipt(request_id)
        if result["status"] != "scheduled":
            return result
        time.sleep(.03)
    raise AssertionError(result)


def state_until(game, predicate, timeout=10):
    until = time.perf_counter() + timeout
    while time.perf_counter() < until:
        state = game.read("presentation")
        if predicate(state):
            return state
        time.sleep(.03)
    raise AssertionError(state)


def plan(game):
    return {"name": "growth-test", "expected_version": game.read("objects * --count")["version"],
            "anchor": {"at": [25, 25, 0]}, "modules": [
                {"id": "base", "label": "台基", "at": [0, 0, .2], "size": [8, 6, .4]},
                {"id": "body", "label": "柱廊", "relative_to": "base", "side": "above",
                 "size": [8, 6, 3], "depends_on": ["base"]},
                {"id": "roof", "label": "屋顶", "relative_to": "body", "side": "above",
                 "size": [8, 6, .5], "depends_on": ["body"]}],
            "environment": {"preset": "noon"}}


def delivery(identity):
    size = {"base": [8, 6, .4], "body": [.5, .5, 3], "roof": [8, 6, .5]}[identity]
    node = {"id": "structure", "asset": "blockout/box", "size": size,
            "surface": "stone-limestone"}
    if identity == "body":
        node.update(at=[-3, -2, 0], repeat={"count": [4, 2, 1], "span": [6, 4, 0]})
    return {"id": identity, "nodes": [node]}


def test_modules_grow_monotonically_commit_once_and_undo(game, tmp_path):
    before = game.read("objects * --count")["count"]
    service = StreamService(game, tmp_path)
    build = service.plan(plan(game), uuid.uuid4().hex)["build_id"]
    service.module(build, delivery("base"), uuid.uuid4().hex)
    base = state_until(game, lambda s: any(g["id"] == "base" and g["progress"] >= 1
                                          for g in s["growth_groups"]))
    assert game.read("objects * --count")["count"] == before
    service.module(build, delivery("body"), uuid.uuid4().hex)
    midpoint = state_until(game, lambda s: any(g["id"] == "body" and .15 < g["progress"] < .9
                                              for g in s["growth_groups"]))
    assert next(g for g in midpoint["growth_groups"] if g["id"] == "base")["progress"] == 1
    assert midpoint["geometry_transform_updates"] == 0
    body = next(g for g in midpoint["growth_groups"] if g["id"] == "body")
    assert body["min_z_cm"] < body["height_cm"] < body["max_z_cm"]
    assert body["instance_count"] == body["prepared_count"] == 8
    result = service.module(build, delivery("roof"), uuid.uuid4().hex)
    receipt = terminal(game, result["req_id"])
    assert receipt["status"] == "applied", receipt
    final = state_until(game, lambda s: s["state"] == "completed")
    assert final["camera"] == "player" and final["music"] == "none"
    assert game.read("objects * --count")["count"] == before + 10
    recovered = BuildService(game, tmp_path).finalize_receipt(result["req_id"], receipt)
    assert recovered["construction_status"] == "applied" and recovered["presentation_ready"]
    assert game.edit(f"undo --tx-id {receipt['tx_id']}")["status"] == "applied"
    assert game.read("objects * --count")["count"] == before
    assert base["effect"] == "upward"


def test_cancel_rejects_late_module_without_committing(game, tmp_path):
    before = game.read("objects * --count")["count"]
    service = StreamService(game, tmp_path)
    build = service.plan(plan(game), uuid.uuid4().hex)["build_id"]
    service.module(build, delivery("base"), uuid.uuid4().hex)
    assert game.edit("presentation cancel")["status"] == "applied"
    with pytest.raises(ValueError, match="cancelled"):
        service.module(build, delivery("body"), uuid.uuid4().hex)
    assert game.read("objects * --count")["count"] == before


def test_skip_persists_across_deliveries_and_revisions_are_guarded(game, tmp_path):
    service = StreamService(game, tmp_path)
    build = service.plan(plan(game), uuid.uuid4().hex)["build_id"]
    service.module(build, delivery("base"), uuid.uuid4().hex)
    assert game.edit("presentation skip")["status"] == "applied"
    service.module(build, delivery("body"), uuid.uuid4().hex)
    assert game.read("presentation")["instant"]
    result = service.module(build, delivery("roof"), uuid.uuid4().hex)
    receipt = terminal(game, result["req_id"])
    assert receipt["status"] == "applied"
    state_until(game, lambda s: s["state"] == "completed")
    assert game.edit(f"undo --tx-id {receipt['tx_id']}")["status"] == "applied"


@pytest.mark.parametrize("surface", [None, "stone-limestone", "glass-clear", "metal-steel"])
def test_material_families_share_the_same_height_rule(game, surface):
    state = game.read("presentation")
    entity = {"id": "material-test", "template": "blockout/box", "group": "material",
              "transform": {"position_cm": [3000, 3000, 200], "size_cm": [100, 100, 400],
                            "rotation_deg": [0, 0, 0]}}
    if surface:
        entity["surface"] = {"preset": surface}
    body = {"source": "material-test", "revision": state["revision"] + 1,
            "entities": [entity], "regions": [],
            "timeline": [{"id": entity["id"], "start_s": 0, "duration_s": 1}]}
    assert game.preview(body)["status"] == "applied"
    first = state_until(game, lambda s: s["growth_groups"][0]["progress"] > .1)
    second = state_until(game, lambda s: s["growth_groups"][0]["progress"] > .6)
    assert second["growth_groups"][0]["height_cm"] > first["growth_groups"][0]["height_cm"]
    invalid = copy.deepcopy(body)
    invalid["incremental"] = True
    invalid["source"] = "other-source"
    invalid["revision"] += 1
    assert game.preview(invalid)["status"] == "rejected"
    longer = copy.deepcopy(body)
    longer["revision"] += 1
    longer["timeline"][0]["duration_s"] = 12
    assert game.preview(longer)["status"] == "applied"
