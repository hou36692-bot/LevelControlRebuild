"""Run the final P0 benchmark after soak, preserving and restoring its demonstration world."""

import json
import time
import uuid

from latency.run_w1 import run
from lc.client import ROOT, Client
from lgg.compiler import delta


def main() -> None:
    path = ROOT / "tests/reports/w4-soak.json"
    while True:
        if path.exists():
            soak = json.loads(path.read_text("utf-8"))
            if soak.get("error"):
                raise RuntimeError("Soak failed; do not start the final regression")
            if soak.get("complete"):
                assert soak["passed"], soak
                break
        time.sleep(5)
    time.sleep(2)
    client = Client(port=18844)
    assert client.read("consistency")["ok"]
    original = client.read("objects * --limit 16384")["entities"]
    checkpoint = "delivery-before-p0-" + uuid.uuid4().hex[:8]
    assert client.edit("checkpoint save " + checkpoint)["status"] == "applied"
    report = None
    try:
        if original:
            removed = client.apply([{"op": "delete", "selector": item["id"]} for item in original])
            assert removed["status"] == "applied", removed
        report = run(ROOT / "tests/reports/w4-p0.json", port=18844)
    finally:
        restored = client.edit("restore --checkpoint " + checkpoint)
        assert restored["status"] == "applied", restored
        current = client.read("objects * --limit 16384")["entities"]
        assert not any(delta(original, current).values())
        state = client.read("consistency")
        assert state["ok"]
        (ROOT / "tests/reports/w4-final-world.json").write_text(json.dumps({
            "checkpoint": checkpoint, "restored_entities": len(current), "consistency": state,
            "same_geometry_and_ids": True}, indent=2), "utf-8")
    assert report and report["passed"], "P0 criteria were not met"
    print("FINAL_P0_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
