"""Validate compact stage documents before changing a design card."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from lc.command import ID

KINDS = {"wall", "floor", "colonnade", "doorway", "stairs", "fountain"}
PARAMETERS = {
    "wall": {"sides", "thickness_m", "segment_m"},
    "floor": {"tile_m"},
    "colonnade": {"count", "diameter_m"},
    "doorway": {"opening_m", "opening_height_m"},
    "stairs": {"steps"},
    "fountain": {"segments", "rim_m"},
}


def number(value: Any, low: float, high: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be within {low}..{high}")
    return float(value)


def integer(value: Any, low: int, high: int, label: str) -> int:
    result = number(value, low, high, label)
    if int(result) != result:
        raise ValueError(f"{label} must be an integer")
    return int(result)


def vector(value: Any, positive: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("Expected a three-number vector")
    return [number(item, 0.01 if positive else -10000, 10000, "vector") for item in value]


def fields(value: Any, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - allowed:
        raise ValueError(f"Expected fields {sorted(required)}; allowed {sorted(allowed)}")
    return deepcopy(value)


def site(value: Any) -> dict[str, Any]:
    result = fields(value, {"center_m", "size_m"}, {"center_m", "size_m"})
    result["center_m"], result["size_m"] = vector(value["center_m"]), vector(value["size_m"], True)
    return result


def module(value: Any, bounds: dict[str, Any]) -> dict[str, Any]:
    required = {"id", "name", "kind", "center_m", "size_m"}
    result = fields(value, required | {"area", "yaw_deg", "params"}, required)
    result["id"] = integer(result["id"], 1, 99, "id")
    result["area"] = integer(result.get("area", 1), 1, 99, "area")
    if not isinstance(result["name"], str) or not ID.fullmatch(result["name"]):
        raise ValueError("name must be kebab-case")
    if result["kind"] not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    result["center_m"] = vector(result["center_m"])
    result["size_m"] = vector(result["size_m"], True)
    result["yaw_deg"] = number(result.get("yaw_deg", 0), -360, 360, "yaw_deg")
    result["params"] = fields(result.get("params", {}), PARAMETERS[result["kind"]], set())
    angle = math.radians(result["yaw_deg"])
    x, y, z = result["size_m"]
    extent = [
        abs(x * math.cos(angle)) + abs(y * math.sin(angle)),
        abs(x * math.sin(angle)) + abs(y * math.cos(angle)),
        z,
    ]
    for center, size, site_center, site_size in zip(
        result["center_m"], extent, bounds["center_m"], bounds["size_m"], strict=True
    ):
        if abs(center - site_center) + size / 2 > site_size / 2 + 1e-6:
            raise ValueError("Module bounds exceed the site")
    return result


def coarse(value: Any, bounds: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 99:
        raise ValueError("coarse requires 1..99 modules")
    result = [module(item, bounds) for item in value]
    if len({item["id"] for item in result}) != len(result):
        raise ValueError("Module ids must be unique")
    if len({item["name"] for item in result}) != len(result):
        raise ValueError("Module names must be unique")
    return result
