"""Durable design cards; game state remains exclusively behind lc.client."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from lc.client import ROOT, Client
from lc.command import parse
from presenter.phase_consumer import publish

from lgg import schema
from lgg.compiler import compile_card, compile_semantics, delta


class DesignService:
    def __init__(self, client: Client, path: Path | None = None) -> None:
        self.client = client
        self.path = (path or getattr(client, "design_file", None)
                     or ROOT / f"project/Saved/LevelControl/design-{client.port}.json")

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            raise ValueError("Call lgg_card init first")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, card: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(card, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        temporary.replace(self.path)

    def revision(self) -> int:
        return int(self.client.read("presentation").get("revision", 0)) + 1

    def status(self, card: dict[str, Any]) -> dict[str, Any]:
        presentation = self.client.read("presentation")
        world = self.client.read("objects * --count")
        commit = self.client.receipt(card["commit_req_id"]) if card.get("commit_req_id") else None
        return {
            "scene": card["scene"],
            "sequence": card["sequence"],
            "next_sequence": card["sequence"] + 1,
            "complete": card["complete"],
            "modules": card["modules"],
            "published": card["published"],
            "entities": len(compile_card(card)),
            "presentation": presentation,
            "commit": commit,
            "world_version": world.get("version"),
            "commit_version_current": bool(
                commit and commit.get("version") == world.get("version")
            ),
        }

    def invoke(self, tool: str, command: str) -> dict[str, Any]:
        args = parse(tool, command).data
        if tool == "lgg_card" and args["action"] == "init":
            cancelled = self.client.edit("presentation cancel")
            if cancelled["status"] != "applied":
                return cancelled
            card = dict(
                scene=args["scene"],
                sequence=0,
                site=None,
                modules=[],
                published=[],
                complete=False,
                revision=self.revision(),
                history={},
                commit_req_id=None,
            )
            self.save(card)
            return {
                "scene": card["scene"],
                "next_sequence": 1,
                "constitution": (ROOT / "tools/lgg/constitution.md").read_text("utf-8"),
                "syntax": self.client.help("lgg"),
                "player": self.client.read("player-status"),
            }
        card = self.load()
        if tool == "lgg_card" or (tool == "lgg_run" and args["action"] == "status"):
            return self.status(card)
        if tool == "lgg_run":
            if args["action"] == "cancel":
                return self.client.edit("presentation cancel")
            if not card["complete"]:
                raise ValueError("Finish all modules with --complete before play")
            # Replaying a pending run is idempotent. A new draft clears commit_req_id.
            if card.get("commit_req_id"):
                receipt = self.client.receipt(card["commit_req_id"])
                if receipt["status"] == "scheduled":
                    return receipt
                if receipt["status"] == "applied":
                    world = self.client.read("objects * --count")
                    if receipt.get("version") == world.get("version"):
                        return receipt
            state = self.client.read("objects * --count")
            if "version" not in state:
                return state
            card["revision"] = self.revision()
            card["preview_req_id"] = f"preview-{uuid.uuid4().hex}"
            result = publish(self.client, card, args["duration"], args["music"])
            if result["status"] != "applied":
                return result
            card["commit_req_id"] = f"build-{uuid.uuid4().hex}"
            card["commit_body"] = {
                "source": card["scene"],
                "revision": card["revision"],
                "mode": "reconcile",
                "expected_version": state["version"],
                "entities": compile_card(card),
                "semantic_nodes": compile_semantics(card),
            }
            self.save(card)
            return self.client.commit(card["commit_body"], card["commit_req_id"])
        return self.draft(card, args)

    def draft(self, card: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        sequence = args["sequence"]
        key = str(sequence)
        if key in card["history"]:
            record = card["history"][key]
            if record["args"] != args:
                raise ValueError("A published sequence cannot be reused with different content")
            return record["receipt"]
        if sequence != card["sequence"] + 1:
            raise ValueError(f"Expected #{card['sequence'] + 1}")
        candidate = deepcopy(card)
        stage, payload = args["stage"], args["payload"]
        if stage == "site":
            if card["sequence"] != 0 or args["complete"] or args["id"] is not None:
                raise ValueError("site must be #1 without --id or --complete")
            candidate["site"] = schema.site(payload)
        elif stage == "coarse":
            if (
                card["site"] is None
                or card["modules"]
                or args["complete"]
                or args["id"] is not None
            ):
                raise ValueError("Publish site first, then coarse without --complete or --id")
            candidate["modules"] = schema.coarse(payload, card["site"])
        else:
            if not card["modules"]:
                raise ValueError("Publish site and coarse before module")
            patch = schema.fields(payload, {"center_m", "size_m", "yaw_deg", "params"}, set())
            existing = next((m for m in candidate["modules"] if m["id"] == args["id"]), None)
            if existing is None:
                raise ValueError("Unknown module id; inspect lgg_card status")
            if "params" in patch:
                patch["params"] = {**existing["params"], **patch["params"]}
            existing.update(schema.module({**existing, **patch}, card["site"]))
            if args["id"] not in candidate["published"]:
                candidate["published"].append(args["id"])
        if args["complete"] and len(candidate["published"]) != len(candidate["modules"]):
            raise ValueError("--complete requires every coarse module to be published")
        candidate["complete"] = bool(args["complete"])
        compiled = compile_card(candidate)
        difference = delta(compile_card(card), compiled)
        candidate.update(
            sequence=sequence,
            revision=self.revision(),
            commit_req_id=None,
            preview_req_id=f"preview-{uuid.uuid4().hex}",
        )
        receipt = publish(self.client, candidate)
        if receipt["status"] != "applied":
            return receipt
        receipt.update(
            sequence=sequence,
            next_sequence=sequence + 1,
            complete=candidate["complete"],
            entity_count=len(compiled),
            delta={k: len(v) for k, v in difference.items()},
        )
        candidate["history"][key] = {"args": args, "receipt": receipt}
        self.save(candidate)
        return receipt
