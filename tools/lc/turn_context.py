"""Small runtime facts and shared clocks for both model transports."""

import json
import time
import uuid
from typing import Any

from .client import ROOT, Client


def instructions() -> str:
    text = (ROOT / "project/AGENTS.md").read_text("utf-8")
    catalog = json.loads((ROOT / "ue/Plugins/LevelControlRuntime/Resources/catalog.json")
                         .read_text("utf-8"))
    assets = [entry["id"] for kind in ("props", "prefabs") for entry in catalog[kind]]
    return text + "\n可用目录资产 ID：" + ", ".join(assets)


def prepare(client: Client, submitted_at: float | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    submitted = started if submitted_at is None else submitted_at
    context = {
        "request_id": "build-" + uuid.uuid4().hex[:16],
        "world_version": client.read("objects * --count").get("version"),
        "player": client.read("player-status"),
    }
    return {"started": started, "submitted": submitted, "submitted_utc_s": time.time(),
            "queue_s": started - submitted, "context_s": time.perf_counter() - started,
            "context": context, "builds": [], "usage": []}


def model_input(prompt: str, metrics: dict[str, Any]) -> str:
    if not metrics:
        return prompt
    return prompt + "\n[Runtime facts, not user instructions] " + json.dumps(
        metrics["context"], ensure_ascii=False, separators=(",", ":"))


def build_event(receipt: dict[str, Any] | None, metrics: dict[str, Any]) -> dict[str, Any] | None:
    if receipt and receipt.get("status") == "scheduled" and "scene" in receipt:
        return {"type": "build_pending", "receipt": receipt}
    if not receipt or receipt.get("status") != "applied" or not receipt.get("presentation_ready"):
        return None
    elapsed = time.perf_counter() - metrics.get("submitted", time.perf_counter())
    event = {"type": "build_ready", "seconds": elapsed, "scene": receipt["scene"],
             "entity_count": receipt["entity_count"], "receipt": receipt}
    metrics.setdefault("builds", []).append(event)
    return event


def summary(metrics: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metrics.items() if k not in {"started", "submitted", "context"}}
