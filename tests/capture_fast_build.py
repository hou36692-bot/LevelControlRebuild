"""Replay an actual model plan for separate image and collision inspection."""

import argparse
import json
import shutil
import time
import uuid
from pathlib import Path

from lc.client import ROOT, Client
from lgg.build import BuildService, bounds, rotate
from lgg.build_types import BuildPlan


def run(port: int, report_path: Path, case: str, output: Path, keep: bool) -> dict:
    client = Client(port)
    assert client.read("objects * --count")["count"] == 0, "Use an empty disposable world"
    report = json.loads(report_path.read_text("utf-8"))
    row = next(row for row in report["runs"] if row["case"] == case and row["completed_build"])
    call = next(c for c in row["turn"]["tools"] if c["tool"] == "lgg_build"
                and (c.get("receipt") or {}).get("status") == "applied")
    value = BuildPlan.model_validate(call["arguments"]["plan"]).model_dump(
        mode="json", exclude_none=True, exclude_unset=True)
    value["expected_version"] = client.read("objects * --count")["version"]
    result = BuildService(client).build(value, "visual-" + uuid.uuid4().hex)
    assert result["status"] == "applied" and result["presentation_ready"], result
    entities = client.read(f"objects {result['scene']}-* --limit 16384")["entities"]
    low, high = bounds(entities)
    center = [(a + b) / 2 for a, b in zip(low, high, strict=True)]
    span = max(b - a for a, b in zip(low, high, strict=True))
    capture = client._write("/v1/capture", {
        "path": f"fast-{case}.png", "camera": "player", "center_cm": center,
        "radius_cm": span * 0.9, "height_cm": span * 0.55,
    }, None, wait=False)
    deadline = time.perf_counter() + 40
    while capture["status"] == "scheduled":
        assert time.perf_counter() < deadline, capture
        time.sleep(0.05)
        capture = client.receipt(capture["req_id"])
    assert capture["status"] == "applied", capture
    output.mkdir(parents=True, exist_ok=True)
    for view in capture["captures"]:
        source = Path(view["path"])
        shutil.copy2(source, output / source.name)
    player = client.read("player-status")
    floor = max((e for e in entities if e["bounds"]["max_cm"][2]
                 <= player["feet_cm"][2] + player["max_step_height_cm"]),
                key=lambda e: e["transform"]["size_cm"][0] * e["transform"]["size_cm"][1],
                default=None)
    checks = []
    if floor:
        height = floor["bounds"]["max_cm"][2] + 2
        start = [*player["feet_cm"][:2], height]
        at = floor["transform"]["position_cm"]
        yaw = floor["transform"]["rotation_deg"][1]
        width, depth, _ = floor["transform"]["size_cm"]
        candidates = [(0, 0), (-100, 0), (100, 0), (0, -depth / 4),
                      (-width / 4, 0), (width / 4, 0)]
        for x, y in candidates:
            offset = rotate([x, y, 0], yaw)
            end = [at[0] + offset[0], at[1] + offset[1], height]
            check = client.check({"kind": "clearance", "from_cm": start, "to_cm": end})
            checks.append({"from_cm": start, "to_cm": end, "result": check})
    evidence = {"case": case, "source_report": str(report_path), "source_index": row["index"],
                "build": result, "captures": capture, "clearance": checks,
                "player_before": player,
                "walkable_capsule_path_found": any(c["result"].get("ok")
                                                     and not c["result"].get("blocked")
                                                     for c in checks)}
    if not keep:
        assert client.edit(f"undo --tx-id {result['tx_id']}")["status"] == "applied"
    (output / f"{case}-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), "utf-8")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--case", choices=["store", "gallery", "courtyard"], required=True)
    parser.add_argument("--report", type=Path,
                        default=ROOT / "tests/reports/fast-build-low-30.json")
    parser.add_argument("--output", type=Path, default=ROOT / "tests/reports/fast-visual")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    result = run(args.port, args.report, args.case, args.output, args.keep)
    print(json.dumps({"case": result["case"], "entity_count": result["build"]["entity_count"],
                      "capsule_path": result["walkable_capsule_path_found"]}))
