import pytest
from lc.client import Client
from lc.command import parse


@pytest.mark.parametrize("name,path", [
    ("environment", "/v1/environment"),
    ("environment-presets", "/v1/environment/presets"),
])
def test_environment_read(name: str, path: str) -> None:
    assert parse("read", name).path == path


@pytest.mark.parametrize("action", ["apply", "preview", "cancel", "commit", "save"])
def test_environment_action(action: str) -> None:
    assert parse("edit", f"environment {action}").data["action"] == action


def test_environment_units_and_values() -> None:
    command = parse("edit", "environment preview --time 18 --fog-height 2m "
                    "--fog-color 0.1,0.2,0.3 --sun-elevation -15 --rate 0.1")
    assert command.data == {"action": "preview", "rate_h_per_s": 0.1, "values": {
        "time_h": 18, "fog_height_cm": 200, "fog_color": [0.1, 0.2, 0.3],
        "sun_elevation_deg": -15,
    }}


@pytest.mark.parametrize("flags", ["--time nan", "--fog-color 0,inf,0", "--time 3 --rain 1"])
def test_environment_invalid_values(flags: str) -> None:
    with pytest.raises(ValueError):
        parse("edit", "environment apply " + flags)


def test_environment_uses_transaction_client(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Client(port=18849)
    calls = []

    def request(path: str, data: dict | None = None) -> dict:
        calls.append((path, data))
        return {"status": "applied", "req_id": data["req_id"]}

    monkeypatch.setattr(client, "_request", request)
    client.edit("environment apply --preset golden-hour", request_id="environment-one")
    assert calls[0][0] == "/v1/environment"
    assert calls[0][1]["req_id"] == "environment-one"
    assert calls[0][1]["preset"] == "golden-hour"


def test_environment_help() -> None:
    assert "expected-version" in Client().help("environment")
