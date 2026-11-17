from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lc.client import Client


def clearance(client: "Client", start_cm: list[float], end_cm: list[float]) -> dict[str, Any]:
    return client.check({"kind": "clearance", "from_cm": start_cm, "to_cm": end_cm})
