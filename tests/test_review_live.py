import os
import time
import uuid
from pathlib import Path

import pytest
from lc.client import Client

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("LC_LIVE") != "1",
                                                reason="Requires disposable runtime")]


@pytest.fixture(scope="module")
def client() -> Client:
    assert os.environ.get("LC_PORT")
    return Client(port=int(os.environ["LC_PORT"]))


def complete(client: Client, result: dict) -> dict:
    deadline = time.monotonic()+25
    while result["status"] == "scheduled" and time.monotonic() < deadline:
        time.sleep(0.02)
        result = client.receipt(result["req_id"])
    assert result["status"] == "applied", result
    return result


@pytest.fixture(scope="module")
def geometry(client: Client):
    prefix = "sight-" + uuid.uuid4().hex
    operations = [{"op": "create", "entity": {
        "id": f"{prefix}-{i}", "template": "blockout/box",
        "transform": {"position_cm": [1600, -3800+i*400, 300], "size_cm": [100, 160, 200],
                      "rotation_deg": [0, 45 if i % 3 == 0 else 0, 0]},
    }} for i in range(20)]
    complete(client, client.apply(operations))
    yield prefix
    complete(client, client.edit(f"apply delete {prefix}*"))


@pytest.mark.parametrize("index", range(20))
def test_twenty_sightline_cases(client: Client, geometry: str, index: int) -> None:
    y = -3800+index*400+(0 if index % 2 == 0 else 200)
    result = client.check({"kind": "sightline", "from_cm": [1200, y, 300],
                           "to_cm": [2000, y, 300]})
    assert result["ok"]
    assert result["blocked"] == (index % 2 == 0), result
    if index % 2 == 0:
        assert result["entity_id"] == f"{geometry}-{index}"
        assert 300 <= result["distance_cm"] <= 400
        assert result["hit_cm"][1:] == pytest.approx([y, 300])


def test_four_view_comparison_and_performance(client: Client) -> None:
    settings = "--center 10m,0,2m --radius 15m --height 8m"
    first = complete(client, client.capture("review-a.png " + settings))
    assert len(first["captures"]) == 4 and first["ms"] <= 5000
    complete(client, client.edit("environment apply --preset golden-hour"))
    second = complete(client, client.capture("review-b.png " + settings))
    assert second["ms"] <= 5000
    for shot in second["captures"]:
        assert f"v{second['version']}" in shot["path"]
        assert Path(shot["path"]).read_bytes().startswith(b"\x89PNG")
    comparison = complete(client, client.capture(
        f"compare --a {first['req_id']} --b {second['req_id']} --name test-comparison"))
    assert len(comparison["comparisons"]) == 4
    assert all(Path(item["path"]).stat().st_size > 0 for item in comparison["comparisons"])
    performance = complete(client, client.edit("review performance --frames 120"))
    report = performance["report"]
    assert report["samples"] == len(report["frame_ms"]) == 120
    assert report["fps_p50"] > 0 and report["frame_ms_p95"] > 0
    assert report["draw_calls_available"] and report["draw_calls_p50"] > 0
    assert report["visible_lights"] >= 1 and report["process_memory_bytes"] > 0
    assert report["width"] > 0 and report["cpu"] and report["gpu"]
    assert Path(performance["path"]).is_file()


def test_review_cancellation(client: Client) -> None:
    started = client.edit("review performance --frames 3600")
    assert started["status"] == "scheduled"
    complete(client, client.edit("review cancel"))
    assert client.receipt(started["req_id"])["status"] == "failed"
    started = client.capture("cancel.png --center 10m,0,2m")
    assert started["status"] == "scheduled"
    complete(client, client.edit("review cancel"))
    assert client.receipt(started["req_id"])["status"] == "failed"
    assert client.read("consistency")["ok"]
    assert not client.read("presentation")["review_camera_active"]


def test_bad_review_and_sightline(client: Client) -> None:
    result = client.check({"kind": "sightline", "from_cm": [1, 1, 1], "to_cm": [1, 1, 1]})
    assert not result["ok"] and result["error"]
    result = client.capture("compare --a missing-a --b missing-b --name invalid")
    assert result["status"] == "rejected"


def test_review_does_not_steal_construction_camera(client: Client) -> None:
    from test_w23_live import make_design

    make_design(client, "review-camera-conflict")
    try:
        assert client.capture("conflict.png --center 10m,0,2m")["status"] == "rejected"
        assert client.edit("review performance --frames 30")["status"] == "rejected"
    finally:
        complete(client, client.edit("presentation cancel"))
