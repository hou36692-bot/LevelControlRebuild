"""Compile local assemblies into ordinary, transactional runtime entities."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from lc.client import ROOT, Client
from lc.command import ID
from presenter import build_policy

from lgg.constructors import construct
from lgg.schema import KINDS, PARAMETERS, fields, integer, number, vector
from lgg.semantic import apply_overrides, constructor_role, metadata


def identifier(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64 or not ID.fullmatch(value):
        raise ValueError("IDs must be kebab-case, at most 64 characters")
    return value


def semantic_key(value: str) -> str:
    if len(value) > 256:
        raise ValueError(
            "Semantic member key exceeds 256 characters; shorten IDs or nesting")
    return value


def rotate(point: list[float], yaw: float) -> list[float]:
    angle = math.radians(yaw)
    x, y, z = point
    return [x * math.cos(angle) - y * math.sin(angle),
            x * math.sin(angle) + y * math.cos(angle), z]


def offsets(repeat: Any) -> list[tuple[str, list[float]]]:
    if repeat is None:
        return [("0", [0, 0, 0])]
    data = fields(repeat, {"count", "step", "span", "members"}, {"count"})
    if ("step" in data) == ("span" in data):
        raise ValueError("repeat needs exactly one of step or span")
    count = data["count"]
    if "span" in data:
        span = vector(data["span"])
        counts = count if isinstance(count, list) else [count] * 3
        if len(counts) != 3:
            raise ValueError("Grid count needs three integers")
        counts = [integer(n, 1, 16384, "count") for n in counts]
        if any(n == 1 and abs(v) > 1e-9 for n, v in zip(counts, span, strict=True)):
            raise ValueError("A one-item array must have zero span")
        step = [v / max(1, n - 1) for v, n in zip(span, counts, strict=True)]
    else:
        step = vector(data["step"])
    if isinstance(count, list):
        if len(count) != 3:
            raise ValueError("Grid count needs three integers")
        counts = [integer(n, 1, 16384, "count") for n in count]
        if math.prod(counts) > 16384:
            raise ValueError("Expanded array exceeds 16384 entities")
        result = [(f"{x},{y},{z}", [x * step[0], y * step[1], z * step[2]])
                  for x, y, z in itertools.product(*(range(n) for n in counts))]
    else:
        result = [(str(i), [i * v for v in step])
                  for i in range(integer(count, 1, 16384, "count"))]
    if "members" in data:
        members = data["members"]
        if not isinstance(members, list) or len(members) != len(result):
            raise ValueError("repeat.members must match the expanded count")
        members = [identifier(item) for item in members]
        if len(set(members)) != len(members):
            raise ValueError("repeat.members must be unique")
        result = [(key, item[1]) for key, item in zip(members, result, strict=True)]
    return result


def bounds(entities: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    low, high = [math.inf] * 3, [-math.inf] * 3
    for entity in entities:
        transform = entity["transform"]
        x, y, z = transform["size_cm"]
        angle = math.radians(transform["rotation_deg"][1])
        size = [abs(x * math.cos(angle)) + abs(y * math.sin(angle)),
                abs(x * math.sin(angle)) + abs(y * math.cos(angle)), z]
        for axis in range(3):
            p = transform["position_cm"][axis]
            low[axis] = min(low[axis], p - size[axis] / 2)
            high[axis] = max(high[axis], p + size[axis] / 2)
    return low, high


def compile_plan(plan: dict[str, Any], scene: str, catalog: dict[str, Any],
                 templates: list[dict[str, Any]], slots: dict[str, int],
                 player: dict[str, Any], previous_anchor: dict[str, Any] | None = None,
                 semantic_out: dict[str, Any] | None = None,
                 ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fields(plan, {"name", "scene", "expected_version", "anchor", "definitions", "nodes",
                  "environment", "intent", "presentation", "override_policy",
                  "constraints"}, {"nodes"})
    definitions = plan.get("definitions", {})
    if not isinstance(definitions, dict) or len(definitions) > 99:
        raise ValueError("definitions must contain at most 99 named assemblies")
    for name in definitions:
        identifier(name)
    assets = {item["id"]: item for item in templates}
    assets.update({item["id"]: item for item in catalog["props"]})
    prefabs = {item["id"]: item for item in catalog["prefabs"]}
    entities: list[dict[str, Any]] = []
    visited = 0
    next_slot = max(slots.values(), default=-1) + 1
    logical: dict[str, Any] = {}
    author_keys: set[str] = set()
    references: dict[str, str] = {}
    root_id = scene + "-root"
    logical[root_id] = {"id": root_id, "name": plan.get("name", scene), "role": "structure",
                        "source": scene, "provenance": "design", "identity_scope": "persistent"}

    def logical_id(key: str) -> str:
        nonlocal next_slot
        key = "@logical" + key
        if key not in slots:
            slots[key] = next_slot
            next_slot += 1
        return f"{scene}-00-00-{slots[key]:02d}"

    def emit(path: str, asset: str, at: list[float], size: list[float], yaw: float,
             surface: dict[str, Any], collision: bool, group: str,
             parent: str, semantic: dict[str, Any]) -> None:
        nonlocal next_slot
        if asset not in assets:
            raise ValueError(f"Unknown cooked asset: {asset}")
        if len(entities) >= 16384:
            raise ValueError("Expanded build exceeds 16384 entities")
        vector(at)
        vector(size, True)
        if path not in slots:
            slots[path] = next_slot
            next_slot += 1
        base_surface = assets[asset].get("surface")
        material = {"preset": base_surface} if isinstance(base_surface, str) else {}
        material.update(surface)
        if material:
            material.setdefault("preset", "stone-limestone")
        entities.append({
            "id": f"{scene}-01-01-{slots[path]:02d}", "source": scene,
            "template": asset, "group": group, "tags": ["build", group],
            "semantic": {**semantic, "parent": parent,
                         "member_key": semantic_key(path),
                         "identity_scope": "persistent"},
            "collision": collision, **({"surface": material} if material else {}),
            "transform": {"position_cm": [v * 100 for v in at],
                          "size_cm": [v * 100 for v in size], "rotation_deg": [0, yaw, 0]},
        })

    def nodes(items: Any, path: str, origin: list[float], angle: float,
              surface: dict[str, Any], collision: bool, group: str,
              depth: int, stack: tuple[str, ...], parent: str, inherited_semantic: dict) -> None:
        if not isinstance(items, list) or not 1 <= len(items) <= 256:
            raise ValueError("nodes/parts must contain 1..256 named components")
        names = [identifier(item.get("id")) if isinstance(item, dict) else identifier(None)
                 for item in items]
        if len(set(names)) != len(names):
            raise ValueError("Sibling component IDs must be unique")
        for item, name in zip(items, names, strict=True):
            node(item, path + "/" + name, origin, angle, surface, collision,
                 group or name, depth, stack, parent, inherited_semantic)

    def node(item: dict[str, Any], path: str, origin: list[float], angle: float,
             inherited: dict[str, Any], collision: bool, group: str,
             depth: int, stack: tuple[str, ...], parent: str, inherited_semantic: dict) -> None:
        nonlocal visited
        visited += 1
        if depth > 12 or visited > 32768:
            raise ValueError("Assembly nesting/work budget exceeded")
        allowed = {"id", "asset", "kind", "parts", "use", "at", "size", "yaw", "surface",
                   "collision", "repeat", "params", "semantic", "key"}
        fields(item, allowed, set())
        variants = {"asset", "kind", "parts", "use"} & item.keys()
        if len(variants) != 1:
            raise ValueError("A component needs exactly one of asset/kind/parts/use")
        if "params" in item and "kind" not in item:
            raise ValueError("params is only valid for constructor kinds")
        if "size" in item and variants & {"parts", "use"}:
            raise ValueError("Set sizes on leaf components, not assemblies")
        local = vector(item.get("at", [0, 0, 0]))
        yaw = angle + number(item.get("yaw", 0), -3600, 3600, "yaw")
        surface = item.get("surface", {})
        if isinstance(surface, str):
            surface = {"preset": surface}
        material = {**inherited, **fields(surface, {
            "preset", "tint", "roughness", "metallic", "emissive", "normal_strength",
            "wetness"}, set())}
        collision = item.get("collision", collision)
        if not isinstance(collision, bool):
            raise ValueError("collision must be boolean")
        semantic = metadata(item.get("semantic"), inherited_semantic)
        semantic.setdefault("name", item.get("id", group))
        if "key" in item:
            stable = identifier(item["key"])
            # Definition keys are scoped to the assembly instance, author keys are global.
            path = path.rsplit("/", 1)[0] + "/@" + stable if stack else "/@" + stable
            if path in author_keys:
                raise ValueError(f"Duplicate immutable author key: {stable}")
            author_keys.add(path)
        for index, offset in offsets(item.get("repeat")):
            translated = rotate([a + b for a, b in zip(local, offset, strict=True)], angle)
            at = [a + b for a, b in zip(origin, translated, strict=True)]
            key = path + "/" + index
            identity = parent
            is_logical = bool(variants & {"parts", "use", "kind"} or item.get("asset") in prefabs)
            if is_logical:
                identity = logical_id(key)
                logical[identity] = {**semantic, "id": identity, "parent": parent,
                    "source": scene, "member_key": semantic_key(key),
                    "identity_scope": "persistent",
                    "local_frame": {"position_m": at, "yaw_deg": yaw}}
                if "kind" in item:
                    logical[identity].setdefault("role", item["kind"])
            member_semantic = {**semantic, "member_key": index}
            if "repeat" in item:
                member_semantic.setdefault("pattern", path)
                if identity in logical:
                    logical[identity].setdefault("pattern", path)
            if "parts" in item:
                nodes(item["parts"], key, at, yaw, material, collision, group, depth + 1, stack,
                      identity, member_semantic)
            elif "use" in item:
                name = identifier(item["use"])
                if name in stack or name not in definitions:
                    raise ValueError(f"Unknown or cyclic assembly reference: {name}")
                node(definitions[name], key + "/" + name, at, yaw, material, collision,
                     group, depth + 1, (*stack, name), identity, member_semantic)
            elif "kind" in item:
                kind = item["kind"]
                if not isinstance(kind, str) or kind not in KINDS:
                    raise ValueError("Unknown constructor kind")
                size = vector(item.get("size"), True)
                params = fields(item.get("params", {}), PARAMETERS[kind], set())
                for part in construct({"kind": kind, "size_m": size, "params": params}):
                    p = rotate(part["position"], yaw)
                    emit(key + "/" + str(part["slot"]), part["template"],
                         [a + b for a, b in zip(at, p, strict=True)], part["size"],
                         yaw + part["yaw"], material, collision, group, identity,
                         {**metadata({}, member_semantic),
                          "role": constructor_role(kind, part["slot"]),
                          "name": f"{semantic['name']} {part['slot'] + 1}"})
            else:
                asset = item["asset"]
                if not isinstance(asset, str):
                    raise ValueError("asset must be a cooked asset ID")
                if asset in prefabs:
                    if "size" in item:
                        raise ValueError("Prefab size is defined by its components")
                    for part in prefabs[asset]["components"]:
                        if part["rotation_deg"][0] or part["rotation_deg"][2]:
                            raise ValueError("This prefab needs unsupported pitch/roll composition")
                        p = rotate([v / 100 for v in part["position_cm"]], yaw)
                        emit(key + "/" + part["name"], part["asset"],
                             [a + b for a, b in zip(at, p, strict=True)],
                             [v / 100 for v in assets[part["asset"]]["size_cm"]],
                             yaw + part["rotation_deg"][1], material, collision, group, identity,
                             {**metadata({}, member_semantic), "name": part["name"]})
                else:
                    if asset not in assets:
                        raise ValueError(f"Unknown cooked asset: {asset}")
                    size = item.get("size", [v / 100 for v in assets[asset]["size_cm"]])
                    emit(key, asset, at, vector(size, True), yaw, material, collision, group,
                         identity, member_semantic)
            if "key" in item:
                reference = (path.lstrip("/") if stack else item["key"]) + (
                    "/" + index if "repeat" in item else "")
                if reference in references:
                    raise ValueError(f"Ambiguous semantic reference: {reference}")
                references[reference] = identity if is_logical else entities[-1]["id"]

    nodes(plan["nodes"], "", [0, 0, 0], 0, {}, True, "", 0, (), root_id, {})
    low, _ = bounds(entities)
    requested_anchor = plan.get("anchor", {})
    # A saved front-relative placement must not move a scene when the player moves.
    anchor = deepcopy(previous_anchor if previous_anchor and "at" not in requested_anchor
                      else requested_anchor)
    fields(anchor, {"front_m", "at", "yaw"}, set())
    if "at" in anchor:
        if "front_m" in anchor:
            raise ValueError("Use an explicit anchor or front_m, not both")
        position = vector(anchor["at"])
        yaw = number(anchor.get("yaw", 0), -3600, 3600, "anchor yaw")
    else:
        if "yaw" in anchor:
            raise ValueError("Explicit anchor yaw requires at")
        gap = number(anchor.get("front_m", 2), 0.5, 1000, "front_m")
        forward = player["forward"]
        yaw = math.degrees(math.atan2(forward[1], forward[0])) - 90
        distance = gap - low[1] / 100
        position = [player["feet_cm"][i] / 100 + forward[i] * distance for i in range(2)]
        position.append(player["feet_cm"][2] / 100)
    anchor = {"at": position, "yaw": yaw}
    for entity in entities:
        transform = entity["transform"]
        point = rotate(transform["position_cm"], yaw)
        transform["position_cm"] = [round(p + o * 100, 6)
                                     for p, o in zip(point, position, strict=True)]
        transform["rotation_deg"][1] += yaw
        if any(abs(v) > 1e6 for v in transform["position_cm"]):
            raise ValueError("Build exceeds the supported world bounds")
    for value in logical.values():
        if "local_frame" in value:
            frame = value["local_frame"]
            point = rotate(frame.pop("position_m"), yaw)
            frame["position_cm"] = [(p + o) * 100 for p, o in zip(point, position, strict=True)]
            frame["yaw_deg"] += yaw
    for semantic in [*logical.values(), *(entity["semantic"] for entity in entities)]:
        for relation in semantic.get("relations", []):
            if relation["target"].startswith("key:"):
                key = relation["target"][4:]
                if key not in references:
                    raise ValueError(f"Unknown semantic author key: {key}")
                relation["target"] = references[key]
    if semantic_out is not None:
        semantic_out.update({
            "nodes": logical,
            "constraints": deepcopy(plan.get("constraints", [])),
        })
    return entities, anchor


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), "utf-8")
    temporary.replace(path)


class BuildService:
    def __init__(self, client: Client, directory: Path | None = None) -> None:
        self.client = client
        data_dir = getattr(client, "data_dir", None)
        self.directory = directory or (data_dir / "builds" if data_dir else
                                      ROOT / f"project/Saved/LevelControl/builds/{client.port}")

    def build(self, plan: dict[str, Any], request_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError("request_id must contain 1..100 characters")
        encoded = json.dumps(plan, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > 1024 * 1024:
            raise ValueError("Build plan exceeds 1 MiB")
        digest = hashlib.sha256(request_id.encode()).hexdigest()
        request_path = self.directory / "requests" / f"{digest}.json"
        if request_path.exists():
            record = json.loads(request_path.read_text("utf-8"))
            if record["plan"] != plan:
                raise ValueError("request_id was already used for a different plan")
            if record.get("receipt", {}).get("status") in {"rejected", "failed"}:
                return record.get("result", record["receipt"])
            receipt = self.client.receipt(request_id)
            if receipt.get("status") not in {"unknown", None}:
                return self.finish(record, receipt, request_path, started)
            raise ValueError("Previous request has no recoverable receipt; inspect before retrying")
        name = identifier(plan.get("name", "build"))
        scene = identifier(plan["scene"]) if "scene" in plan else f"{name[:40]}-{digest[:12]}"
        scene_path = self.directory / "scenes" / f"{scene}.json"
        previous = {}
        if "scene" in plan:
            if not scene_path.exists() or "expected_version" not in plan:
                raise ValueError("Updating a scene requires its saved plan and expected_version")
            previous = json.loads(scene_path.read_text("utf-8"))
            if previous.get("stream_build"):
                raise ValueError("Use lgg_module with this scene's build_id to preserve module IDs")
        state = self.client.read("objects * --count")
        version = state["version"]
        if "expected_version" in plan:
            expected = integer(plan["expected_version"], 0, 1e12, "expected_version")
            if expected != version:
                raise ValueError("Stale expected_version; reread the world")
        catalog = self.client.read("catalog")
        templates = self.client.read("templates")["templates"]
        slots = deepcopy(previous.get("slots", {}))
        compile_start = time.perf_counter()
        semantics: dict[str, Any] = {}
        entities, anchor = compile_plan(
            plan, scene, catalog, templates, slots,
            self.client.read("player-status"), previous.get("anchor"), semantics)
        runtime_semantics = self.client.semantic_state(scene)
        if runtime_semantics.get("version") != version:
            raise ValueError("World changed while reading instance overrides; reread context")
        generated = deepcopy(entities)
        entities = apply_overrides(entities, runtime_semantics.get("overrides", {}),
                                   plan.get("override_policy", "preserve"))
        if not entities:
            raise ValueError(
                "All generated members are deleted; "
                "explicitly discard overrides to rebuild")
        compile_s = time.perf_counter() - compile_start
        environment = fields(plan["environment"], {"preset", "values"}, set()) \
            if "environment" in plan else None
        presentation = self.client.read("presentation")
        before = self.client.read(f"objects {scene}-* --limit 16384").get("entities", []) \
            if plan.get("scene") else []
        policy = build_policy.select(plan, before, entities, presentation.get("commit_held", False))
        revision = int(presentation["revision"]) + 1
        animated = set(policy["animate_ids"])
        low, high = bounds([e for e in entities if e["id"] in animated] or entities)
        region = {"center_cm": [(a + b) / 2 for a, b in zip(low, high, strict=True)],
                  "size_cm": [b - a for a, b in zip(low, high, strict=True)]}
        body = {"source": scene, "revision": revision, "mode": "reconcile",
                "expected_version": version, "entities": entities,
                "semantic_nodes": semantics["nodes"], "constraints": semantics["constraints"],
                "override_policy": plan.get("override_policy", "preserve"),
                **({"environment": environment} if environment is not None else {})}
        record = {"plan": plan, "scene": scene, "slots": slots, "anchor": anchor,
                  "body": body, "generated": generated, "request_id": request_id,
                  "entity_count": len(entities),
                  "timings": {"compile_s": compile_s}, "created_utc_s": time.time(),
                  "presentation_policy": policy}
        atomic_json(request_path, record)
        preview_start = time.perf_counter()
        preview = self.client.preview({
            "source": scene, "revision": revision, "regions": [region],
            "entities": entities,
            **build_policy.preview(policy, entities, region),
        }, f"preview-{digest[:32]}")
        record["timings"]["preview_s"] = time.perf_counter() - preview_start
        if preview["status"] != "applied":
            record["receipt"] = preview
            atomic_json(request_path, record)
            return preview
        commit_start = time.perf_counter()
        receipt = self.client.commit(body, request_id)
        deadline = time.perf_counter() + self.client.timeout
        hold_check = time.perf_counter() + 0.25
        while receipt["status"] == "scheduled" and time.perf_counter() < deadline:
            time.sleep(0.01)
            receipt = self.client.receipt(request_id)
            if receipt["status"] == "scheduled" and time.perf_counter() >= hold_check:
                if self.client.read("presentation").get("commit_held"):
                    break
                hold_check = time.perf_counter() + 0.25
        record["timings"]["commit_observed_s"] = time.perf_counter() - commit_start
        return self.finish(record, receipt, request_path, started)

    def finalize_receipt(self, request_id: str, receipt: dict[str, Any]) -> dict[str, Any]:
        path = self.directory / "requests" / (hashlib.sha256(request_id.encode()).hexdigest()
                                               + ".json")
        record = json.loads(path.read_text("utf-8"))
        return self.finish(record, receipt, path, time.perf_counter())

    def finish(self, record: dict[str, Any], receipt: dict[str, Any],
               path: Path, started: float) -> dict[str, Any]:
        state = self.client.read("presentation")
        if receipt["status"] in {"rejected", "failed"} and \
                state.get("source") == record["scene"] and \
                state.get("revision") == record["body"]["revision"] and \
                state.get("state") in {"previewing", "settled"}:
            self.client.edit("presentation cancel")
            state = self.client.read("presentation")
        if receipt["status"] == "applied":
            deadline = time.perf_counter() + 2
            while state.get("source") == record["scene"] and state["state"] == "committed" \
                    and time.perf_counter() < deadline:
                time.sleep(0.01)
                state = self.client.read("presentation")
            scene_path = self.directory / "scenes" / f"{record['scene']}.json"
            saved = json.loads(scene_path.read_text("utf-8")) if scene_path.exists() else {}
            record["committed_version"] = receipt["version"]
            if receipt["version"] >= saved.get("committed_version", -1):
                atomic_json(scene_path, record)
        current = self.client.read("objects * --count").get("version") == receipt.get("version")
        presentation_matches = ((state.get("source") == record["scene"]
                                 and state.get("state") == "completed")
                                or state.get("state") == "idle")
        ready = (current and receipt["status"] == "applied" and presentation_matches
                 and state.get("camera") == "player")
        result = {**{k: v for k, v in receipt.items() if k != "entity_ids"},
                  "scene": record["scene"], "entity_count": record["entity_count"],
                  "presentation_ready": ready,
                  "world_version_current": current,
                  "presentation": {**{k: v for k, v in record.get("presentation_policy", {}).items()
                                       if k != "animate_ids"},
                                   "animated_count": len(record.get("presentation_policy", {})
                                                         .get("animate_ids", [])),
                                   "skip_reason": state.get("skip_reason", ""),
                                   "elapsed_s": state.get("elapsed_s", 0)},
                  "timings": {**record["timings"], "tool_total_s": time.perf_counter() - started}}
        if receipt["status"] == "scheduled":
            result["revision"] = record["body"]["revision"]
        if record.get("stream_build"):
            result.update(build_id=record["scene"], construction_status=receipt["status"])
        record.update(receipt=receipt, result=result, presentation=state)
        atomic_json(path, record)
        return result
