"""Overview, per-module and hero shots expressed in protocol centimeters."""

import itertools
import math
from typing import Any

from lgg.compiler import region


def shot(bounds: dict[str, Any], factor: float, height: float) -> dict[str, Any]:
    center, size = bounds["center_cm"], bounds["size_cm"]
    norm = math.sqrt(2 * factor * factor + height * height)
    forward = [factor / norm, factor / norm, -height / norm]
    right = [math.sqrt(0.5), -math.sqrt(0.5), 0]
    up = [
        right[1] * forward[2],
        -right[0] * forward[2],
        right[0] * forward[1] - right[1] * forward[0],
    ]
    horizontal = math.tan(math.radians(65 / 2))
    vertical = horizontal / (16 / 9)
    distance = 300.0
    for signs in itertools.product([-1, 1], repeat=3):
        corner = [sign * length / 2 for sign, length in zip(signs, size, strict=True)]
        depth = sum(a * b for a, b in zip(corner, forward, strict=True))
        for basis, tangent in [(right, horizontal), (up, vertical)]:
            projected = abs(sum(a * b for a, b in zip(corner, basis, strict=True)))
            distance = max(distance, projected / tangent - depth)
    # The shipped viewport is 16:9 with a 65-degree horizontal camera field of view.
    return {
        "position_cm": [a - b * distance * 1.12 for a, b in zip(center, forward, strict=True)],
        "target_cm": center,
    }


def cameras(card: dict[str, Any]) -> dict[str, Any]:
    bounds = region(card["site"])
    return {
        "overview": shot(bounds, 0.7, 1.2),
        "hero": shot(bounds, 0.9, 0.55),
        "groups": {m["name"]: shot(region(m), 0.85, 0.8) for m in card["modules"]},
    }
