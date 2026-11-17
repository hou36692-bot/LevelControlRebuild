import copy

import pytest
from lc.client import Client
from lgg.build import offsets
from lgg.stream import StreamService, layout
from lgg.stream_types import CoarsePlan
from presenter.timeline import timeline


class Game:
    port = 18872

    def __init__(self):
        self.version = 0
        self.state = {"state": "idle", "revision": 0, "source": "", "camera": "player"}
        self.previews = []
        self.commits = []
        self.receipts = {}

    def health(self):
        return {"upward_growth": True}

    def read(self, command):
        if command == "presentation":
            return copy.deepcopy(self.state)
        if command == "catalog":
            return {"props": [], "prefabs": []}
        if command == "templates":
            return {"templates": [{"id": "blockout/box", "size_cm": [100, 100, 100]}]}
        if command == "player-status":
            return {"feet_cm": [0, 0, 0], "forward": [0, 1, 0]}
        return {"version": self.version, "count": 0}

    def preview(self, body, request_id):
        self.previews.append(copy.deepcopy(body))
        self.state.update(source=body["source"], revision=body["revision"], state="previewing")
        return {"req_id": request_id, "tx_id": "preview", "status": "applied"}

    def commit(self, body, request_id):
        self.commits.append(copy.deepcopy(body))
        result = {"req_id": request_id, "tx_id": "transaction", "status": "scheduled",
                  "version": self.version}
        self.receipts[request_id] = result
        return result

    def receipt(self, request_id):
        return self.receipts.get(request_id, {"status": "unknown"})


def coarse(dependencies=False):
    return {"name": "temple", "expected_version": 0, "anchor": {"at": [20, 0, 0]},
            "modules": [
                {"id": "base", "label": "台基", "size": [8, 6, .4], "at": [0, 0, .2]},
                {"id": "body", "label": "柱廊", "size": [8, 6, 3],
                 "relative_to": "base", "side": "above",
                 "depends_on": ["base"] if dependencies else []},
                {"id": "roof", "label": "屋顶", "size": [8, 6, .5],
                 "relative_to": "body", "side": "above",
                 "depends_on": ["body"] if dependencies else []}],
            "environment": {"preset": "noon"}}


def delivery(identity, revision=1, size=None):
    return {"id": identity, "revision": revision,
            "nodes": [{"id": "shape", "asset": "blockout/box",
                       "size": size or {"base": [8, 6, .4], "body": [1, 1, 3],
                                        "roof": [8, 6, .5]}[identity]}]}


def start(tmp_path, dependencies=False):
    game = Game()
    service = StreamService(game, tmp_path)
    result = service.plan(coarse(dependencies), "new-plan")
    return game, service, result["build_id"]


def test_coarse_freezes_program_computed_interfaces_and_has_no_entities(tmp_path):
    game, service, build = start(tmp_path)
    saved = service.load(build)
    assert [m["at"][2] for m in saved["modules"]] == pytest.approx([.2, 1.9, 3.65])
    assert not game.previews[0]["entities"] and not game.commits
    assert game.previews[0]["director"] and game.previews[0]["music"] == "none"
    assert service.plan(coarse(), "new-plan")["build_id"] == build
    assert len(game.previews) == 1


def test_modules_publish_immediately_and_final_commit_is_single_atomic_snapshot(tmp_path):
    game, service, build = start(tmp_path)
    first = service.module(build, delivery("base"), "base-1")
    assert first["construction_status"] == "designing" and not game.commits
    base = copy.deepcopy(game.previews[-1]["entities"][0])
    assert service.module(build, delivery("base"), "base-1") == first
    service.module(build, delivery("body"), "body-1")
    assert game.previews[-1]["entities"][0] == base
    final = service.module(build, delivery("roof"), "roof-1")
    assert final["construction_status"] == "scheduled"
    assert len(game.commits) == 1 and len(game.commits[0]["entities"]) == 3
    assert game.commits[0]["environment"] == {"preset": "noon"}
    assert game.previews[-1]["seal"] and game.previews[-1]["incremental"]
    assert all(t["start_s"] == 0 and t["duration_s"] == 3
               for t in game.previews[-1]["timeline"])


def test_ids_and_geometry_do_not_depend_on_delivery_order(tmp_path):
    outputs = []
    for index, order in enumerate([("base", "body", "roof"), ("roof", "body", "base")]):
        game, service, build = start(tmp_path / str(index))
        for identity in order:
            service.module(build, delivery(identity), identity)
        outputs.append(game.commits[0]["entities"])
    assert outputs[0] == outputs[1]


def test_bad_envelope_dependency_stale_world_and_cancel_do_not_publish(tmp_path):
    game, service, build = start(tmp_path, True)
    with pytest.raises(ValueError, match="dependencies"):
        service.module(build, delivery("body"), "early")
    with pytest.raises(ValueError, match="envelope"):
        service.module(build, delivery("base", size=[10, 6, .4]), "outside")
    assert len(game.previews) == 1
    game.version = 2
    with pytest.raises(ValueError, match="stale"):
        service.module(build, delivery("base"), "stale")
    game.version = 0
    game.state["state"] = "cancelled"
    with pytest.raises(ValueError, match="cancelled"):
        service.module(build, delivery("base"), "late")
    assert len(game.previews) == 1 and not game.commits


def test_revision_rejects_old_content_and_invalidates_only_dependencies(tmp_path):
    game, service, build = start(tmp_path, True)
    service.module(build, delivery("base"), "base-1")
    service.module(build, delivery("body"), "body-1")
    with pytest.raises(ValueError, match="revision 2"):
        service.module(build, delivery("base"), "old-base")
    edited = service.module(build, delivery("base", 2, [7, 6, .4]), "base-2")
    assert edited["invalidated"] == ["body", "roof"]
    assert edited["pending_modules"] == ["body", "roof"]
    assert len(game.previews[-1]["entities"]) == 1
    assert not game.commits


def test_repeated_request_cannot_change_content(tmp_path):
    _, service, build = start(tmp_path)
    service.module(build, delivery("base"), "base-1")
    with pytest.raises(ValueError, match="different module"):
        service.module(build, delivery("base", 2), "base-1")


def test_repeat_span_and_shared_height_timing():
    assert offsets({"count": 3, "span": [6, 0, 0]})[-1] == ("2", [6, 0, 0])
    assert offsets({"count": [2, 2, 1], "span": [6, 4, 0]})[-1][1] == [6, 4, 0]
    for bad in [{"count": 1, "span": [1, 0, 0]},
                {"count": 2, "span": [1, 0, 0], "step": [1, 0, 0]}]:
        with pytest.raises(ValueError):
            offsets(bad)
    times = timeline([{"id": "a"}, {"id": "b"}], 3)
    assert {t["start_s"] for t in times} == {0}
    assert {t["duration_s"] for t in times} == {3}


def test_relation_cycles_are_rejected():
    value = CoarsePlan.model_validate(coarse()).model_dump(exclude_none=True)
    value["modules"][0].pop("at")
    value["modules"][0].update(relative_to="roof", side="above")
    with pytest.raises(ValueError, match="cyclic"):
        layout(value["modules"])


def test_growth_help_is_reachable_and_has_material_and_retry_contract():
    text = Client(18872).help("growth")
    assert "stone-limestone" in text and "lgg_module" in text
    assert "revision" in text
