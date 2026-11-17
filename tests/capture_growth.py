"""Capture real Shipping growth separately from performance measurement."""

import argparse
import copy
import json
import shutil
import time
import uuid
from pathlib import Path

from lc.client import Client
from lgg.stream import StreamService

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "tests/reports/upward-growth"


def wait(game, request_id):
    until = time.perf_counter() + 30
    while time.perf_counter() < until:
        value = game.receipt(request_id)
        if value["status"] != "scheduled":
            assert value["status"] == "applied", value
            return value
        time.sleep(.025)
    raise AssertionError(value)


def input_record():
    result = json.loads((REPORT / "agent/result.json").read_text("utf-8"))
    build = result["finalized"]["build_id"]
    return json.loads((REPORT / f"agent/data/builds/streams/{build}.json").read_text("utf-8"))


def run(visual=True):
    game = Client(18872)
    assert game.health().get("upward_growth")
    assert game.edit("presentation cancel")["status"] == "applied"
    before = game.read("objects * --count")["count"]
    source = input_record()
    if not visual:
        for value in source["delivered"].values():
            for node in value["module"]["nodes"]:
                if node.get("asset") == "blockout/column" and node.get("repeat"):
                    node["repeat"]["count"] = [12, 12, 1]
                    node["size"][:2] = [.3, .3]
    plan = copy.deepcopy(source["original_plan"])
    plan["expected_version"] = game.read("objects * --count")["version"]
    directory = REPORT / ("visual" if visual else "performance")
    directory.mkdir(parents=True, exist_ok=True)
    service = StreamService(game, directory / "data")
    frame_start = game.read("frame-history")["last"]
    started = time.perf_counter()
    result = service.plan(plan, uuid.uuid4().hex)
    build = result["build_id"]
    frames = []

    def capture(stage):
        index = len(frames)
        state = game.read("presentation")
        value = game._write("/v1/capture", {"path": f"growth-{index:03d}.png",
                                            "camera": "player", "include_ui": True}, None)
        assert value["status"] == "applied", value
        target = directory / f"frame-{index:03d}.png"
        shutil.copy2(value["path"], target)
        frames.append({"stage": stage, "at_s": time.perf_counter() - started,
                       "file": str(target), "state": state})

    if visual:
        for _ in range(6):
            capture("coarse")
            time.sleep(.35)
    for brief in source["modules"]:
        module = source["delivered"][brief["id"]]["module"]
        result = service.module(build, module, uuid.uuid4().hex)
        if visual:
            for _ in range(7):
                capture(brief["id"])
                time.sleep(.35)
    receipt = wait(game, result["req_id"])
    until = time.perf_counter() + 5
    state = game.read("presentation")
    while state["state"] != "completed" and time.perf_counter() < until:
        time.sleep(.03)
        state = game.read("presentation")
    history = game.read(f"frame-history --since {frame_start}")
    report = {"source": source["build_id"], "build_id": build, "receipt": receipt,
              "state": state, "frames": frames, "frame_history": history,
              "visual_capture": visual, "stress_test": not visual}
    (directory / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    assert game.edit(f"undo --tx-id {receipt['tx_id']}")["status"] == "applied"
    assert game.read("objects * --count")["count"] == before
    print(json.dumps({"frames": len(frames), "average_fps": state["average_fps"],
                      "max_tick_ms": state["max_tick_ms"], "handoff_count": state["handoff_count"],
                      "max_apply_frame_ms": receipt.get("max_apply_frame_ms")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--performance", action="store_true")
    args = parser.parse_args()
    run(not args.performance)
