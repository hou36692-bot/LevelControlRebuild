"""Measure full natural-language builds on an explicitly disposable runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
import uuid
from pathlib import Path

from lc.client import ROOT, Client
from lc.resident import ResidentAgent

CASES = {
    "store": "在我的面前创建一个便利店，我可以走进去查看货架上的物体，并且要有灯光。",
    "gallery": "在我前面建一个可步入的展厅，有入口、展台，陈列100件小展品，留出参观通道并有照明。",
    "courtyard": ("在我前面做一个三面围合、中间有喷泉的庭院，左右各20根柱子，"
                  "配长椅，留出通路，使用清晨光照。"),
}


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def run(port: int, count: int, effort: str, cases: list[str], output: Path) -> dict:
    client = Client(port)
    assert client.read("objects * --count")["count"] == 0, "Use an empty disposable world"
    checkpoint = "fast-empty-" + uuid.uuid4().hex
    assert client.edit(f"checkpoint save {checkpoint}")["status"] == "applied"
    report = {"model": "gpt-6-astra", "effort": effort, "port": port, "runs": [],
              "complete": False, "measurement": "submit to applied receipt plus game presentation "
              "completed/player camera; HTTP observation, not display scanout",
              "quality": "Screenshots and physical traversal are separately validated"}
    report["runtime_health"] = client._request("/health")
    report["revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                                 text=True).strip()
    report["source_sha256"] = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "tools").rglob("*.py"))}
    output.parent.mkdir(parents=True, exist_ok=True)
    with ResidentAgent(port=port, model="gpt-6-astra", effort=effort) as agent:
        report["setup_s"] = agent.setup_s
        for index in range(count):
            for case in cases:
                assert client.read("objects * --count")["count"] == 0
                submitted = time.perf_counter()
                turn = agent.turn(CASES[case], submitted_at=submitted)
                builds = turn.get("timings", {}).get("builds", [])
                receipts = [call["receipt"] for call in turn["tools"]
                            if call.get("receipt") and call["receipt"].get("status") == "applied"
                            and call["tool"] == "lgg_build"]
                world = client.read("consistency")
                row = {"case": case, "index": index, "prompt": CASES[case], "turn": turn,
                       "usable_s": builds[-1]["seconds"] if builds else None,
                       "entities": world["memory_count"], "consistency": world,
                       "presentation": client.read("presentation")}
                row["completed_build"] = bool(builds and receipts and world["ok"])
                row["within_10s"] = bool(row["completed_build"] and row["usable_s"] <= 10)
                report["runs"].append(row)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
                print(json.dumps({k: row[k] for k in ["case", "index", "usable_s", "entities",
                                                       "completed_build"]}), flush=True)
                client.edit("presentation cancel")
                restored = client.edit(f"restore --checkpoint {checkpoint}")
                assert restored["status"] == "applied", restored
                assert client.read("objects * --count")["count"] == 0
    report["complete"] = True
    values = [row["usable_s"] for row in report["runs"] if row["completed_build"]]
    report.update(p50_s=percentile(values, 0.5), p95_s=percentile(values, 0.95))
    report["completed_count"] = len(values)
    report["latency_passed"] = (len(values) == len(report["runs"])
                                and report["p95_s"] is not None and report["p95_s"] <= 10)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--effort", choices=["low", "high"], default="low")
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=list(CASES))
    parser.add_argument("--output", type=Path, default=ROOT / "tests/reports/fast-build.json")
    args = parser.parse_args()
    result = run(args.port, args.count, args.effort, args.cases, args.output)
    print(json.dumps({k: v for k, v in result.items() if k != "runs"}, ensure_ascii=False))
