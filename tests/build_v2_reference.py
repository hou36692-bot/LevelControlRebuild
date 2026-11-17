"""Build the specification's 24 m x 20 m reference courtyard in an empty disposable world."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lc.client import ROOT, Client
from lc.command import parse
from lc.placement import place_operations


def build(client: Client) -> dict:
    assert client.read("objects * --count")["count"] == 0, "Reference world must be empty"
    operations = []

    def box(name: str, position: list, size: list, preset: str, group: str) -> None:
        operations.append({"op": "create", "entity": {
            "id": name, "template": "blockout/box", "group": group,
            "tags": [group], "surface": {"preset": preset},
            "transform": {"position_cm": position, "size_cm": size,
                          "rotation_deg": [0, 0, 0]},
        }})

    for x in range(12):
        for y in range(10):
            box(f"reference-floor-{x}-{y}", [900+x*200, -900+y*200, 10], [200, 200, 20],
                "stone-limestone", "reference-floor")
    for level in range(2):
        for y in range(10):
            for side, x in [("west", 800), ("east", 3200)]:
                box(f"reference-wall-{side}-{level}-{y}", [x, -900+y*200, 120+level*200],
                    [40, 200, 200], "stone-brick", "reference-wall")
        for x in range(12):
            box(f"reference-wall-north-{level}-{x}", [900+x*200, 1000, 120+level*200],
                [200, 40, 200], "stone-brick", "reference-wall")
    for index in range(12):
        position = [840+(index//6)*2320, [-800,-500,-200,200,500,800][index % 6], 180]
        box(f"reference-column-{index}", position,
            [55, 55, 320], "stone-slate", "reference-column")
    for x in range(6):
        for y in range(8):
            box(f"reference-path-{x}-{y}", [1500+x*200, -700+y*200, 23], [90, 90, 6],
                "stone-slate", "reference-path")
    for index in range(20):
        box(f"reference-border-{index}", [1050+(index % 10)*210, -970+(index//10)*1940, 45],
            [70, 35, 50], "stone-limestone", "reference-border")
    geometry = client.apply(operations)
    assert geometry["status"] == "applied", geometry
    catalog = client.read("catalog")
    props = []

    def prop(asset: str, x: float, y: float, yaw: float = 0) -> None:
        data = parse("edit", f"prop place prop/{asset} --id reference-prop-{len(props):02d} "
                     f"--at {x}cm,{y}cm,1m --yaw {yaw} --snap --group reference-props").data
        props.extend(place_operations(catalog, data))

    prop("fountain", 2000, 0)
    for x in [1200, 2800]:
        for y in [-750, -450, -150, 150, 450, 750]:
            prop("bench", x, y, 90)
    for x in [1000, 1300, 1600, 1900, 2200, 2500, 2800, 3100]:
        prop("planter", x, 900)
    for x in [1000, 3000]:
        for y in [-800, 800]:
            prop("lamppost", x, y)
    for x in [1700, 2300]:
        for y in [-850, -650, -450, -250, 250, 450, 650, 850]:
            prop("bollard", x, y)
    for x in [1400, 2600]:
        for y in [-500, 500]:
            prop("bollard", x, y)
    for x in [1200, 2800]:
        prop("trashbin", x, 0)
    for x in [1600, 2000, 2400]:
        prop("table", x, 650)
    assert len(props) == 50
    placed = client.apply(props)
    assert placed["status"] == "applied", placed
    environment = client.edit("environment apply --preset noon")
    assert environment["status"] == "applied", environment
    checkpoint = client.edit("checkpoint save reference-baseline")
    assert checkpoint["status"] == "applied", checkpoint
    count = client.read("objects * --count")["count"]
    assert 300 <= count <= 500
    return {"entity_count": count, "prop_count": 50, "size_m": [24, 20],
            "geometry": geometry, "props": placed, "environment": environment,
            "checkpoint": checkpoint}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "tests/reports/v2-reference.json")
    args = parser.parse_args()
    result = build(Client(port=args.port))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), "utf-8")
    print({"entities": result["entity_count"], "props": result["prop_count"]})
