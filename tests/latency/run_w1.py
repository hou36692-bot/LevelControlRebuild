"""Measure 20 real conversational turns and 50 registry/SQLite round trips."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lc.client import ROOT, Client
from lc.resident import ResidentAgent

PROMPT = "在我面前放一根 5 米高的柱子，然后撤销"


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize_turn(turn: dict[str, Any]) -> dict[str, Any]:
    assert turn["status"] == "completed", turn
    calls = turn["tools"]
    creates = [
        call for call in calls if call["arguments"].get("command", "").startswith("apply create ")
    ]
    undos = [call for call in calls if call["arguments"].get("command", "").startswith("undo ")]
    assert len(creates) == 1 and len(undos) == 1, "Expected one actual create and one actual undo"
    create, undo = creates[0], undos[0]
    for call in [create, undo]:
        assert call["receipt"] and call["receipt"]["status"] == "applied", call
        assert call["receipt"]["applied"] == 1, call
    assert create["completed_s"] <= undo["started_s"]
    assert create["receipt"]["entity_ids"] == undo["receipt"]["entity_ids"]
    return {
        "end_to_end_s": undo["completed_s"],
        "undo_receipt_s": undo["completed_s"] - undo["started_s"],
        "first_visible_game_frame_proxy_s": create["started_s"]
        + create["receipt"]["first_visible_game_frame_s"],
        "commit_receipt_upper_bound_s": create["completed_s"],
        "presentation_s": None,
        "max_apply_frame_ms": max(call["receipt"]["max_apply_frame_ms"] for call in [create, undo]),
        "create_req_id": create["receipt"]["req_id"],
        "undo_req_id": undo["receipt"]["req_id"],
        "agent_total_s": turn["elapsed_s"],
        "events": turn["events"],
    }


def consistency_cycles(client: Client, count: int) -> dict[str, Any]:
    times = []
    frames = []
    for index in range(count):
        created = client.edit(
            f"apply create consistency-{index} --template blockout/column "
            "--size 1m,1m,5m --front 4m"
        )
        assert created["status"] == "applied", created
        assert client.read("consistency")["ok"] is True
        started = time.perf_counter()
        undone = client.edit(f"undo --tx-id {created['tx_id']}")
        times.append(time.perf_counter() - started)
        assert undone["status"] == "applied", undone
        state = client.read("consistency")
        assert state["ok"] is True and state["memory_count"] == state["sqlite_count"] == 0, state
        frames += [created["max_apply_frame_ms"], undone["max_apply_frame_ms"]]
    return {
        "cycles": count,
        "every_state_matched": True,
        "final_state": state,
        "max_undo_s": max(times),
        "max_apply_frame_ms": max(frames),
    }


def run(output: Path, turns: int = 20, cycles: int = 50, port: int = 18844) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    client = Client(port=port)
    assert client.read("objects * --count")["count"] == 0, "Use an empty disposable world"
    report: dict[str, Any] = {
        "date": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "prompt": PROMPT,
        "model": "gpt-6-luna",
        "reasoning_effort": "low",
        "measurement": "persistent Codex app-server; clock starts before sending turn/start",
        "presentation": "not implemented in W1",
        "runs": [],
        "complete": False,
    }
    report["consistency"] = consistency_cycles(client, cycles)
    print(f"Consistency: {cycles}/{cycles} passed", flush=True)
    with ResidentAgent(port=port) as agent:
        report["model"] = agent.model
        report["agent_setup_s"] = agent.setup_s
        for index in range(turns):
            row = summarize_turn(agent.turn(PROMPT))
            state = client.read("consistency")
            assert state["ok"] and state["memory_count"] == 0, state
            report["runs"].append(row)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                f"Turn {index + 1}/{turns}: {row['end_to_end_s']:.3f} s; "
                f"undo {row['undo_receipt_s']:.3f} s",
                flush=True,
            )
    values = [row["end_to_end_s"] for row in report["runs"]]
    report["p50_s"] = percentile(values, 0.5)
    report["p90_s"] = percentile(values, 0.9)
    report["p95_s"] = percentile(values, 0.95)
    report["max_undo_s"] = max(row["undo_receipt_s"] for row in report["runs"])
    report["max_apply_frame_ms"] = max(row["max_apply_frame_ms"] for row in report["runs"])
    report["complete"] = True
    report["passed"] = (
        turns == 20
        and cycles == 50
        and report["p50_s"] <= 10
        and report["p90_s"] <= 15
        and report["max_undo_s"] <= 1
        and report["consistency"]["max_undo_s"] <= 1
        and report["max_apply_frame_ms"] <= 2
        and report["consistency"]["max_apply_frame_ms"] <= 2
    )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"runs", "prompt"}},
            ensure_ascii=False,
            indent=2,
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "tests/reports/w1-latency.json")
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--cycles", type=int, default=50)
    parser.add_argument("--port", type=int, default=18844)
    options = parser.parse_args()
    result = run(options.output, options.turns, options.cycles, options.port)
    raise SystemExit(0 if result["passed"] else 1)
