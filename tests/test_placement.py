import json

import pytest
from lc.client import ROOT
from lc.command import parse
from lc.placement import layout_operations, place_operations

CATALOG = json.loads((ROOT / "ue/Plugins/LevelControlRuntime/Resources/catalog.json").read_text())


def test_content_manifest() -> None:
    assert len(CATALOG["props"]) >= 50 and len(CATALOG["prefabs"]) >= 10
    ids = {item["id"] for item in CATALOG["props"]}
    assert len(ids) == len(CATALOG["props"])
    for prop in CATALOG["props"]:
        assert prop["imported"] and min(prop["mesh_size_cm"]) > 0
        path = ROOT / "project/Content" / (prop["mesh"].removeprefix("/Game/")+".uasset")
        assert path.is_file()
    assert all(part["asset"] in ids for prefab in CATALOG["prefabs"]
               for part in prefab["components"])


def test_seeded_hundred_grid() -> None:
    data = parse("edit", "prop place prop/bollard --id bollards --at 10m,0,1m "
                 "--count 100 --columns 10 --step 1m,1m,0 --seed 42 "
                 "--random-yaw 30 --scale-min 0.9 --scale-max 1.1 --snap").data
    operations = place_operations(CATALOG, data)
    assert operations == place_operations(CATALOG, data)
    assert len({operation["entity"]["id"] for operation in operations}) == 100
    assert operations[10]["entity"]["transform"]["position_cm"] == [1000, 100, 100]
    assert all(operation["snap"] for operation in operations)
    data["seed"] = 43
    assert operations != place_operations(CATALOG, data)


def test_prefab_ids_and_assembly() -> None:
    data = parse("edit", "prop preview prefab/rest-stop --id east-rest --at 0,0,0").data
    operations = place_operations(CATALOG, data)
    assert [operation["entity"]["id"] for operation in operations] == [
        "east-rest-part-00", "east-rest-part-01", "east-rest-part-02"]
    assert all(operation["entity"]["group"] == "east-rest" for operation in operations)


@pytest.mark.parametrize("flags", ["--count 0", "--random-yaw nan", "--scale-min 2 --scale-max 1",
                                    "--grid -1m", "--lod 4"])
def test_invalid_placement(flags: str) -> None:
    with pytest.raises(ValueError):
        parse("edit", f"prop place prop/bench --id bench --at 0,0,0 {flags}")


def test_unknown_asset() -> None:
    data = parse("edit", "prop place unknown --id bench --at 0,0,0").data
    with pytest.raises(ValueError, match="Unknown cooked asset"):
        place_operations(CATALOG, data)


def test_align_and_array_do_not_modify_input() -> None:
    entities = [{"id": f"item-{i}", "transform": {"position_cm": [i*100, 0, 0]},
                 "bounds": {"min_cm": [i*100-50, -50, -50], "max_cm": [i*100+50, 50, 50]}}
                for i in range(2)]
    before = json.dumps(entities)
    aligned = layout_operations(entities, {"action": "align", "axis": "x", "mode": "center"})
    assert [operation["delta_cm"] for operation in aligned] == [[50, 0, 0], [-50, 0, 0]]
    copies = layout_operations(entities, {"action": "array", "prefix": "copies", "count": 3,
                                          "step": [0, 200, 0]})
    assert len(copies) == 6 and json.dumps(entities) == before
    assert copies[-1]["entity"]["transform"]["position_cm"] == [100, 600, 0]


def test_replacement_syntax() -> None:
    parsed = parse("edit", "apply set east-rest-part-00 --template prop/stool --collision off")
    assert parsed.data["operations"][0]["template"] == "prop/stool"
    assert parsed.data["operations"][0]["collision"] is False
