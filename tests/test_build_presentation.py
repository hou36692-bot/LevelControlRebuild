import copy

import pytest
from lc.command import parse
from lgg.build_types import BuildPlan
from presenter import build_policy


def entity(identity, group, x=0):
    return {"id": identity, "template": "blockout/box", "group": group,
            "transform": {"position_cm": [x, 0, 100], "size_cm": [100, 100, 200],
                          "rotation_deg": [0, 0, 0]}}


def test_new_structure_only_and_audio_camera_require_explicit_request():
    scene = [entity("a", "floor"), entity("b", "walls")]
    policy = build_policy.select({"intent": "new_structure", "presentation": {
        "camera": "overview", "music": "architect-pulse"}}, [], scene)
    assert policy["duration_s"] == 4 and policy["reason"] == "new_structure"
    assert policy["camera"] == "player" and policy["music"] == "none"
    assert build_policy.select({}, [], scene)["duration_s"] == 0
    assert build_policy.select({"intent": "placement"}, [], scene)["duration_s"] == 0
    assert build_policy.select({"intent": "new_structure"}, [], scene, True)["duration_s"] == 0
    assert build_policy.select({"intent": "new_structure", "presentation": {"mode": "off"}},
                               [], scene)["duration_s"] == 0


def test_extension_reveals_only_new_independent_group():
    before = [entity("floor", "floor"), entity("walls", "walls")]
    tower = entity("tower", "tower", 500)
    policy = build_policy.select({"intent": "extension", "scene": "church"}, before,
                                  before + [tower])
    assert policy["duration_s"] == 3 and policy["animate_ids"] == ["tower"]
    region = {"center_cm": [0, 0, 100], "size_cm": [1000, 1000, 200]}
    payload = build_policy.preview(policy, before + [tower], region)
    assert [t["duration_s"] for t in payload["timeline"]] == [0, 0, 3]
    assert "cameras" not in payload and payload["finish_delay_s"] == 0
    tower["group"] = "walls"
    assert build_policy.select({"intent": "extension", "scene": "church"}, before,
                               before + [tower])["duration_s"] == 0


def test_independent_extension_can_have_its_own_scene_and_one_group():
    tower = [entity("post", "tower"), entity("bell", "tower")]
    policy = build_policy.select({"intent": "extension"}, [], tower)
    assert policy["duration_s"] == 3 and policy["animate_ids"] == ["bell", "post"]
    assert build_policy.select({"intent": "new_structure"}, [], tower)["duration_s"] == 4
    assert build_policy.select({"intent": "placement"}, [], tower)["duration_s"] == 0


def test_large_rebuild_move_surface_and_retry_do_not_auto_play():
    before = [entity("a", "floor"), entity("b", "walls")]
    after = copy.deepcopy(before)
    for e in after:
        e["transform"]["position_cm"][0] += 100
    assert build_policy.select({"intent": "edit", "scene": "church"}, before,
                               after)["duration_s"] == 0
    assert build_policy.select({"intent": "extension", "scene": "church"}, before,
                               after + [entity("c", "tower")])["duration_s"] == 0
    after = copy.deepcopy(before)
    after[0]["surface"] = {"preset": "metal-steel"}
    policy = build_policy.select({"intent": "edit", "scene": "church"}, before, after)
    assert policy["duration_s"] == 0 and not policy["animate_ids"]
    assert build_policy.select({"intent": "new_structure", "scene": "church"}, before,
                               before)["duration_s"] == 0


@pytest.mark.parametrize("count", [1, 2, 152, 500])
def test_explicit_show_has_fixed_five_second_budget(count):
    entities = [entity(f"item-{i}", "items") for i in range(count)]
    policy = build_policy.select({"presentation": {"mode": "on", "camera": "overview",
                                                   "music": "architect-pulse"}}, [], entities)
    payload = build_policy.preview(policy, entities, {
        "center_cm": [0, 0, 100], "size_cm": [1000, 1000, 200]})
    assert max(t["start_s"] + t["duration_s"] for t in payload["timeline"]) == pytest.approx(5)
    assert payload["finish_delay_s"] == 0 and not payload["auto_skip"]
    assert payload["music"] == "architect-pulse" and "cameras" in payload


def test_schema_and_skip_command_keep_cancel_separate():
    plan = BuildPlan.model_validate({"intent": "new_structure", "nodes": [],
                                    "presentation": {"mode": "off"}})
    assert plan.presentation.mode == "off"
    assert parse("edit", "presentation skip --revision 4").data == {
        "action": "skip", "revision": 4}
    assert parse("edit", "presentation cancel").data == {"action": "cancel"}
