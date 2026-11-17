"""Measure actual model-driven planning, final commit, presentation and interruption."""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
import uuid

from lc.client import ROOT, Client
from lc.resident import ResidentAgent

PROMPT = "做一个三面围合、中间有喷泉的庭院"


def wait_receipt(client: Client, request_id: str) -> dict:
    deadline = time.perf_counter() + 120
    while time.perf_counter() < deadline:
        receipt = client.receipt(request_id)
        if receipt["status"] != "scheduled":
            return receipt
        time.sleep(0.02)
    raise AssertionError("Commit timed out")


def measure(port: int, count: int) -> dict:
    client = Client(port=port)
    assert client.read("objects * --count")["count"] == 0
    empty = "latency-empty-" + uuid.uuid4().hex
    assert client.edit(f"checkpoint save {empty}")["status"] == "applied"
    report = {
        "prompt": PROMPT,
        "runs": [],
        "complete": False,
        "measurement": "real ResidentAgent turns; observed HTTP frame state, not scanout",
    }
    output = ROOT / "tests/reports/w23-latency.json"
    for index in range(count):
        client.edit("presentation cancel")
        with ResidentAgent(port=port) as agent:
            observations: dict = {}
            event_cursor = client.read("events --since 0")["next"]
            stop = threading.Event()
            started = time.perf_counter()

            def watch() -> None:
                reader = Client(port=port)
                while not stop.is_set():
                    state = reader.read("presentation")
                    elapsed = time.perf_counter() - started
                    if state["region_count"] > 0 and state["first_visible_game_frame_s"] >= 0:
                        observations.setdefault("site_observed_s", elapsed)
                    if state["region_count"] > 1 and state["first_visible_game_frame_s"] >= 0:
                        observations.setdefault("modules_observed_s", elapsed)
                    stop.wait(0.01)

            watcher = threading.Thread(target=watch)
            watcher.start()
            try:
                turn = agent.turn(PROMPT)
                queued = [
                    c["receipt"]
                    for c in turn["tools"]
                    if c["tool"] == "lgg_run"
                    and c["receipt"]
                    and c["receipt"]["status"] == "scheduled"
                ]
                assert queued, turn
                pending = queued[-1]
                interrupted = None
                if index == count - 1:
                    assert client.read("objects * --count")["count"] == 0
                    revision_started = time.perf_counter()
                    changed = agent.turn("喷泉小一点")
                    replacement = [
                        c["receipt"]
                        for c in changed["tools"]
                        if c["tool"] == "lgg_run"
                        and c["receipt"]
                        and c["receipt"]["status"] == "scheduled"
                    ]
                    assert replacement, changed
                    old = client.receipt(pending["req_id"])
                    assert old["status"] == "rejected", old
                    assert client.read("objects * --count")["count"] == 0
                    interrupted = {
                        "prompt": "喷泉小一点",
                        "turn": changed,
                        "old_receipt": old,
                        "seconds": time.perf_counter() - revision_started,
                        "entities_before_replacement_commit": 0,
                    }
                    pending = replacement[-1]
                committed = wait_receipt(client, pending["req_id"])
                commit_s = time.perf_counter() - started
                assert committed["status"] == "applied", committed
                deadline = time.perf_counter() + 10
                while time.perf_counter() < deadline:
                    state = client.read("presentation")
                    if state["state"] == "completed":
                        break
                    time.sleep(0.01)
                assert state["state"] == "completed" and state["camera"] == "player", state
                row = {
                    **observations,
                    "commit_observed_s": commit_s,
                    "presentation_observed_s": time.perf_counter() - started,
                    "setup_s": agent.setup_s,
                    "turn": turn,
                    "commit": committed,
                    "presentation": state,
                    "interruption": interrupted,
                    "consistency": client.read("consistency"),
                }
                events = client.read(f"events --since {event_cursor}")
                stage_events = [
                    e
                    for e in events["events"]
                    if e["type"]
                    in {"cancelled", "preview_settled", "committed", "presentation_complete"}
                ]
                max_frame = max(
                    [state["max_frame_ms"]] + [e.get("max_frame_ms", 0) for e in stage_events]
                )
                row["all_stages_min_fps"] = 1000 / max_frame if max_frame else 0
                row["stage_events"] = events
                row["passed"] = (
                    row.get("modules_observed_s", 999) <= 20
                    and commit_s <= 120
                    and row["all_stages_min_fps"] >= 30
                    and state["max_tick_ms"] <= 2
                    and committed["max_apply_frame_ms"] <= 2
                    and row["consistency"]["ok"]
                )
                report["runs"].append(row)
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    json.dumps(
                        {
                            k: v
                            for k, v in row.items()
                            if k
                            in {
                                "site_observed_s",
                                "modules_observed_s",
                                "commit_observed_s",
                                "presentation_observed_s",
                                "passed",
                            }
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            finally:
                stop.set()
                watcher.join(timeout=5)
        assert client.edit(f"restore --checkpoint {empty}")["status"] == "applied"
    report["complete"] = True
    report["passed"] = all(row["passed"] for row in report["runs"])
    for key in ["modules_observed_s", "commit_observed_s", "presentation_observed_s"]:
        values = sorted(row[key] for row in report["runs"])
        position = (len(values) - 1) * 0.95
        low, high = math.floor(position), math.ceil(position)
        report[key] = {
            "min": min(values),
            "max": max(values),
            "p50": values[len(values) // 2],
            "p95": values[low] + (values[high] - values[low]) * (position - low),
        }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18846)
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    result = measure(args.port, args.count)
    print(
        json.dumps({k: v for k, v in result.items() if k != "runs"}, ensure_ascii=False, indent=2)
    )
    raise SystemExit(0 if result["passed"] else 1)
