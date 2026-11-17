"""The only coordinate/unit boundary between design documents and entity JSON."""

from __future__ import annotations

import math
from typing import Any

from lgg.constructors import construct
from lgg.schema import vector
from lgg.semantic import constructor_role


def from_y_up(value: list[float]) -> list[float]:
    """Explicit right-handed Y-up import; native drafts never call this adapter."""
    x, y, z = vector(value)
    return [x, -z, y]


def region(module: dict[str, Any]) -> dict[str, Any]:
    x, y, z = module["size_m"]
    angle = math.radians(module.get("yaw_deg", 0))
    size = [
        abs(x * math.cos(angle)) + abs(y * math.sin(angle)),
        abs(x * math.sin(angle)) + abs(y * math.cos(angle)),
        z,
    ]
    return {"center_cm": [v * 100 for v in module["center_m"]], "size_cm": [v * 100 for v in size]}


def compile_module(scene: str, module: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    angle = math.radians(module["yaw_deg"])
    for part in construct(module):
        x, y, z = part["position"]
        rotated = [
            x * math.cos(angle) - y * math.sin(angle),
            x * math.sin(angle) + y * math.cos(angle),
            z,
        ]
        result.append(
            {
                "id": f"{scene}-{module['area']:02d}-{module['id']:02d}-{part['slot']:02d}",
                "template": part["template"],
                "source": scene,
                "group": module["name"],
                "tags": [module["kind"], module["name"]],
                "semantic": {"role": constructor_role(module["kind"], part["slot"]),
                             "parent": f"{scene}-00-{module['id']:02d}-00",
                             "name": f"{module['name']} {part['slot'] + 1}",
                             "area": str(module["area"]), "pattern": module["name"],
                             "member_key": str(part["slot"]), "provenance": "design",
                             "identity_scope": "persistent"},
                "transform": {
                    "position_cm": [
                        round((a + b) * 100, 6)
                        for a, b in zip(rotated, module["center_m"], strict=True)
                    ],
                    "size_cm": [round(v * 100, 6) for v in part["size"]],
                    "rotation_deg": [0, part["yaw"] + module["yaw_deg"], 0],
                },
            }
        )
    return result


def compile_card(card: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for module in sorted(card["modules"], key=lambda item: (item["area"], item["id"])):
        if module["id"] in card["published"]:
            result.extend(compile_module(card["scene"], module))
        if len(result) > 16384:
            raise ValueError("A compiled design cannot exceed 16384 entities")
    return result


def compile_semantics(card: dict[str, Any]) -> dict[str, Any]:
    scene = card["scene"]
    root = scene + "-root"
    nodes = {root: {"id": root, "source": scene, "name": scene, "role": "structure",
                    "provenance": "design", "identity_scope": "persistent"}}
    for module in card["modules"]:
        if module["id"] not in card["published"]:
            continue
        identity = f"{scene}-00-{module['id']:02d}-00"
        nodes[identity] = {"id": identity, "source": scene, "parent": root,
                           "name": module["name"], "role": module["kind"],
                           "area": str(module["area"]),
                           "provenance": "design", "identity_scope": "persistent",
                           "local_frame": {"position_cm": [v * 100 for v in module["center_m"]],
                                           "yaw_deg": module["yaw_deg"]}}
    return nodes


def delta(previous: list[dict[str, Any]], desired: list[dict[str, Any]]) -> dict[str, list[str]]:
    def canonical(entity: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in entity.items() if k not in {"version", "bounds"}}

    before, after = ({e["id"]: canonical(e) for e in values} for values in (previous, desired))
    return {
        "create": sorted(after.keys() - before.keys()),
        "update": sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
        "delete": sorted(before.keys() - after.keys()),
    }
