"""Surface scope, rendering contract, preview and persistence on disposable worlds."""

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


@pytest.fixture
def sample(client: Client):
    prefix = "surface-" + uuid.uuid4().hex
    applied(client.edit(f"apply create {prefix} --size 1m,1m,1m --at 10m,0,1m"))
    yield prefix
    client.edit("design cancel")
    applied(client.edit(f"apply delete {prefix}"))


@pytest.mark.parametrize("preset", [
    "stone-limestone", "stone-slate", "stone-brick", "wood-oak", "wood-walnut", "wood-painted",
    "metal-steel", "metal-brass", "metal-rust", "cloth-linen", "cloth-red", "cloth-blue",
    "glass-clear", "glass-amber", "glass-frosted", "emissive-warm", "emissive-cool",
    "emissive-green",
])
def test_surface_presets(client: Client, sample: str, preset: str) -> None:
    receipt = applied(client.edit(f"surface apply {sample} --preset {preset}"))
    surface = client.read(f"objects {sample}")["entities"][0]["surface"]
    assert surface == client.read("surfaces")["presets"][preset]
    applied(client.edit(f"undo --tx-id {receipt['tx_id']}"))
    assert "surface" not in client.read(f"objects {sample}")["entities"][0]
    assert client.read("consistency")["ok"]


def test_surface_preview_isolation(client: Client, sample: str) -> None:
    before = client.read(f"objects {sample}")
    preview = applied(client.edit(f"surface preview {sample} --preset metal-brass"))
    assert client.read(f"objects {sample}") == before
    assert client.read("consistency")["ok"]
    assert client.edit(f"apply delete {sample}")["status"] == "rejected"
    assert client.edit("design commit --expected-version -1")["status"] == "rejected"
    applied(client.edit("design cancel"))
    assert client.read(f"objects {sample}") == before
    preview = applied(client.edit(f"surface preview {sample} --preset glass-amber"))
    applied(client.edit(f"design commit --expected-version {preview['preview_version']}"))
    assert client.read(f"objects {sample}")["entities"][0]["surface"]["preset"] == "glass-amber"


def test_hundred_entity_group_and_checkpoint(client: Client) -> None:
    group = "surface-" + uuid.uuid4().hex
    operations = [{"op": "create", "entity": {
        "id": f"{group}-{i}", "template": "blockout/box", "group": group if i < 100 else "",
        "transform": {"position_cm": [1500 + i % 10 * 110, i // 10 * 110, 100],
                      "size_cm": [100, 100, 100], "rotation_deg": [0, 0, 0]},
    }} for i in range(101)]
    applied(client.apply(operations))
    receipt = applied(client.edit(f"surface apply group:{group} --preset wood-oak --wetness 0.2"))
    assert receipt["applied"] == 100
    untouched = client.read(f"objects {group}-100")["entities"][0]
    assert "surface" not in untouched
    applied(client.edit(f"checkpoint save {group}"))
    applied(client.edit(f"surface apply group:{group} --preset cloth-red"))
    applied(client.edit(f"restore --checkpoint {group}"))
    items = client.read(f"objects group:{group} --limit 200")["entities"]
    assert len(items) == 100
    assert all(x["surface"]["preset"] == "wood-oak" and x["surface"]["wetness"] == 0.2
               for x in items)
    applied(client.edit(f"apply delete {group}-*"))
    assert client.read("consistency")["ok"]


@pytest.mark.parametrize("flags", ["--roughness -1", "--metallic 2", "--wetness 2",
                                    "--emissive 51", "--normal-strength 3", "--tint 0,0,2",
                                    "--preset missing"])
def test_invalid_surface_is_atomic(client: Client, sample: str, flags: str) -> None:
    before = client.read(f"objects {sample}")
    result = client.edit(f"surface apply {sample} {flags}")
    assert result["status"] == "rejected" and result["warnings"]
    assert client.read(f"objects {sample}") == before


@pytest.mark.parametrize("kind", ["stain", "marker", "path"])
def test_decal_transaction(client: Client, kind: str) -> None:
    name = "decal-" + uuid.uuid4().hex
    created = applied(client.edit(f"apply create {name} --template decal/{kind} "
                                 "--size 10cm,2m,2m --at 6m,0,2cm --rotation -90,0,0"))
    entity = client.read(f"objects {name}")["entities"][0]
    assert entity["template"] == f"decal/{kind}" and not entity["collision"]
    assert client.read("consistency")["ok"]
    applied(client.edit(f"undo --tx-id {created['tx_id']}"))
    assert client.read(f"objects {name}* --count")["count"] == 0
