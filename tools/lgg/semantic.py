"""Semantic metadata and three-way instance overrides; no engine access."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .schema import fields

SEMANTIC_FIELDS = {"name", "aliases", "role", "area", "pattern", "ring", "side",
                   "provenance", "relations"}
INHERITED = {"area", "pattern", "ring", "side"}


def metadata(value: Any, inherited: dict | None = None) -> dict:
    result = {k: v for k, v in (inherited or {}).items() if k in INHERITED}
    result.update(fields(value or {}, SEMANTIC_FIELDS, set()))
    for key in SEMANTIC_FIELDS - {"aliases", "relations"}:
        if key in result and (not isinstance(result[key], str) or len(result[key]) > 256):
            raise ValueError(f"semantic.{key} must be a string of at most 256 characters")
    aliases = result.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 32 or any(
        not isinstance(v, str) or not v or len(v) > 256 for v in aliases
    ):
        raise ValueError("semantic.aliases requires at most 32 nonempty strings")
    result.setdefault("provenance", "design")
    if result["provenance"] not in {"design", "manual", "inferred"}:
        raise ValueError("Unknown semantic provenance")
    relations = result.get("relations", [])
    if not isinstance(relations, list) or len(relations) > 64:
        raise ValueError("semantic.relations requires at most 64 relations")
    for relation in relations:
        fields(relation, {"kind", "target"}, {"kind", "target"})
        if any(not isinstance(v, str) or not v or len(v) > 256 for v in relation.values()):
            raise ValueError("Relation kind and target must be nonempty strings")
    return result


def apply_overrides(entities: list[dict], overrides: dict, policy: str = "preserve") -> list[dict]:
    if policy not in {"preserve", "discard"}:
        raise ValueError("override_policy must be preserve or discard")
    if policy == "discard":
        return entities
    desired = {e["id"]: deepcopy(e) for e in entities}
    conflicts = []
    for identity, override in overrides.items():
        if override.get("deleted"):
            desired.pop(identity, None)
            continue
        if override.get("added"):
            entity = override.get("entity")
            if not isinstance(entity, dict) or not entity.get("id"):
                conflicts.append(f"{identity}: added override has no entity")
                continue
            if identity in desired and desired[identity] != entity:
                conflicts.append(f"{identity}: added identity collides with design")
            else:
                desired[identity] = deepcopy(entity)
            continue
        if identity not in desired:
            conflicts.append(f"{identity}: edited member removed from design")
            continue
        for key, patch in override.get("fields", {}).items():
            target = desired[identity]
            pieces = key.split(".")
            for part in pieces[:-1]:
                target = target.setdefault(part, {})
            before, after = patch.get("base"), patch.get("value")
            current = target.get(pieces[-1])
            if current != before and current != after:
                conflicts.append(f"{identity}: {key} changed in both design and instance")
            else:
                if after is None:
                    target.pop(pieces[-1], None)
                else:
                    target[pieces[-1]] = deepcopy(after)
    if conflicts:
        raise ValueError("Instance override conflict; keep original node/member keys or explicitly "
                         "set override_policy=discard: " + "; ".join(conflicts[:32]))
    return list(desired.values())


def constructor_role(kind: str, slot: int) -> str:
    if kind == "colonnade":
        return "column"
    if kind == "doorway":
        return "lintel" if slot == 2 else "jamb"
    if kind == "stairs":
        return "step"
    if kind == "fountain":
        return {0: "basin", 1: "pedestal", 2: "bowl"}.get(slot, "rim")
    return {"floor": "floor-tile", "wall": "wall-segment"}.get(kind, "unclassified")
