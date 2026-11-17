import os
import uuid

import pytest
from lc.client import Client

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("LC_LIVE") != "1",
                                                reason="Requires disposable runtime")]


@pytest.fixture
def client() -> Client:
    assert os.environ.get("LC_PORT")
    return Client(port=int(os.environ["LC_PORT"]))


def applied(result: dict) -> dict:
    assert result["status"] == "applied", result
    return result


def test_catalog_and_hundred_props(client: Client) -> None:
    catalog = client.read("catalog")
    assert len(catalog["props"]) == 50 and len(catalog["prefabs"]) == 10
    assert all(item["category"] == "furniture"
               for item in client.read("catalog --category furniture")["props"])
    assert client.read("catalog --search bench")["props"][0]["id"] == "prop/bench"
    name = "bollards-" + uuid.uuid4().hex
    receipt = applied(client.edit(f"prop place prop/bollard --id {name} --at 10m,0,1m "
                                  "--count 100 --columns 10 --step 1m,1m,0 --snap --seed 42 "
                                  "--random-yaw 20 --scale-min 0.9 --scale-max 1.1"))
    assert receipt["applied"] == 100 and receipt["ms"] <= 2000
    items = client.read(f"objects group:{name} --limit 200")["entities"]
    assert len(items) == len({item["id"] for item in items}) == 100
    assert all(abs(item["bounds"]["min_cm"][2]) < 0.1 for item in items)
    applied(client.edit(f"undo --tx-id {receipt['tx_id']}"))
    assert client.read(f"objects {name}* --count")["count"] == 0
    assert client.read("consistency")["ok"]


@pytest.mark.parametrize("prefab", ["rest-stop", "dining-set", "gate-section", "courtyard-center",
                                    "market-stall", "reading-corner", "workshop", "checkpoint",
                                    "monument", "garden-entry"])
def test_prefab_lifecycle(client: Client, prefab: str) -> None:
    name = "prefab-" + uuid.uuid4().hex
    receipt = applied(client.edit(f"prop preview prefab/{prefab} --id {name} --at 10m,0,0"))
    assert client.read(f"objects {name}* --count")["count"] == 0
    applied(client.edit(f"design commit --expected-version {receipt['preview_version']}"))
    items = client.read(f"objects group:{name}")["entities"]
    assert len(items) >= 3
    first = items[0]["id"]
    applied(client.edit(f"apply set {first} --template prop/stool --lod 2 --collision off"))
    after = client.read(f"objects group:{name}")["entities"]
    assert after[0]["id"] == first and after[0]["template"] == "prop/stool"
    assert after[1:] == items[1:]
    applied(client.edit(f"apply delete {name}*"))
    assert client.read("consistency")["ok"]


def test_alignment_array_and_version_conflict(client: Client) -> None:
    name = "layout-" + uuid.uuid4().hex
    applied(client.edit(f"prop place prop/stool --id {name} --at 10m,0,1m "
                        "--count 3 --step 2m,2m,0"))
    applied(client.edit(f"placement align group:{name} --axis y --mode center"))
    items = client.read(f"objects group:{name}")["entities"]
    assert len({item["transform"]["position_cm"][1] for item in items}) == 1
    copied = applied(client.edit(f"placement array {name}-000 --prefix {name}-copy "
                                 "--count 3 --step 0,2m,0"))
    assert copied["applied"] == 3
    bad = client._write("/v1/edits", {"expected_version": -1, "command": "stale",
                                      "operations": [{"op": "delete", "selector": name+"*"}]},
                        None)
    assert bad["status"] == "rejected"
    applied(client.edit(f"apply delete {name}*"))


@pytest.mark.parametrize("asset", ["prop/bench", "prop/barrel", "prop/archway", "prop/lantern",
                                   "prop/umbrella", "prop/flagpole", "prop/terminal"])
def test_asset_collision_and_lod(client: Client, asset: str) -> None:
    name = "asset-" + uuid.uuid4().hex
    applied(client.edit(f"prop place {asset} --id {name} --at 10m,0,1m --snap --lod 3"))
    item = client.read(f"objects {name}")["entities"][0]
    assert item["lod"] == 3 and item.get("collision", True)
    applied(client.edit(f"apply delete {name}"))


def test_unsupported_assets_and_no_support(client: Client) -> None:
    name = "bad-" + uuid.uuid4().hex
    for template in ["prop/unknown", "https://example.test/object.glb"]:
        result = client.edit(f"apply create {name} --template {template} "
                             "--size 1m,1m,1m --at 10m,0,1m")
        assert result["status"] == "rejected"
    result = client.edit(f"prop place prop/stool --id {name} --at 1e6cm,0,1m --snap")
    assert result["status"] == "rejected" and result["warnings"]
