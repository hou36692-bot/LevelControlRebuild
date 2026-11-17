"""Six whitebox constructors; fixed slots preserve identity across size changes."""

from __future__ import annotations

import math
from typing import Any

from lgg.schema import integer, number


def construct(module: dict[str, Any]) -> list[dict[str, Any]]:
    x, y, z = module["size_m"]
    p = module["params"]
    parts: list[dict[str, Any]] = []

    def add(
        slot: int, shape: str, position: list[float], size: list[float], yaw: float = 0
    ) -> None:
        if min(size) <= 0 or len(parts) >= 16384:
            raise ValueError("Invalid geometry or batch exceeds 16384 entities")
        parts.append(
            dict(slot=slot, template=f"blockout/{shape}", position=position, size=size, yaw=yaw)
        )

    kind = module["kind"]
    if kind == "floor":
        tile = number(p.get("tile_m", 2), 0.1, 100, "tile_m")
        nx, ny = math.ceil(x / tile), math.ceil(y / tile)
        if max(nx, ny) > 128:
            raise ValueError("floor supports at most 128 tiles per axis")
        for i in range(nx):
            for j in range(ny):
                add(
                    i * 128 + j,
                    "floor",
                    [-x / 2 + (i + 0.5) * x / nx, -y / 2 + (j + 0.5) * y / ny, 0],
                    [x / nx, y / ny, z],
                )
    elif kind == "wall":
        sides = p.get("sides", ["north", "east", "west"])
        if (
            not isinstance(sides, list)
            or not sides
            or len(set(sides)) != len(sides)
            or not set(sides) <= {"north", "east", "south", "west"}
        ):
            raise ValueError("sides must be unique north/east/south/west names")
        thickness = number(p.get("thickness_m", 0.25), 0.05, min(x, y) / 2, "thickness_m")
        segment = number(p.get("segment_m", 2), 0.1, 100, "segment_m")
        for side_index, side in enumerate(["north", "east", "south", "west"]):
            if side not in sides:
                continue
            horizontal = side in {"north", "south"}
            span = x if horizontal else y - 2 * thickness
            count = math.ceil(span / segment)
            if count > 1000:
                raise ValueError("wall supports at most 1000 pieces per side")
            for i in range(count):
                along = -span / 2 + (i + 0.5) * span / count
                across = ((y if horizontal else x) - thickness) / 2
                across *= 1 if side in {"north", "east"} else -1
                add(
                    side_index * 1000 + i,
                    "wall",
                    [along, across, 0] if horizontal else [across, along, 0],
                    [span / count, thickness, z] if horizontal else [thickness, span / count, z],
                )
    elif kind == "colonnade":
        count = integer(p.get("count", 6), 2, 256, "count")
        diameter = number(p.get("diameter_m", 0.5), 0.05, min(x, y), "diameter_m")
        if diameter > x / count:
            raise ValueError("Columns would overlap")
        for i in range(count):
            add(
                i,
                "column",
                [-x / 2 + diameter / 2 + i * (x - diameter) / (count - 1), 0, 0],
                [diameter, diameter, z],
            )
    elif kind == "doorway":
        opening = number(p.get("opening_m", 2), 0.8, x - 0.1, "opening_m")
        height = number(p.get("opening_height_m", 2.4), 1.8, z - 0.05, "opening_height_m")
        width = (x - opening) / 2
        add(0, "wall", [-(x + opening) / 4, 0, 0], [width, y, z])
        add(1, "wall", [(x + opening) / 4, 0, 0], [width, y, z])
        add(2, "wall", [0, 0, height / 2], [opening, y, z - height])
    elif kind == "stairs":
        count = integer(p.get("steps", 8), 1, 256, "steps")
        if z / count > 0.4 or x / count < 0.2:
            raise ValueError("Stairs require rise <= 0.4 m and tread >= 0.2 m")
        for i in range(count):
            height = z * (i + 1) / count
            add(
                i,
                "box",
                [-x / 2 + (i + 0.5) * x / count, 0, -z / 2 + height / 2],
                [x / count, y, height],
            )
    elif kind == "fountain":
        count = integer(p.get("segments", 16), 8, 64, "segments")
        diameter = min(x, y)
        rim = number(p.get("rim_m", 0.3), 0.05, diameter / 5, "rim_m")
        base = z * 0.1
        add(0, "column", [0, 0, -z / 2 + base / 2], [diameter, diameter, base])
        add(1, "column", [0, 0, 0], [diameter * 0.16, diameter * 0.16, z * 0.8])
        add(2, "column", [0, 0, z * 0.4], [diameter * 0.45, diameter * 0.45, z * 0.2])
        radius = diameter / 2 - rim
        for i in range(count):
            angle = i * 2 * math.pi / count
            add(
                10 + i,
                "wall",
                [radius * math.cos(angle), radius * math.sin(angle), -z / 2 + base + z * 0.12],
                [rim, 2 * radius * math.tan(math.pi / count), z * 0.24],
                math.degrees(angle),
            )
    else:
        raise ValueError(f"Unknown constructor: {kind}")
    return parts
