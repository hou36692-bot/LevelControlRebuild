"""Project-bound runtime context and recovery, independent of any agent frontend."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from lgg.build import BuildService
from mcp.types import CallToolResult, ImageContent, TextContent

from .client import Client


class RuntimeSession:
    def __init__(self, client: Client) -> None:
        self.client = client
        self.session_id: str | None = None

    def current_session(self) -> str:
        events = self.client._request("/v1/events?since=2147483647")
        return str(events["session_id"])

    def check(self) -> None:
        current = self.current_session()
        if self.session_id is not None and current != self.session_id:
            raise ConnectionError("Game restarted; call lc.context before issuing more tools")
        self.session_id = current

    def receipt(self, request_id: str) -> dict[str, Any]:
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError("request_id must contain 1..100 characters")
        receipt = self.client.receipt(request_id)
        service = BuildService(self.client)
        path = service.directory / "requests" / (hashlib.sha256(request_id.encode()).hexdigest()
                                                  + ".json")
        if path.exists() and receipt.get("status") not in {None, "unknown"}:
            return service.finalize_receipt(request_id, receipt)
        return receipt

    def context(self) -> dict[str, Any]:
        health = self.client.health()
        if not health.get("ok") or health.get("port") != self.client.port:
            raise ConnectionError("The configured game did not confirm its identity")
        if health.get("protocol") != 4 or not health.get("semantic_core"):
            raise ConnectionError(
                "This game does not expose semantic protocol 4; "
                "connect a semantic build instead")
        self.session_id = self.current_session()
        service = BuildService(self.client)
        recovered, warnings = [], []
        for path in sorted((service.directory / "requests").glob("*.json")):
            try:
                record = json.loads(path.read_text("utf-8"))
                if record.get("receipt", {}).get("status") not in {"applied", "rejected", "failed"}:
                    receipt = self.receipt(record["request_id"])
                    recovered.append({"request_id": record["request_id"],
                                      "status": receipt.get("status", "unknown")})
            except (OSError, ValueError, KeyError, ConnectionError) as error:
                warnings.append(f"Recovery {path.name}: {error}")
        scenes = []
        for path in sorted((service.directory / "scenes").glob("*.json")):
            record = json.loads(path.read_text("utf-8"))
            scenes.append({"scene": record["scene"],
                           "name": record["plan"].get("name", record["scene"]),
                           "entity_count": record["entity_count"],
                           "version": record.get("committed_version"),
                           "plan_path": str(path.resolve())})
        staged = []
        for path in sorted((service.directory / "streams").glob("*.json")):
            record = json.loads(path.read_text("utf-8"))
            modules = []
            for brief in record["modules"]:
                delivery = record["delivered"].get(brief["id"], {})
                modules.append({"id": brief["id"], "label": brief["label"],
                                "revision": delivery.get("revision", 0),
                                "delivered": delivery.get("valid", False)})
            staged.append({"build_id": record["build_id"], "plan_path": str(path.resolve()),
                           "modules": modules})
        summary = self.client.query({"mode": "summary"})
        if summary.get("status") != "ok":
            raise ConnectionError(
                "Semantic context unavailable: " + str(summary.get("warnings", summary)))
        return {"project_id": self.client.project_id, "port": self.client.port,
                "session_id": self.session_id, "world": health.get("world"),
                "world_version": summary["version"], "semantic_summary": summary,
                "player": self.client.read("player-status"),
                "request_id": "lc-" + uuid.uuid4().hex,
                "workspace": str(self.client.workspace), "scenes": scenes,
                "staged_builds": staged, "recovered": recovered, "warnings": warnings}


def tool_result(receipt: dict[str, Any]) -> CallToolResult:
    """Return receipt and completed PNGs without adding a model call."""
    content: list[TextContent | ImageContent] = [
        TextContent(type="text", text=json.dumps(receipt, ensure_ascii=False))]
    if receipt.get("status") == "applied":
        paths = [receipt["path"]] if receipt.get("path") else []
        paths.extend(item["path"] for item in receipt.get("captures", []) if item.get("path"))
        for name in paths[:4]:
            path = Path(name)
            try:
                if path.suffix.lower() != ".png" or path.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("Preview must be a PNG no larger than 16 MiB")
                data = path.read_bytes()
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Invalid PNG signature")
                content.append(ImageContent(type="image", mimeType="image/png",
                                            data=base64.b64encode(data).decode("ascii")))
            except (OSError, ValueError) as error:
                content.append(TextContent(type="text", text=f"Image unavailable: {error}"))
    return CallToolResult(content=content, structuredContent=receipt,
                          isError=receipt.get("status") in {"rejected", "failed", "unknown"})
