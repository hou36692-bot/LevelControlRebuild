from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lc.client import Client


def jump_check(
    client: "Client", start_cm: list[float], heading_deg: float = 0, speed_cm_s: float | None = None
) -> dict[str, Any]:
    data = {"kind": "jump", "start_cm": start_cm, "heading_deg": heading_deg}
    if speed_cm_s is not None:
        data["speed_cm_s"] = speed_cm_s
    return client.check(data)
