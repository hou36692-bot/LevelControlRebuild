"""Turn a published design into one independent, replaceable presentation."""

from typing import Any

from lc.client import Client
from lgg.compiler import compile_card, region

from presenter.camera_rules import cameras
from presenter.timeline import timeline


def publish(
    client: Client, card: dict[str, Any], duration: float = 8, music: str = "none"
) -> dict[str, Any]:
    entities = compile_card(card)
    timing = timeline(entities, duration)
    if music != "none":
        from presenter.beats import timed

        timing = timed(entities, duration)
    state = client.read("presentation")
    incremental = (state.get("source") == card["scene"]
                   and state.get("state") in {"previewing", "settled"})
    return client.preview(
        {
            "source": card["scene"],
            "revision": card["revision"],
            "incremental": incremental,
            "director": True,
            "seal": bool(card.get("complete")),
            "caption": "结构向上生成中" if entities else "空间规划已发布",
            "regions": [region(m) for m in card["modules"]] or [region(card["site"])],
            "entities": entities,
            "timeline": timing,
            "music": music,
            "cameras": cameras(card),
        },
        request_id=card["preview_req_id"],
    )
