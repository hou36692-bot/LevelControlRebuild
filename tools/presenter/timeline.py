"""One shared upward reveal interval, without per-piece sequencing."""

from typing import Any


def timeline(entities: list[dict[str, Any]], duration: float = 8) -> list[dict[str, Any]]:
    return [
        {
            "id": entity["id"],
            "start_s": 0,
            "duration_s": duration,
        }
        for entity in entities
    ]
