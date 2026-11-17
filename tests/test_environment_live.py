"""Run only against an explicitly selected disposable -game instance."""

import os
import time
import uuid

import pytest
from lc.client import Client

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("LC_LIVE") != "1",
                                                reason="Requires disposable runtime")]


@pytest.fixture
def client() -> Client:
    assert os.environ.get("LC_PORT"), "Select an isolated test port explicitly"
    return Client(port=int(os.environ["LC_PORT"]))


def applied(result: dict) -> dict:
    assert result["status"] == "applied", result
    return result


@pytest.mark.parametrize("preset", ["morning", "noon", "golden-hour", "dusk", "night", "overcast"])
def test_preset_readback_and_undo(client: Client, preset: str) -> None:
    before = client.read("environment")["committed"]
    receipt = applied(client.edit(f"environment apply --preset {preset}"))
    current = client.read("environment")
    assert current["committed"] == current["presets"][preset]
    for key, value in current["observed"].items():
        if key in current["committed"]:
            assert value == pytest.approx(current["committed"][key], abs=0.001)
    assert client.receipt(receipt["req_id"])["tx_id"] == receipt["tx_id"]
    applied(client.edit(f"undo --tx-id {receipt['tx_id']}"))
    assert client.read("environment")["committed"] == before
    assert client.read("consistency")["ok"]


@pytest.mark.parametrize("values", [
    {"time_h": 25}, {"sun_intensity": -1}, {"sun_temperature_k": 500},
    {"fog_density": 0.11}, {"fog_color": [0, 0, 2]}, {"cloud_coverage": 1.1},
    {"contrast": 3}, {"rain": 1}, {"time_h": "12"}, {"time_h": True},
    {"saturation": None}, {"fog_falloff": 0}, {"temperature_k": 16000},
    {"fog_height_cm": 100001}, {"fog_color": [0, 1]},
])
def test_invalid_environment_is_atomic(client: Client, values: dict) -> None:
    before = client.read("environment")
    result = client._write("/v1/environment", {"action": "apply", "values": values}, None)
    assert result["status"] == "rejected" and result["warnings"]
    after = client.read("environment")
    assert before["committed"] == after["committed"]
    assert before["version"] == after["version"]


def test_preview_cancel_commit_checkpoint_and_custom_preset(client: Client) -> None:
    before = client.read("environment")
    applied(client.edit("environment preview --preset night"))
    preview = client.read("environment")
    assert preview["preview_active"]
    assert preview["committed"] == before["committed"]
    assert preview["version"] == before["version"]
    assert client.read("consistency")["ok"]
    assert client.edit("environment apply --time 1")["status"] == "rejected"
    result = client.edit("apply create forbidden-during-preview --at 20m,0,1m")
    assert result["status"] == "rejected"
    applied(client.edit("environment cancel"))
    assert client.read("environment")["state"] == before["committed"]
    applied(client.edit("environment preview --time 6 --rate 0.1"))
    time.sleep(0.2)
    assert client.read("environment")["state"]["time_h"] > 6
    assert client.edit("environment commit --expected-version -1")["status"] == "rejected"
    applied(client.edit(f"environment commit --expected-version {before['version']}"))
    saved = client.read("environment")["committed"]
    name = "light-" + uuid.uuid4().hex
    applied(client.edit(f"environment save --name {name}"))
    assert client.edit(f"environment save --name {name}")["status"] == "rejected"
    applied(client.edit(f"checkpoint save {name}"))
    applied(client.edit("environment apply --preset noon"))
    applied(client.edit(f"restore --checkpoint {name}"))
    assert client.read("environment")["committed"] == saved
    applied(client.edit("environment apply --preset dusk"))
    applied(client.edit(f"environment apply --preset {name}"))
    assert client.read("environment")["committed"] == saved
    assert client.read("consistency")["ok"]


def test_bad_request_fields_and_duplicate_request(client: Client) -> None:
    before = client.read("environment")
    request_id = "light-" + uuid.uuid4().hex
    first = applied(client.edit("environment apply --time 4", request_id=request_id))
    assert client.edit("environment apply --time 20", request_id=request_id) == first
    assert client.read("environment")["committed"]["time_h"] == 4
    for body in [{"action": "cancel", "values": {}}, {"action": "preview", "rate_h_per_s": -1},
                 {"action": "apply", "preset": "unknown"}, {"action": "apply", "values": {}},
                 {"action": "save", "name": "noon"}]:
        assert client._write("/v1/environment", body, None)["status"] == "rejected"
    applied(client.edit(f"undo --tx-id {first['tx_id']}"))
    assert client.read("environment")["committed"] == before["committed"]
