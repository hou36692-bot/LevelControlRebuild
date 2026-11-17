"""The single parser for the agent-facing command language."""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
SELECTOR = re.compile(r"(?:(?:tag|group):)?[a-z0-9*-]+(?:,(?:(?:tag|group):)?[a-z0-9*-]+)*\Z")
LENGTH = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(cm|m)\Z")
FIELDS = {"id", "template", "transform", "bounds", "tags", "group", "version", "source",
          "surface", "collision", "lod", "semantic"}


class Parser(argparse.ArgumentParser):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(add_help=False, **kwargs)

    def error(self, message: str) -> None:
        raise ValueError(message)

    def _parse_optional(self, arg_string: str):
        # argparse mistakes negative vectors and unit-bearing distances for option names.
        if re.fullmatch(r"-[0-9.][0-9eE+.,cm-]*", arg_string):
            return None
        return super()._parse_optional(arg_string)


@dataclass
class Command:
    path: str
    data: dict[str, Any] = field(default_factory=dict)
    front_cm: float | None = None
    file: str | None = None


def length(value: str) -> float:
    if value == "0":
        return 0.0
    match = LENGTH.fullmatch(value)
    if not match:
        raise ValueError(f"Use an explicit m or cm unit: {value}")
    result = float(match[1]) * (100 if match[2] == "m" else 1)
    if not math.isfinite(result) or abs(result) > 1e8:
        raise ValueError("Distance must be finite and within 100000000 cm")
    return result


def vector(value: str, distances: bool = True) -> list[float]:
    values = value.split(",")
    if len(values) != 3:
        raise ValueError("Expected three comma-separated values")
    result = [length(item) if distances else float(item) for item in values]
    if not all(math.isfinite(item) for item in result):
        raise ValueError("Vector values must be finite")
    return result


def selector(value: str) -> str:
    if len(value) > 4096 or not SELECTOR.fullmatch(value):
        raise ValueError("Use lowercase IDs, '*' and comma-separated selectors")
    return value


def parse(tool: str, command: str) -> Command:
    if "\n" in command or "\r" in command:
        raise ValueError("Commands must be a single line")
    words = shlex.split(command)
    parser = Parser(prog=f"lc.{tool}")
    if tool.startswith("lgg_"):
        return parse_lgg(tool, words)
    if tool == "connect":
        parser.add_argument("--port", type=int, default=18844)
        args = parser.parse_args(words)
        if not 1024 <= args.port <= 65535:
            raise ValueError("Port must be 1024..65535")
        return Command("/health", {"port": args.port})
    if tool == "help":
        topic = words[0].removeprefix("lc_").removeprefix("lc.") if words else "index"
        if len(words) > 1 or topic not in {
            "index",
            "connect",
            "read",
            "edit",
            "capture",
            "lgg",
            "build",
            "growth",
            "checks",
            "actions",
            "environment",
            "surfaces",
            "placement",
            "review",
            "native",
            "semantic",
        }:
            raise ValueError("Help topics: read, edit, capture, lgg, build, growth, surfaces, "
                             "environment, placement, checks, actions, review, native")
        return Command(topic + ".md")
    if tool == "capture":
        if words and words[0] == "compare":
            parser.add_argument("--a", required=True)
            parser.add_argument("--b", required=True)
            parser.add_argument("--name", required=True)
            values = vars(parser.parse_args(words[1:]))
            return Command("/v1/review", {"action": "compare", **values})
        parser.add_argument("--center", type=vector, dest="center_cm")
        parser.add_argument("--radius", type=length, default=1800, dest="radius_cm")
        parser.add_argument("--height", type=length, default=800, dest="height_cm")
        parser.add_argument("path")
        args = parser.parse_args(words)
        if not re.fullmatch(r"[\w.-]{1,124}\.png", args.path, flags=re.IGNORECASE):
            raise ValueError("Use a PNG filename without a directory")
        data = {"path": args.path, "camera": "player"}
        if args.center_cm is not None:
            data.update(center_cm=args.center_cm, radius_cm=args.radius_cm,
                        height_cm=args.height_cm)
        return Command("/v1/capture", data)
    if not words:
        raise ValueError(f"Empty lc.{tool} command; read lc.help")
    verb, *rest = words
    if tool == "read":
        if verb == "catalog":
            parser.add_argument("--category", default="")
            parser.add_argument("--tag", default="")
            parser.add_argument("--search", default="")
            return Command("catalog", vars(parser.parse_args(rest)))
        if verb in {"environment", "environment-presets"}:
            parser.parse_args(rest)
            return Command("/v1/environment" + ("/presets" if verb.endswith("-presets") else ""))
        if verb in {"presentation", "checkpoints", "clock", "surfaces"}:
            parser.parse_args(rest)
            return Command(f"/v1/{verb}")
        if verb == "jump-check":
            parser.add_argument("--from", type=vector, dest="start_cm", required=True)
            parser.add_argument("--heading", type=float, default=0, dest="heading_deg")
            parser.add_argument("--speed", type=length, dest="speed_cm_s")
            parser.add_argument("--max-time", type=float, default=3, dest="max_time_s")
            return Command(
                "/v1/checks",
                {
                    "kind": "jump",
                    **{k: v for k, v in vars(parser.parse_args(rest)).items() if v is not None},
                },
            )
        if verb in {"clearance", "sightline"}:
            parser.add_argument("--from", type=vector, dest="from_cm", required=True)
            parser.add_argument("--to", type=vector, dest="to_cm", required=True)
            parser.add_argument("--radius", type=length, dest="radius_cm")
            parser.add_argument("--half-height", type=length, dest="half_height_cm")
            return Command(
                "/v1/checks",
                {
                    "kind": verb,
                    **{k: v for k, v in vars(parser.parse_args(rest)).items() if v is not None},
                },
            )
        if verb == "walk-graph":
            parser.add_argument("selector", type=selector)
            parser.add_argument("--from-id")
            parser.add_argument("--to-id")
            return Command("walk-graph", vars(parser.parse_args(rest)))
        if verb in {"events", "frame-history"}:
            parser.add_argument("--since", type=int, default=0)
            args = parser.parse_args(rest)
            if args.since < 0:
                raise ValueError("since must be nonnegative")
            return Command(f"/v1/{verb}", {"since": args.since})
        if verb == "objects":
            parser.add_argument("selector", nargs="?", default="*", type=selector)
            parser.add_argument("fields", nargs="?")
            parser.add_argument("--fields", dest="named_fields")
            parser.add_argument("--nearest", action="store_true")
            parser.add_argument("--count", action="store_true")
            parser.add_argument("--limit", type=int, default=256)
            args = parser.parse_args(rest)
            if args.fields and args.named_fields:
                raise ValueError("Specify fields once")
            fields = args.named_fields or args.fields or ""
            if fields and not set(fields.split(",")) <= FIELDS:
                raise ValueError("Unknown entity fields")
            if not 1 <= args.limit <= 16384:
                raise ValueError("Limit must be 1..16384")
            data = dict(
                selector=args.selector,
                nearest=str(args.nearest).lower(),
                count=str(args.count).lower(),
                limit=args.limit,
            )
            if fields:
                data["fields"] = fields
            return Command("/v1/entities", data)
        if verb == "templates":
            parser.add_argument("--kind", choices=["blockout"], default="blockout")
            parser.parse_args(rest)
            return Command("/v1/templates")
        if verb == "player-status":
            parser.add_argument("--fields", choices=["transform", "facing"])
            parser.parse_args(rest)
            return Command("/v1/player")
        if verb == "consistency":
            parser.parse_args(rest)
            return Command("/v1/consistency")
        if verb == "receipt":
            parser.add_argument("req_id")
            return Command("/v1/receipt", {"req_id": parser.parse_args(rest).req_id})
        raise ValueError(f"Unsupported read command: {verb}")
    if tool != "edit":
        raise ValueError(f"Unknown tool: {tool}")
    if verb == "review":
        parser.add_argument("action", choices=["performance", "cancel"])
        parser.add_argument("--frames", type=int, default=120)
        data = vars(parser.parse_args(rest))
        if not 30 <= data["frames"] <= 3600:
            raise ValueError("Frames must be 30..3600")
        return Command("/v1/review", data)
    if verb == "prop":
        parser.add_argument("action", choices=["place", "preview"])
        parser.add_argument("asset")
        parser.add_argument("--id", required=True)
        parser.add_argument("--at", type=vector, required=True)
        parser.add_argument("--count", type=int, default=1)
        parser.add_argument("--columns", type=int, default=0)
        parser.add_argument("--step", type=vector, default=[0, 0, 0])
        parser.add_argument("--yaw", type=float, default=0)
        parser.add_argument("--random-yaw", type=float, default=0)
        parser.add_argument("--scale-min", type=float, default=1)
        parser.add_argument("--scale-max", type=float, default=1)
        parser.add_argument("--grid", type=length, default=0)
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument("--snap", action="store_true")
        parser.add_argument("--group", default="")
        parser.add_argument("--collision", choices=["on", "off"], default="on")
        parser.add_argument("--lod", type=int, choices=range(4), default=0)
        data = vars(parser.parse_args(rest))
        if not ID.fullmatch(data["id"]) or len(data["id"]) > 110:
            raise ValueError("Placement needs a kebab-case ID of at most 110 characters")
        if not 1 <= data["count"] <= 16384 or not 0 <= data["columns"] <= 16384:
            raise ValueError("Count must be 1..16384")
        if (not all(math.isfinite(data[k]) for k in ["yaw", "random_yaw", "scale_min",
                                                     "scale_max", "grid"])
                or not 0 <= data["random_yaw"] <= 180 or abs(data["yaw"]) > 3600
                or not 0.01 <= data["scale_min"] <= data["scale_max"] <= 100
                or data["grid"] < 0):
            raise ValueError("Invalid placement rotation, scale range or grid")
        return Command("place", data)
    if verb == "placement":
        parser.add_argument("action", choices=["align", "array"])
        parser.add_argument("selector", type=selector)
        parser.add_argument("--axis", choices=["x", "y", "z"])
        parser.add_argument("--mode", choices=["min", "center", "max"], default="center")
        parser.add_argument("--prefix")
        parser.add_argument("--count", type=int, default=1)
        parser.add_argument("--step", type=vector, default=[0, 0, 0])
        data = vars(parser.parse_args(rest))
        if data["action"] == "align" and not data["axis"]:
            raise ValueError("Align requires --axis")
        if data["action"] == "array" and (
                not data["prefix"] or not ID.fullmatch(data["prefix"])
                or len(data["prefix"]) > 110 or not 1 <= data["count"] <= 16384):
            raise ValueError("Array requires a valid --prefix and count 1..16384")
        return Command("layout", data)
    if verb == "surface":
        parser.add_argument("action", choices=["apply", "preview"])
        parser.add_argument("selector", type=selector)
        parser.add_argument("--preset")
        parser.add_argument("--tint", type=lambda value: vector(value, False))
        for name in ["roughness", "metallic", "emissive", "normal-strength", "wetness"]:
            parser.add_argument("--" + name, type=float)
        data = {k: v for k, v in vars(parser.parse_args(rest)).items() if v is not None}
        action = data.pop("action")
        operation = {"op": "surface", "selector": data.pop("selector")}
        if "preset" in data:
            operation["preset"] = data.pop("preset")
        if data:
            operation["values"] = data
        return Command("/v1/edits" if action == "apply" else "/v1/design",
                       {"operations": [operation], **({"action": "preview"}
                                                     if action == "preview" else {})})
    if verb == "design":
        parser.add_argument("action", choices=["cancel", "commit"])
        parser.add_argument("--expected-version", type=int)
        return Command("/v1/design", {k: v for k, v in vars(parser.parse_args(rest)).items()
                                      if v is not None})
    if verb == "environment":
        parser.add_argument("action", choices=["apply", "preview", "commit", "cancel", "save"])
        parser.add_argument("--preset")
        parser.add_argument("--name")
        parser.add_argument("--expected-version", type=int)
        parser.add_argument("--rate", type=float, dest="rate_h_per_s")
        names = {
            "time": "time_h", "sun-azimuth": "sun_azimuth_deg",
            "sun-elevation": "sun_elevation_deg", "sun-intensity": "sun_intensity",
            "sun-temperature": "sun_temperature_k", "sky-intensity": "sky_intensity",
            "atmosphere-density": "atmosphere_density", "fog-density": "fog_density",
            "fog-falloff": "fog_falloff", "cloud-coverage": "cloud_coverage",
            "exposure": "exposure_ev", "contrast": "contrast", "saturation": "saturation",
            "temperature": "temperature_k",
        }
        for flag, key in names.items():
            parser.add_argument("--" + flag, type=float, dest=key)
        parser.add_argument("--fog-height", type=length, dest="fog_height_cm")
        parser.add_argument("--fog-color", type=lambda value: vector(value, False))
        data = {k: v for k, v in vars(parser.parse_args(rest)).items() if v is not None}
        values = {key: data.pop(key) for key in [*names.values(), "fog_height_cm", "fog_color"]
                  if key in data}
        if values:
            if any(not math.isfinite(v) for x in values.values()
                   for v in (x if isinstance(x, list) else [x])):
                raise ValueError("Environment values must be finite")
            data["values"] = values
        return Command("/v1/environment", data)
    if verb == "player":
        parser.add_argument("action", choices=["teleport", "configure"])
        parser.add_argument("--feet", type=vector, dest="feet_cm")
        parser.add_argument("--speed", type=length, dest="max_walk_speed_cm_s")
        parser.add_argument("--jump-velocity", type=length, dest="jump_velocity_cm_s")
        parser.add_argument("--gravity-scale", type=float, dest="gravity_scale")
        return Command(
            "/v1/player/control",
            {k: v for k, v in vars(parser.parse_args(rest)).items() if v is not None},
        )
    if verb == "action":
        parser.add_argument("action", choices=["pause", "resume", "stop"])
        return Command("/v1/actions/control", vars(parser.parse_args(rest)))
    if verb == "checkpoint":
        parser.add_argument("action", choices=["save"])
        parser.add_argument("name")
        args = parser.parse_args(rest)
        return Command("/v1/checkpoints", vars(args))
    if verb == "restore":
        parser.add_argument("--checkpoint", required=True)
        return Command(
            "/v1/checkpoints", {"action": "restore", "name": parser.parse_args(rest).checkpoint}
        )
    if verb == "presentation":
        parser.add_argument("action", choices=["skip", "cancel", "camera-player", "hold", "resume"])
        parser.add_argument("--lease-id")
        parser.add_argument("--revision", type=int)
        return Command(
            "/v1/presentation",
            {k: v for k, v in vars(parser.parse_args(rest)).items() if v is not None},
        )
    if verb == "undo":
        options = parser.add_mutually_exclusive_group(required=True)
        options.add_argument("--last", action="store_true")
        options.add_argument("--tx-id")
        args = parser.parse_args(rest)
        return Command("/v1/undo", {"last": True} if args.last else {"tx_id": args.tx_id})
    if verb == "select":
        parser.add_argument("selector", type=selector)
        parser.add_argument("--nearest", action="store_true")
        args = parser.parse_args(rest)
        return Command("/v1/select", vars(args))
    if verb == "act":
        if len(rest) >= 2 and rest[1] != "raise":
            parser.add_argument("selector", type=selector)
            parser.add_argument("kind", choices=["spin", "move", "scale", "oscillate"])
            parser.add_argument("--duration", type=float, default=2, dest="duration_s")
            parser.add_argument("--degrees", type=float, default=180, dest="angle_deg")
            parser.add_argument("--delta", type=vector, default=[0, 0, 0], dest="delta_cm")
            parser.add_argument("--factor", type=float, default=1.2)
            parser.add_argument("--amplitude", type=vector, default=[0, 0, 20], dest="amplitude_cm")
            parser.add_argument("--frequency", type=float, default=1, dest="frequency_hz")
            return Command("/v1/actions", vars(parser.parse_args(rest)))
        parser.add_argument("selector", type=selector)
        parser.add_argument("action", choices=["raise"])
        parser.add_argument("distance", type=length)
        args = parser.parse_args(rest)
        return Command(
            "/v1/edits",
            {
                "operations": [
                    {"op": "move", "selector": args.selector, "delta_cm": [0, 0, args.distance]}
                ]
            },
        )
    if verb != "apply" or not rest:
        raise ValueError("Supported edits: apply, select, undo, act <selector> raise <distance>")
    if rest[0] == "--path":
        parser.add_argument("--path", required=True)
        return Command("/v1/edits", file=parser.parse_args(rest).path)
    operation, *rest = rest
    if operation not in {"create", "move", "set", "delete"}:
        raise ValueError(f"Unsupported apply operation: {operation}")
    parser.add_argument("selector", type=selector)
    if operation == "create":
        parser.add_argument("--template", default="blockout/box")
        parser.add_argument("--size", type=vector)
        location = parser.add_mutually_exclusive_group()
        location.add_argument("--at", type=vector)
        location.add_argument("--front", type=length)
        parser.add_argument(
            "--rotation", type=lambda value: vector(value, False), default=[0, 0, 0]
        )
        parser.add_argument("--tags", default="")
        parser.add_argument("--group", default="")
        args = parser.parse_args(rest)
        if len(args.selector) > 128 or not ID.fullmatch(args.selector):
            raise ValueError("Create requires a lowercase kebab-case ID, without wildcards")
        if args.size is not None and min(args.size) <= 0:
            raise ValueError("Size must be positive")
        entity = {
            "id": args.selector,
            "template": args.template,
            "transform": {
                "position_cm": args.at,
                "size_cm": args.size,
                "rotation_deg": args.rotation,
            },
            "tags": args.tags.split(",") if args.tags else [],
            "group": args.group,
        }
        return Command(
            "/v1/edits",
            {"operations": [{"op": "create", "entity": entity}]},
            front_cm=(args.front if args.front is not None else 400) if args.at is None else None,
        )
    if operation == "move":
        parser.add_argument("--delta", type=vector, required=True)
        args = parser.parse_args(rest)
        return Command(
            "/v1/edits",
            {"operations": [{"op": "move", "selector": args.selector, "delta_cm": args.delta}]},
        )
    if operation == "set":
        parser.add_argument("--template")
        parser.add_argument("--collision", choices=["on", "off"])
        parser.add_argument("--lod", type=int, choices=range(4))
        parser.add_argument("--at", type=vector)
        parser.add_argument("--size", type=vector)
        parser.add_argument("--rotation", type=lambda value: vector(value, False))
        args = parser.parse_args(rest)
        transform = {
            key: value
            for key, value in [
                ("position_cm", args.at),
                ("size_cm", args.size),
                ("rotation_deg", args.rotation),
            ]
            if value is not None
        }
        if ((not transform and args.template is None and args.collision is None
             and args.lod is None) or (args.size is not None and min(args.size) <= 0)):
            raise ValueError("Set needs a nonempty transform patch and positive sizes")
        return Command(
            "/v1/edits",
            {"operations": [{"op": "set", "selector": args.selector, "transform": transform,
                             **({"template": args.template} if args.template else {}),
                             **({"collision": args.collision == "on"}
                                if args.collision is not None else {}),
                             **({"lod": args.lod} if args.lod is not None else {})}]},
        )
    args = parser.parse_args(rest)
    return Command("/v1/edits", {"operations": [{"op": "delete", "selector": args.selector}]})


def parse_lgg(tool: str, words: list[str]) -> Command:
    parser = Parser(prog=tool)
    if tool == "lgg_card":
        parser.add_argument("action", choices=["init", "status"])
        parser.add_argument("--scene", default="courtyard")
    elif tool == "lgg_draft":
        parser.add_argument("sequence")
        parser.add_argument("stage", choices=["site", "coarse", "module"])
        parser.add_argument("--json", type=json.loads, dest="payload")
        parser.add_argument("--center")
        parser.add_argument("--size")
        parser.add_argument("--module", nargs=5, action="append", dest="modules")
        parser.add_argument("--id", type=int)
        parser.add_argument("--complete", action="store_true")
    elif tool == "lgg_run":
        parser.add_argument("action", choices=["play", "status", "cancel"])
        parser.add_argument("--duration", type=float, default=8)
        parser.add_argument("--music", choices=["none", "architect-pulse"], default="none")
    else:
        raise ValueError(f"Unknown tool: {tool}")
    data = vars(parser.parse_args(words))
    if tool == "lgg_draft":
        if not re.fullmatch(r"#[1-9][0-9]*", data["sequence"]):
            raise ValueError("Draft sequence must be #N")
        data["sequence"] = int(data["sequence"][1:])
        center, size, modules = (data.pop(key) for key in ("center", "size", "modules"))
        if data["payload"] is not None and any(v is not None for v in (center, size, modules)):
            raise ValueError("Use JSON or compact flags, not both")
        if data["payload"] is None:
            if data["stage"] == "site" and center and size and not modules:
                data["payload"] = {"center_m": vector(center, False), "size_m": vector(size, False)}
            elif data["stage"] == "coarse" and modules and not center and not size:
                data["payload"] = [
                    {
                        "id": int(i),
                        "name": name,
                        "kind": kind,
                        "center_m": vector(at, False),
                        "size_m": vector(dim, False),
                    }
                    for i, name, kind, at, dim in modules
                ]
            elif data["stage"] == "module" and not modules:
                data["payload"] = {}
                if center:
                    data["payload"]["center_m"] = vector(center, False)
                if size:
                    data["payload"]["size_m"] = vector(size, False)
            else:
                raise ValueError("Provide --json or the stage's compact flags")
    if tool == "lgg_card" and (not ID.fullmatch(data["scene"]) or len(data["scene"]) > 80):
        raise ValueError("scene must be a kebab-case name of at most 80 characters")
    if tool == "lgg_run" and (
        not math.isfinite(data["duration"]) or not 1 <= data["duration"] <= 110
    ):
        raise ValueError("duration must be 1..110 seconds")
    return Command(tool, data)
