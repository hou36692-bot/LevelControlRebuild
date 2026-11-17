"""Deterministic placement arithmetic; only Client may commit the resulting operations."""

from __future__ import annotations

import copy
import math
import random
from typing import Any


def place_operations(catalog: dict[str, Any], data: dict[str, Any]) -> list[dict[str, Any]]:
    props = {item["id"]: item for item in catalog["props"]}
    prefabs = {item["id"]: item for item in catalog["prefabs"]}
    asset = data["asset"]
    if asset not in props and asset not in prefabs:
        raise ValueError(f"Unknown cooked asset: {asset}")
    count = data["count"]
    parts = (prefabs[asset]["components"] if asset in prefabs else
             [{"name": "", "asset": asset, "position_cm": [0, 0, 0],
               "rotation_deg": [0, 0, 0]}])
    if count * len(parts) > 16384:
        raise ValueError("Expanded placement exceeds 16384 entities")
    generator = random.Random(data["seed"])
    operations = []
    for index in range(count):
        root = data["id"] if count == 1 else f"{data['id']}-{index:03d}"
        origin = [x + index * delta for x, delta in zip(data["at"], data["step"], strict=True)]
        if data["columns"]:
            origin[0] = data["at"][0] + (index % data["columns"]) * data["step"][0]
            origin[1] = data["at"][1] + (index // data["columns"]) * data["step"][1]
        if data["grid"]:
            origin[:2] = [round(x / data["grid"]) * data["grid"] for x in origin[:2]]
        yaw = data["yaw"] + generator.uniform(-data["random_yaw"], data["random_yaw"])
        scale = generator.uniform(data["scale_min"], data["scale_max"])
        cosine, sine = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        for component in parts:
            prop = props[component["asset"]]
            x, y, z = [value * scale for value in component["position_cm"]]
            entity_id = root + ("-" + component["name"] if component["name"] else "")
            if len(entity_id) > 128:
                raise ValueError("Expanded entity ID exceeds 128 characters")
            entity = {
                "id": entity_id, "template": prop["id"], "surface": {"preset": prop["surface"]},
                "group": data["group"] or (root if asset in prefabs else data["id"]),
                "tags": prop["tags"], "collision": data["collision"] == "on", "lod": data["lod"],
                "transform": {"position_cm": [origin[0]+x*cosine-y*sine,
                                              origin[1]+x*sine+y*cosine, origin[2]+z],
                              "rotation_deg": [component["rotation_deg"][0],
                                               component["rotation_deg"][1]+yaw,
                                               component["rotation_deg"][2]],
                              "size_cm": [value * scale for value in prop["size_cm"]]},
            }
            operations.append({"op": "create", "entity": entity, "snap": data["snap"]})
    return operations


def layout_operations(entities: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
    if not entities:
        raise ValueError("No entities match the placement selector")
    if data["action"] == "align":
        axis = "xyz".index(data["axis"])
        values = [(item["bounds"]["min_cm"][axis] if data["mode"] == "min" else
                   item["bounds"]["max_cm"][axis] if data["mode"] == "max" else
                   item["transform"]["position_cm"][axis]) for item in entities]
        target = (min(values) if data["mode"] == "min" else max(values)
                  if data["mode"] == "max" else sum(values)/len(values))
        operations = []
        for item, value in zip(entities, values, strict=True):
            delta = [0.0, 0.0, 0.0]
            delta[axis] = target - value
            operations.append({"op": "move", "selector": item["id"], "delta_cm": delta})
        return operations
    if len(entities) * data["count"] > 16384:
        raise ValueError("Expanded array exceeds 16384 entities")
    operations = []
    for index in range(data["count"]):
        for ordinal, original in enumerate(entities):
            item = copy.deepcopy(original)
            item["id"] = f"{data['prefix']}-{index:03d}-{ordinal:03d}"
            if len(item["id"]) > 128:
                raise ValueError("Array entity ID exceeds 128 characters")
            for key in ["source", "version", "bounds"]:
                item.pop(key, None)
            item["transform"]["position_cm"] = [value + (index+1)*delta for value, delta
                in zip(item["transform"]["position_cm"], data["step"], strict=True)]
            operations.append({"op": "create", "entity": item})
    return operations
