import os
import time
import uuid
from pathlib import Path

import pytest
from lc.client import Client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("LC_LIVE") != "1", reason="Set LC_LIVE=1"),
]


@pytest.fixture
def client() -> Client:
    instance = Client(port=int(os.getenv("LC_PORT", "18844")))
    assert instance.read("objects * --count")["count"] == 0, "Use an empty disposable world"
    return instance


def test_five_meter_column_select_capture_undo(client: Client) -> None:
    request = "test-" + uuid.uuid4().hex
    command = "apply create live-column --template blockout/column --size 1m,1m,5m --front 4m"
    created = client.edit(command, request_id=request)
    assert created["status"] == "applied", created
    assert client.edit(command, request_id=request) == created
    assert client.read("objects live-* --count")["count"] == 1
    entity = client.read("objects live-column transform,bounds")["entities"][0]
    assert entity["transform"]["size_cm"][2] == 500
    assert entity["bounds"]["max_cm"][2] - entity["bounds"]["min_cm"][2] == 500
    selected = client.edit("select live-* --nearest")
    assert selected["status"] == "applied", selected
    assert selected["entity_ids"] == ["live-column"]
    capture = client.capture("live-column.png")
    assert capture["status"] == "applied", capture
    assert Path(capture["path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    started = time.perf_counter()
    undone = client.edit("undo --last")
    assert time.perf_counter() - started <= 1.0
    assert undone["status"] == "applied", undone
    assert client.read("consistency")["ok"] is True
    assert client.read("objects * --count")["count"] == 0


def test_batch_swap_removal_and_undo(client: Client) -> None:
    operations = [
        {
            "op": "create",
            "entity": {
                "id": f"batch-{index:04d}",
                "template": "blockout/box",
                "transform": {
                    "position_cm": [1000 + (index % 10) * 150, 1000 + (index // 10) * 150, 50],
                    "size_cm": [100, 100, 100],
                },
            },
        }
        for index in range(100)
    ]
    created = client.apply(operations)
    assert created["status"] == "applied", created
    assert created["max_apply_frame_ms"] <= 2.0, created
    assert client.read("objects batch-* --limit 10")["truncated"] is True
    deleted = client.edit("apply delete batch-0000,batch-0050")
    assert deleted["status"] == "applied", deleted
    assert client.read("objects batch-* --count")["count"] == 98
    assert client.read("objects batch-0099 transform")["entities"][0]["transform"][
        "position_cm"
    ] == [2350, 2350, 50]
    assert client.read("consistency")["ok"] is True
    assert client.edit("undo --last")["status"] == "applied"
    assert client.read("objects batch-* --count")["count"] == 100
    assert client.edit("undo --last")["status"] == "applied"
    assert client.read("consistency")["ok"] is True


def test_http_rejects_overlap_and_unknown_template(client: Client) -> None:
    position = client.read("player-status")["position_cm"]
    base = {
        "id": "overlap",
        "template": "blockout/box",
        "transform": {"position_cm": position, "size_cm": [100, 100, 100]},
    }
    assert client.apply([{"op": "create", "entity": base}])["status"] == "rejected"
    base["template"] = "missing"
    assert client.apply([{"op": "create", "entity": base}])["status"] == "rejected"
    assert client.read("consistency")["ok"] is True
