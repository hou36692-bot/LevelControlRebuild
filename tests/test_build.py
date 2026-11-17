import copy

import pytest
from lgg.build import compile_plan, offsets

TEMPLATES = [{"id": "blockout/box", "size_cm": [100, 100, 100]},
             {"id": "blockout/floor", "size_cm": [100, 100, 20]}]
CATALOG = {"props": [{"id": "prop/table", "size_cm": [120, 80, 80],
                      "surface": "wood-oak"}], "prefabs": []}
PLAYER = {"feet_cm": [1000, 2000, 0], "forward": [1, 0, 0]}


def plan():
    return {"nodes": [{"id": "shelf", "at": [1, 0, 0], "yaw": 90,
                       "repeat": {"count": 2, "step": [2, 0, 0]},
                       "surface": {"tint": [0.1, 0.5, 0.8]}, "parts": [
                           {"id": "deck", "asset": "blockout/box", "size": [1, 2, 0.1],
                            "at": [0, 1, 0.5],
                            "repeat": {"count": 3, "step": [0, 0, 0.5]}},
                           {"id": "table", "asset": "prop/table", "at": [0, 0, 0.4]}]}]}


def compile(value, slots=None, player=PLAYER, previous_anchor=None):
    return compile_plan(value, "test", CATALOG, TEMPLATES,
                        {} if slots is None else slots, player, previous_anchor)


def test_nested_arrays_local_rotation_material_and_forward_anchor():
    entities, anchor = compile(plan())
    assert len(entities) == 8
    assert anchor["yaw"] == -90
    assert entities[0]["transform"]["rotation_deg"] == [0, 0, 0]
    assert entities[0]["transform"]["position_cm"][2] == 50
    assert entities[0]["surface"]["tint"] == [0.1, 0.5, 0.8]
    assert entities[3]["surface"]["preset"] == "wood-oak"
    assert len({e["id"] for e in entities}) == 8
    assert compile(plan()) == compile(copy.deepcopy(plan()))


def test_repeated_grid_indices_do_not_shift_other_ids_or_update_anchor():
    value = plan()
    slots = {}
    first, anchor = compile(value, slots)
    old_slots = dict(slots)
    value["nodes"][0]["parts"][0]["repeat"]["count"] = 5
    second, second_anchor = compile(value, slots, {**PLAYER, "feet_cm": [9000, 0, 0]}, anchor)
    assert second_anchor == anchor
    assert all(slots[k] == v for k, v in old_slots.items())
    assert {e["id"] for e in first} <= {e["id"] for e in second}
    a = dict(offsets({"count": [2, 2, 1], "step": [1, 2, 3]}))
    b = dict(offsets({"count": [4, 2, 1], "step": [1, 2, 3]}))
    assert all(b[k] == v for k, v in a.items())


def test_named_definition_and_constructor():
    value = {"anchor": {"at": [0, 0, 0], "yaw": 90},
             "definitions": {"tile": {"kind": "floor", "size": [4, 4, 0.2]}},
             "nodes": [{"id": "floor", "use": "tile"}]}
    entities, _ = compile(value)
    assert len(entities) == 4
    assert entities[0]["transform"]["position_cm"] == [100, -100, 0]


@pytest.mark.parametrize("node", [
    {"id": "bad", "asset": "unknown"},
    {"id": "bad", "asset": "blockout/box", "size": [1, 0, 1]},
    {"id": "bad", "asset": "blockout/box", "yaw": float("nan")},
    {"id": "bad", "asset": "blockout/box", "at": [1e5, 0, 0]},
    {"id": "bad", "asset": "blockout/box", "collision": "false"},
    {"id": "bad", "asset": "blockout/box", "repeat": {"count": True, "step": [1, 0, 0]}},
    {"id": "bad", "asset": "blockout/box", "repeat": {"count": [129, 129, 1],
                                                         "step": [1, 1, 0]}},
    {"id": "bad", "asset": "blockout/box", "parts": []},
    {"id": "bad", "parts": []},
    {"id": "bad", "use": "missing"},
    {"id": "bad", "kind": "unknown"},
    {"id": "bad", "asset": "blockout/box", "surfaces": {}},
])
def test_invalid_plan_rejected(node):
    with pytest.raises(ValueError):
        compile({"nodes": [node]})


def test_cycles_duplicate_paths_and_nested_expansion_budget():
    with pytest.raises(ValueError, match="cyclic"):
        compile({"definitions": {"loop": {"use": "loop"}},
                 "nodes": [{"id": "loop", "use": "loop"}]})
    with pytest.raises(ValueError, match="unique"):
        compile({"nodes": [plan()["nodes"][0]] * 2})
    with pytest.raises(ValueError, match="16384"):
        compile({"nodes": [{"id": "outer", "repeat": {"count": 200, "step": [1, 0, 0]},
                            "parts": [{"id": "inner", "asset": "blockout/box",
                                       "repeat": {"count": 100, "step": [0, 1, 0]}}]}]})


def test_model_schema_accepts_json_arrays_but_rejects_ambiguous_fields():
    from lgg.build_types import BuildPlan
    from pydantic import ValidationError

    value = plan()
    value["nodes"][0]["surface"] = "metal-steel"
    normalized = BuildPlan.model_validate(value).model_dump(mode="json", exclude_unset=True,
                                                           exclude_none=True)
    assert compile(normalized)[0][0]["surface"]["preset"] == "metal-steel"
    with pytest.raises(ValidationError):
        BuildPlan.model_validate({**value, "name": "bad_name"})
    with pytest.raises(ValidationError):
        BuildPlan.model_validate({**value, "environment": {"values": {"exposure": 1}}})
    value["nodes"][0]["yaw"] = True
    with pytest.raises(ValidationError):
        BuildPlan.model_validate(value)


def test_api_adapter_passes_typed_plan_through_real_mcp_registry(monkeypatch):
    from types import SimpleNamespace

    from lc import mcp_server
    from lc.api_agent import ApiAgent

    received = []
    monkeypatch.setattr(mcp_server, "BuildService", lambda client: SimpleNamespace(
        build=lambda document, request: received.append(document) or {
            "req_id": request, "tx_id": "tx-test", "status": "applied"}))
    agent = ApiAgent.__new__(ApiAgent)
    agent.client = SimpleNamespace(port=18858)
    previous = mcp_server.client
    try:
        result = agent.invoke_tool("lgg_build", {"plan": plan(), "request_id": "test"})
    finally:
        mcp_server.client = previous
    assert result["structuredContent"]["status"] == "applied"
    assert received == [plan()]
