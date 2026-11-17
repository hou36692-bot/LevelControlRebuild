"""Build conservative walk connectivity, validating corridors against runtime collision."""

from collections import deque
from typing import TYPE_CHECKING, Any

from .clearance import clearance

if TYPE_CHECKING:
    from lc.client import Client


def walk_graph(
    client: "Client", selector: str, from_id: str | None = None, to_id: str | None = None
) -> dict[str, Any]:
    response = client.read(f"objects {selector} bounds --limit 256")
    if response.get("truncated"):
        raise ValueError("Walk graph supports at most 256 selected surfaces")
    if "entities" not in response:
        return response
    player = client.read("player-status")
    version = response["version"]
    radius = player["capsule_radius_cm"]
    step = player["max_step_height_cm"]
    nodes = {}
    bounds = {}
    for entity in response["entities"]:
        box = entity["bounds"]
        low, high = box["min_cm"], box["max_cm"]
        if min(high[0] - low[0], high[1] - low[1]) < 2 * radius:
            continue
        point = [(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, high[2] + 2]
        support = client.check({"kind": "support", "point_cm": point})
        if support.get("supported") and abs(support["point_cm"][2] - high[2]) <= 3:
            nodes[entity["id"]] = point
            bounds[entity["id"]] = box
    adjacency: dict[str, list[str]] = {key: [] for key in nodes}
    ids = sorted(nodes)
    for i, first in enumerate(ids):
        for second in ids[i + 1 :]:
            a, b = bounds[first], bounds[second]
            gaps = [
                max(a["min_cm"][axis] - b["max_cm"][axis], b["min_cm"][axis] - a["max_cm"][axis], 0)
                for axis in (0, 1)
            ]
            overlaps = [
                min(a["max_cm"][axis], b["max_cm"][axis])
                - max(a["min_cm"][axis], b["min_cm"][axis])
                for axis in (0, 1)
            ]
            if (
                max(gaps) > 2
                or max(overlaps) < 2 * radius
                or abs(nodes[first][2] - nodes[second][2]) > step
            ):
                continue
            height = max(nodes[first][2], nodes[second][2])
            check = clearance(client, [*nodes[first][:2], height], [*nodes[second][:2], height])
            if check.get("ok") and not check["blocked"]:
                adjacency[first].append(second)
                adjacency[second].append(first)
    path = []
    if from_id is not None or to_id is not None:
        if from_id not in nodes or to_id not in nodes:
            raise ValueError("Both route endpoints must be valid selected surfaces")
        queue = deque([from_id])
        previous = {from_id: None}
        while queue:
            current = queue.popleft()
            if current == to_id:
                while current is not None:
                    path.append(current)
                    current = previous[current]
                path.reverse()
                break
            for neighbor in adjacency[current]:
                if neighbor not in previous:
                    previous[neighbor] = current
                    queue.append(neighbor)
    return {
        "ok": True,
        "node_count": len(nodes),
        "edge_count": sum(map(len, adjacency.values())) // 2,
        "nodes": nodes,
        "edges": adjacency,
        "path": path,
        "reachable": bool(path) if from_id else None,
        "kind": "walk_only",
        "world_version": version,
        "player_parameters": {
            key: player[key]
            for key in ("capsule_radius_cm", "max_step_height_cm")
        },
        "valid_for_snapshot": client.read("objects * --count")["version"] == version,
    }
