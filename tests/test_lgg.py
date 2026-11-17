import copy
import json

import pytest
from lc.command import parse
from lgg import schema
from lgg.compiler import compile_module, delta, from_y_up, region
from lgg.service import DesignService
from presenter.camera_rules import shot
from presenter.timeline import timeline

SITE = {"center_m": [20, 0, 5], "size_m": [30, 30, 10]}


def module(kind="fountain", identifier=1):
    return schema.module(
        {"id": identifier, "name": kind, "kind": kind, "center_m": [20, 0, 2], "size_m": [8, 8, 4]},
        SITE,
    )


@pytest.mark.parametrize("kind", sorted(schema.KINDS))
def test_six_constructors_deterministic_and_bounded(kind):
    design = module(kind)
    if kind == "stairs":
        design["params"] = {"steps": 10}
    compiled = compile_module("court", design)
    assert compiled == compile_module("court", copy.deepcopy(design))
    assert len({e["id"] for e in compiled}) == len(compiled)
    assert all(e["source"] == "court" for e in compiled)
    assert all(min(e["transform"]["size_cm"]) > 0 for e in compiled)
    assert delta(compiled, compiled) == {"create": [], "update": [], "delete": []}


def test_scale_changes_only_same_module_ids():
    initial = module()
    smaller = copy.deepcopy(initial)
    smaller["size_m"] = [4, 4, 3]
    before, after = (compile_module("court", m) for m in [initial, smaller])
    changes = delta(before, after)
    assert not changes["create"] and not changes["delete"]
    assert set(changes["update"]) == {e["id"] for e in before}
    floor = compile_module("court", module("floor", 2))
    assert delta(before + floor, after + floor) == changes


def test_units_rotation_and_explicit_y_up_import():
    design = module("colonnade")
    design["params"] = {"count": 2, "diameter_m": 1}
    design["yaw_deg"] = 90
    entities = compile_module("court", design)
    assert entities[0]["transform"]["position_cm"] == [2000, -350, 200]
    assert entities[0]["transform"]["size_cm"] == [100, 100, 400]
    assert from_y_up([1, 2, 3]) == [1, -3, 2]
    assert region(design)["center_cm"] == [2000, 0, 200]


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"center_m": [0, 0, 0], "size_m": [1, -1, 1]},
        {"center_m": [float("nan"), 0, 0], "size_m": [1, 1, 1]},
    ],
)
def test_invalid_site(bad):
    with pytest.raises(ValueError):
        schema.site(bad)


def test_reject_unknown_fields_duplicate_ids_and_outside():
    design = module()
    with pytest.raises(ValueError):
        schema.coarse([design, design], SITE)
    design["center_m"] = [100, 0, 0]
    with pytest.raises(ValueError, match="exceed"):
        schema.module(design, SITE)
    with pytest.raises(ValueError):
        schema.module({**module(), "params": {"mystery": 1}}, SITE)


def test_timing_exactly_matches_entities():
    entities = compile_module("court", module())
    plan = timeline(entities, 8)
    assert [p["id"] for p in plan] == [e["id"] for e in entities]
    assert max(p["start_s"] + p["duration_s"] for p in plan) == pytest.approx(8)


class FakeClient:
    port = 18844

    def __init__(self):
        self.revision = 0
        self.published = []

    def read(self, command):
        return {"revision": self.revision, "version": 0}

    def edit(self, command):
        return {"status": "applied"}

    def help(self, command):
        return "help"

    def preview(self, body, request_id):
        self.revision = body["revision"]
        self.published.append(body)
        return {"req_id": request_id, "tx_id": "preview", "status": "applied"}


def draft(service, n, stage, payload, extra=""):
    return service.invoke("lgg_draft", f"#{n} {stage} --json '{json.dumps(payload)}' {extra}")


def test_stage_order_retry_and_complete_are_enforced(tmp_path):
    client = FakeClient()
    service = DesignService(client, tmp_path / "design.json")
    service.invoke("lgg_card", "init --scene court")
    with pytest.raises(ValueError, match="site and coarse"):
        draft(service, 1, "module", {}, "--id 1")
    draft(service, 1, "site", SITE)
    result = draft(service, 2, "coarse", [module()])
    assert draft(service, 2, "coarse", [module()]) == result
    assert len(client.published) == 2
    assert not client.published[-1]["entities"]
    with pytest.raises(ValueError, match="reused"):
        draft(service, 2, "coarse", [module("floor")])
    result = draft(service, 3, "module", {}, "--id 1 --complete")
    assert result["complete"] and result["entity_count"] > 0
    assert service.load()["sequence"] == 3


@pytest.mark.parametrize(
    "tool,command,path",
    [
        ("read", "presentation", "/v1/presentation"),
        ("read", "events --since 3", "/v1/events"),
        ("edit", "checkpoint save before", "/v1/checkpoints"),
        ("edit", "restore --checkpoint before", "/v1/checkpoints"),
        ("edit", "presentation cancel", "/v1/presentation"),
        ("lgg_run", "play --duration 12", "lgg_run"),
    ],
)
def test_command_routes(tool, command, path):
    assert parse(tool, command).path == path


def test_compact_commands_match_json():
    compact = parse("lgg_draft", "#2 coarse --module 1 pool fountain 20,0,2 5,5,4")
    payload = [
        {"id": 1, "name": "pool", "kind": "fountain", "center_m": [20, 0, 2], "size_m": [5, 5, 4]}
    ]
    full = parse("lgg_draft", f"#2 coarse --json '{json.dumps(payload)}'")
    assert compact.data == full.data
    assert parse("lgg_draft", "#3 module --id 1").data["payload"] == {}
    assert parse("lgg_draft", "#4 module --id 1 --size 3,3,4").data["payload"] == {
        "size_m": [3, 3, 4]
    }
    with pytest.raises(ValueError, match="not both"):
        parse("lgg_draft", "#4 module --id 1 --size 3,3,4 --json '{}'")


def test_floor_budget_and_constructor_rejections():
    with pytest.raises(ValueError, match="128"):
        compile_module(
            "court", {**module("floor"), "size_m": [20, 20, 0.2], "params": {"tile_m": 0.1}}
        )


@pytest.mark.parametrize(
    "kind,params",
    [
        ("fountain", {"segments": 7}),
        ("stairs", {"steps": 1}),
        ("doorway", {"opening_m": 10}),
        ("wall", {"sides": ["north", "north"]}),
        ("colonnade", {"diameter_m": 7}),
    ],
)
def test_invalid_constructor_parameters(kind, params):
    with pytest.raises(ValueError):
        compile_module("court", {**module(kind), "params": params})


@pytest.mark.parametrize("size", [[2400, 2000, 800], [100, 100, 3000], [3000, 200, 300]])
def test_camera_frames_every_corner(size):
    import itertools
    import math

    position = shot({"center_cm": [0, 0, 0], "size_cm": size}, 0.9, 0.55)["position_cm"]
    distance = math.sqrt(sum(x * x for x in position))
    forward = [-x / distance for x in position]
    right = [math.sqrt(0.5), -math.sqrt(0.5), 0]
    up = [
        right[1] * forward[2],
        -right[0] * forward[2],
        right[0] * forward[1] - right[1] * forward[0],
    ]
    for signs in itertools.product([-1, 1], repeat=3):
        offset = [s * d / 2 - p for s, d, p in zip(signs, size, position, strict=True)]
        depth = sum(a * b for a, b in zip(offset, forward, strict=True))
        assert depth > 0
        assert abs(sum(a * b for a, b in zip(offset, right, strict=True))) / depth < math.tan(
            math.radians(32.5)
        )
        assert abs(sum(a * b for a, b in zip(offset, up, strict=True))) / depth < math.tan(
            math.radians(32.5)
        ) / (16 / 9)
