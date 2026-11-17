"""Select construction reveals from intent and real geometry changes, not object counts."""

from typing import Any

from .camera_rules import shot
from .timeline import timeline


def geometry(entity: dict[str, Any]) -> dict[str, Any]:
    return {"template": entity["template"], "transform": entity["transform"]}


def select(plan: dict[str, Any], before: list[dict[str, Any]],
           desired: list[dict[str, Any]], held: bool = False) -> dict[str, Any]:
    intent = plan.get("intent", "edit")
    options = plan.get("presentation") or {}
    if intent not in {"new_structure", "extension", "edit", "placement"}:
        raise ValueError("Unknown build intent")
    if not isinstance(options, dict) or options.keys() - {"mode", "camera", "music"}:
        raise ValueError("Invalid presentation options")
    mode = options.get("mode", "auto")
    camera, music = options.get("camera", "player"), options.get("music", "none")
    if mode not in {"auto", "off", "on"} or camera not in {"player", "overview"} \
            or music not in {"none", "architect-pulse"}:
        raise ValueError("Invalid presentation mode, camera or music")
    old, new = ({e["id"]: e for e in collection} for collection in (before, desired))
    added = new.keys() - old.keys()
    removed = old.keys() - new.keys()
    changed = {key for key in old.keys() & new.keys() if geometry(old[key]) != geometry(new[key])}
    duration, reason = 0, "direct_edit"
    animate = set()
    if mode == "off":
        reason = "disabled"
    elif mode == "on":
        duration, reason = 5, "explicit_show"
        animate = added | changed
        if not animate:
            animate = set(new)
    elif held:
        reason = "input_held"
    elif intent == "new_structure" and not plan.get("scene") and not before and added:
        duration, reason, animate = 4, "new_structure", set(added)
    elif intent == "extension" and not plan.get("scene") and not before and added:
        duration, reason, animate = 3, "independent_extension", set(added)
    elif intent == "extension" and plan.get("scene") and before and added \
            and not changed and not removed:
        old_groups = {e.get("group") for e in before}
        if all(new[key].get("group") not in old_groups for key in added):
            duration, reason, animate = 3, "independent_extension", set(added)
    if not duration:
        camera, music = "player", "none"
        animate = added | changed
    elif mode != "on":
        camera, music = "player", "none"
    return {"mode": mode, "reason": reason, "duration_s": duration,
            "animate_ids": sorted(animate), "auto_skip": mode == "auto",
            "camera": camera, "music": music,
            "changes": {"create": len(added), "geometry_update": len(changed),
                        "delete": len(removed)}}


def preview(policy: dict[str, Any], entities: list[dict[str, Any]],
            region: dict[str, Any]) -> dict[str, Any]:
    ids = set(policy["animate_ids"])
    moving = [e for e in entities if e["id"] in ids]
    duration = policy["duration_s"]
    schedule = {item["id"]: item for item in timeline(moving, duration)} if duration else {}
    if duration and len(moving) == 1:
        schedule[moving[0]["id"]]["duration_s"] = duration
    result = {
        "instant": not bool(duration), "auto_skip": policy["auto_skip"],
        "finish_delay_s": 0, "animate_ids": policy["animate_ids"],
        "timeline": [schedule.get(e["id"], {"id": e["id"], "start_s": 0, "duration_s": 0})
                     for e in entities], "music": policy["music"],
    }
    if duration and policy["camera"] == "overview":
        view = shot(region, 0.7, 0.9)
        result["cameras"] = {"overview": view, "hero": view, "groups": {}}
    return result
