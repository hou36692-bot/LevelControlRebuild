"""Single-designer, durable module delivery with one final world transaction."""

from __future__ import annotations

import hashlib
import json
import math
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from lc.client import Client
from presenter.camera_rules import shot
from presenter.timeline import timeline

from .build import BuildService, atomic_json, bounds, compile_plan, identifier, rotate
from .compiler import delta
from .schema import number, vector
from .stream_types import CoarsePlan, ModuleDelivery


def request_key(request_id: str) -> str:
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
        raise ValueError("request_id must contain 1..100 characters")
    return hashlib.sha256(request_id.encode()).hexdigest()


def layout(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = deepcopy(modules)
    by_id = {identifier(m["id"]): m for m in result}
    if len(by_id) != len(result):
        raise ValueError("Module IDs must be unique")
    visiting, done = set(), set()

    def resolve(identity: str) -> None:
        if identity in done:
            return
        if identity in visiting or identity not in by_id:
            raise ValueError("Unknown or cyclic module relation")
        visiting.add(identity)
        item = by_id[identity]
        item["size"] = vector(item["size"], True)
        item["yaw"] = number(item.get("yaw", 0), -360, 360, "yaw")
        target = item.get("relative_to")
        if target:
            if "at" in item or not item.get("side"):
                raise ValueError("Relative placement needs side and must not include at")
            resolve(target)
            other = by_id[target]
            axis, sign = {
                "left": (0, -1),
                "right": (0, 1),
                "front": (1, 1),
                "back": (1, -1),
                "above": (2, 1),
                "below": (2, -1),
            }[item["side"]]

            def extent(value: dict[str, Any]) -> float:
                size = value["size"]
                if axis == 2:
                    return size[2]
                angle = math.radians(value["yaw"])
                return abs(math.cos(angle)) * size[axis] + abs(math.sin(angle)) * size[1 - axis]

            item["at"] = list(other["at"])
            item["at"][axis] += sign * (
                (extent(item) + extent(other)) / 2 + number(item.get("gap", 0), 0, 1000, "gap")
            )
        else:
            if item.get("side") or item.get("gap", 0):
                raise ValueError("side/gap require relative_to")
            item["at"] = vector(item.get("at", [0, 0, 0]))
        for dependency in item.get("depends_on", []):
            resolve(dependency)
        visiting.remove(identity)
        done.add(identity)

    for identity in by_id:
        resolve(identity)
    return result


class StreamService:
    def __init__(self, client: Client, directory: Path | None = None) -> None:
        self.client = client
        self.builds = BuildService(client, directory)
        self.directory = self.builds.directory / "streams"

    def load(self, build_id: str) -> dict[str, Any]:
        path = self.directory / (identifier(build_id) + ".json")
        if not path.exists():
            raise ValueError("Unknown build_id; publish lgg_plan first")
        return json.loads(path.read_text("utf-8"))

    def save(self, record: dict[str, Any]) -> None:
        atomic_json(self.directory / (record["build_id"] + ".json"), record)

    def plan(self, plan: dict[str, Any], request_id: str) -> dict[str, Any]:
        plan = CoarsePlan.model_validate(plan).model_dump(mode="json", exclude_none=True)
        key = request_key(request_id)
        build_id = f"{plan['name'][:32]}-{key[:12]}"
        path = self.directory / (build_id + ".json")
        if path.exists():
            existing = self.load(build_id)
            if existing["original_plan"] != plan:
                raise ValueError("request_id was already used for a different plan")
            return existing["initial_result"]
        modules = layout(plan["modules"])
        if not self.client.health().get("upward_growth"):
            raise ValueError("This game does not support upward growth; update the runtime first")
        version = self.client.read("objects * --count")["version"]
        if plan["expected_version"] != version:
            raise ValueError("Stale expected_version; reread the world")
        state = self.client.read("presentation")
        if state["state"] in {"previewing", "settled", "committed"}:
            raise ValueError("Finish or cancel the active presentation before a new plan")
        catalog = self.client.read("catalog")
        templates = self.client.read("templates")["templates"]
        player = self.client.read("player-status")
        proxies = [
            {
                "id": m["id"],
                "asset": "blockout/box",
                "at": m["at"],
                "size": m["size"],
                "yaw": m["yaw"],
            }
            for m in modules
        ]
        proxy_plan = {"nodes": proxies, **({"anchor": plan["anchor"]} if "anchor" in plan else {})}
        envelope, anchor = compile_plan(proxy_plan, build_id, catalog, templates, {}, player)
        regions = []
        for item, entity in zip(modules, envelope, strict=True):
            low, high = bounds([entity])
            regions.append(
                {
                    "id": item["id"],
                    "label": item["label"],
                    "center_cm": [(a + b) / 2 for a, b in zip(low, high, strict=True)],
                    "size_cm": [b - a for a, b in zip(low, high, strict=True)],
                }
            )
        low, high = bounds(envelope)
        site = {
            "center_cm": [(a + b) / 2 for a, b in zip(low, high, strict=True)],
            "size_cm": [b - a for a, b in zip(low, high, strict=True)],
        }
        record = {
            "build_id": build_id,
            "original_plan": plan,
            "modules": modules,
            "anchor": anchor,
            "regions": regions,
            "site": site,
            "delivered": {},
            "catalog": catalog,
            "templates": templates,
            "player": player,
            "world_version": version,
            "revision": state["revision"] + 1,
            "started_utc_s": time.time(),
        }
        result = self.client.preview(self.preview(record, [], False), "plan-" + key[:32])
        if result["status"] != "applied":
            return result
        result = {
            **result,
            "build_id": build_id,
            "construction_status": "designing",
            "modules": [{k: m[k] for k in ("id", "label", "at", "size", "yaw")} for m in modules],
        }
        record["initial_result"] = result
        self.save(record)
        return result

    def preview(
        self,
        record: dict[str, Any],
        entities: list[dict[str, Any]],
        incremental: bool,
        complete: bool = False,
        animate_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        waiting = [
            m["label"]
            for m in record["modules"]
            if not record["delivered"].get(m["id"], {}).get("valid")
        ]
        caption = "规划已发布" if not entities else "结构向上生成中"
        if waiting:
            caption += " · 待交稿：" + "、".join(waiting)
        else:
            caption += " · 全部模块已交稿"
        times = timeline(entities, 3)
        if animate_ids is not None:
            included = set(animate_ids)
            times = [
                t if t["id"] in included else {"id": t["id"], "start_s": 0, "duration_s": 0}
                for t in times
            ]
        return {
            "source": record["build_id"],
            "revision": record["revision"],
            "incremental": incremental,
            "director": True,
            "seal": complete,
            "growth_duration_s": 3,
            "finish_delay_s": 1,
            "music": "none",
            "caption": caption[:160],
            "regions": record["regions"],
            "entities": entities,
            "timeline": times,
            **({"animate_ids": animate_ids} if animate_ids is not None else {}),
            "cameras": {
                "overview": shot(record["site"], 0.7, 1.2),
                "hero": shot(record["site"], 0.9, 0.55),
                "groups": {r["id"]: shot(r, 0.85, 0.8) for r in record["regions"]},
            },
        }

    def module(self, build_id: str, module: dict[str, Any], request_id: str) -> dict[str, Any]:
        module = ModuleDelivery.model_validate(module).model_dump(mode="json", exclude_none=True)
        key = request_key(request_id)
        delivery_path = self.directory / "deliveries" / (key + ".json")
        if delivery_path.exists():
            old = json.loads(delivery_path.read_text("utf-8"))
            if old["module"] != module or old["build_id"] != build_id:
                raise ValueError("request_id was already used for a different module")
            if old["result"].get("construction_status") == "scheduled":
                receipt = self.client.receipt(old["result"]["req_id"])
                if receipt["status"] != "unknown":
                    result = self.builds.finalize_receipt(receipt["req_id"], receipt)
                    return {**result, "build_id": build_id, "construction_status": result["status"]}
            return old["result"]
        record = self.load(build_id)
        brief = next((m for m in record["modules"] if m["id"] == module["id"]), None)
        if brief is None:
            raise ValueError("Module is not in the published coarse plan")
        prior = record["delivered"].get(module["id"], {})
        if module["revision"] != prior.get("revision", 0) + 1:
            raise ValueError(f"Expected module revision {prior.get('revision', 0) + 1}")
        if any(not record["delivered"].get(dep, {}).get("valid") for dep in brief["depends_on"]):
            raise ValueError("Deliver this module's dependencies first")
        state = self.client.read("presentation")
        version = self.client.read("objects * --count")["version"]
        incremental = state["source"] == build_id and state["state"] in {"previewing", "settled"}
        if not incremental:
            committed = self.client.receipt(record.get("commit_req_id", "no-commit"))
            if committed.get("status") != "applied" or module.get("expected_version") != version:
                raise ValueError(
                    "Build was cancelled, replaced or interrupted; no late delivery accepted"
                )
            if state["state"] not in {"idle", "completed", "cancelled"}:
                raise ValueError("Another presentation is active")
            record["world_version"] = version
        elif version != record["world_version"] or state["revision"] != record["revision"]:
            raise ValueError("World or preview changed; stale module delivery rejected")
        candidate = deepcopy(record)
        slots = deepcopy(prior.get("slots", {}))
        local_plan = {
            "nodes": module["nodes"],
            "definitions": module.get("definitions", {}),
            "anchor": {"at": [0, 0, 0], "yaw": 0},
        }
        entities, _ = compile_plan(
            local_plan, build_id, record["catalog"], record["templates"], slots, record["player"]
        )
        low, high = bounds(entities)
        if any(
            a < -size * 50 - 0.01 or b > size * 50 + 0.01
            for a, b, size in zip(low, high, brief["size"], strict=True)
        ):
            raise ValueError(
                "Module geometry exceeds its agreed envelope; keep nodes local to its center"
            )
        ordinal = record["modules"].index(brief) + 1
        angle = record["anchor"]["yaw"] + brief["yaw"]
        origin = [
            a + b
            for a, b in zip(
                record["anchor"]["at"], rotate(brief["at"], record["anchor"]["yaw"]), strict=True
            )
        ]
        for entity in entities:
            entity["id"] = entity["id"].replace(
                f"{build_id}-01-01-", f"{build_id}-01-{ordinal:02d}-"
            )
            entity["group"] = brief["id"]
            transform = entity["transform"]
            transform["position_cm"] = [
                round(a + b * 100, 6)
                for a, b in zip(rotate(transform["position_cm"], angle), origin, strict=True)
            ]
            transform["rotation_deg"][1] += angle
            if any(abs(v) > 1e6 for v in transform["position_cm"]):
                raise ValueError("Build exceeds supported world bounds")
        invalidated = set()
        if prior:
            dirty = {brief["id"]}
            while True:
                affected = {
                    m["id"] for m in record["modules"] if set(m["depends_on"]) & dirty
                } - dirty
                if not affected:
                    break
                dirty.update(affected)
            invalidated = dirty - {brief["id"]}
            for identity in invalidated:
                if identity in candidate["delivered"]:
                    candidate["delivered"][identity]["valid"] = False
        candidate["delivered"][brief["id"]] = {
            "revision": module["revision"],
            "valid": True,
            "module": module,
            "slots": slots,
            "entities": entities,
        }
        desired = [
            e
            for m in candidate["modules"]
            if candidate["delivered"].get(m["id"], {}).get("valid")
            for e in candidate["delivered"][m["id"]]["entities"]
        ]
        if len(desired) > 16384:
            raise ValueError("Expanded build exceeds 16384 entities")
        complete = all(
            candidate["delivered"].get(m["id"], {}).get("valid") for m in candidate["modules"]
        )
        candidate["revision"] = state["revision"] + 1
        animate_ids = None
        if not incremental:
            previous = [e for item in record["delivered"].values() for e in item["entities"]]
            changes = delta(previous, desired)
            animate_ids = changes["create"] + changes["update"]
        preview = self.client.preview(
            self.preview(candidate, desired, incremental, complete, animate_ids),
            "module-" + key[:32],
        )
        if preview["status"] != "applied":
            return preview
        result = {
            **preview,
            "build_id": build_id,
            "module_id": brief["id"],
            "module_revision": module["revision"],
            "entity_count": len(entities),
            "construction_status": "designing",
            "invalidated": sorted(invalidated),
            "pending_modules": [
                m["id"]
                for m in candidate["modules"]
                if not candidate["delivered"].get(m["id"], {}).get("valid")
            ],
        }
        if complete:
            commit_id = "growth-" + key[:32]
            body = {
                "source": build_id,
                "revision": candidate["revision"],
                "mode": "reconcile",
                "expected_version": candidate["world_version"],
                "entities": desired,
            }
            if "environment" in candidate["original_plan"]:
                body["environment"] = candidate["original_plan"]["environment"]
            saved = {
                "stream_build": True,
                "scene": build_id,
                "plan": candidate["original_plan"],
                "anchor": candidate["anchor"],
                "slots": {},
                "body": body,
                "request_id": commit_id,
                "entity_count": len(desired),
                "timings": {},
                "presentation_policy": {
                    "reason": "upward_modules",
                    "animate_ids": [e["id"] for e in desired],
                },
            }
            request_path = self.builds.directory / "requests" / (request_key(commit_id) + ".json")
            atomic_json(request_path, saved)
            candidate["commit_req_id"] = commit_id
            self.save(candidate)
            receipt = self.client.commit(body, commit_id)
            result = self.builds.finish(saved, receipt, request_path, time.perf_counter())
            result.update(
                build_id=build_id, construction_status=result["status"], pending_modules=[]
            )
        self.save(candidate)
        atomic_json(delivery_path, {"module": module, "build_id": build_id, "result": result})
        return result
