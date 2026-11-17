"""MCP owns orchestration only; entity authority stays in the game process."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from typing import Any

from lgg.build import BuildService
from lgg.build_types import BuildPlan
from lgg.service import DesignService
from lgg.stream import StreamService
from lgg.stream_types import CoarsePlan, ModuleDelivery
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from .client import Client
from .command import parse
from .native import RuntimeSession, tool_result

server = FastMCP("LevelControl", log_level="ERROR", instructions=(
    "LevelControl edits a running game. Use lc.context for project, request_id, player, "
    "world_version and saved scenes. Read lc.help native for recovery. "
    "Use your native search, vision, files and skills; modify the world only through LC tools. "
    "For buildings, one designer calls lgg_plan then lgg_module. Do not spawn subagents. "
    "Each accepted module grows upward while you design the next; final module auto-commits. "
    "Only construction_status=applied confirms a staged build; preview applied is not a commit. "
    "Only applied receipts confirm changes. scheduled means pending; query lc.receipt. "
    "Canceling agent output does not cancel the game; use presentation cancel or undo. "
    "Use lc.query for identity, hierarchy and spatial selection before screenshots. "
    "Use lgg_knowledge for relevant rules/examples; cases pending review are not validated. "
    "Retain node key and repeat.members when editing a saved design."))
client = Client()
runtime = RuntimeSession(client)


def invoke(name: str, command: str, action: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    tool_name = name if name.startswith("lgg_") else f"lc.{name}"
    print(f"{tool_name} {command}", file=sys.stderr, flush=True)
    try:
        if getattr(client, "data_dir", None) and name not in {
            "help", "context", "connect", "receipt", "changes"
        }:
            runtime.check()
        return action()
    finally:
        print(f"{tool_name} | {time.perf_counter() - started:.3f} s", file=sys.stderr, flush=True)


@server.tool(name="lc.connect", description=(
    "Connect to this game session; omit command to use its configured port."))
def connect(command: str = "") -> dict[str, Any]:
    command = command or f"--port {client.port}"
    bound = os.environ.get("LC_BIND_PORT")
    if bound and parse("connect", command).data["port"] != int(bound):
        raise ValueError(f"This agent session is bound to port {os.environ['LC_BIND_PORT']}")
    return invoke("connect", command, lambda: client.connect(command))


@server.tool(name="lc.help", description="Read local tool docs: lc_read, lc_edit, lc_capture")
def help_tool(command: str = "") -> str:
    return invoke("help", command, lambda: client.help(command))


@server.tool(
    name="lc.read",
    description=("Query objects, player-status, templates, environment, "
                 "environment-presets or consistency.")
)
def read(command: str) -> dict[str, Any]:
    return invoke("read", command, lambda: client.read(command))


@server.tool(
    name="lc.edit",
    description="Edit entities, environment or undo. Read lc.help environment or lc_edit."
)
def edit(command: str, request_id: str | None = None, expected_version: int | None = None,
         selection_id: str | None = None) -> dict[str, Any]:
    return invoke("edit", command, lambda: client.edit(
        command, request_id, expected_version, selection_id))


@server.tool(name="lc.query", description=(
    "Query semantic identities, hierarchy, spatial relations and runtime state. "
    "Read lc.help semantic. Defaults limit=32. view_left freezes the player camera; "
    "ambiguous candidates cannot be edited. Prefer this over screenshots for object selection."))
def query(query: dict[str, Any]) -> dict[str, Any]:
    return invoke("query", "", lambda: client.query(query))


@server.tool(name="lc.changes", description=(
    "Read durable edits and live-state deltas; retain both cursors. "
    "Resync after restart."))
def changes(cursor: dict[str, Any] | None = None) -> dict[str, Any]:
    return invoke("changes", "", lambda: client.changes(cursor))


@server.tool(name="lc.annotate", description=(
    "Transactionally annotate identities and aliases. "
    "Does not grant geometry edit capability."))
def annotate(ids: list[str], semantic: dict[str, Any], request_id: str,
             expected_version: int) -> dict[str, Any]:
    return invoke("annotate", request_id, lambda: client.annotate(
        ids, semantic, request_id, expected_version))


@server.tool(name="lgg_knowledge", description=(
    "Retrieve sourced design rules and paired LGG/whitebox cases. "
    "Unreviewed cases are not validated designs."))
def knowledge(query: str = "", case_id: str | None = None) -> dict[str, Any]:
    from lgg.knowledge import retrieve
    return invoke("lgg_knowledge", query, lambda: retrieve(query, case_id))


@server.tool(
    name="lc.capture", description="Capture player view to a PNG filename; returns saved path."
)
def capture(command: str = "photo.png", request_id: str | None = None) -> CallToolResult:
    return tool_result(invoke("capture", command, lambda: client.capture(command, request_id)))


@server.tool(name="lc.context", description=(
    "Read project, game session, world_version, player, new request_id and saved scene plans. "
    "Use before lgg_build; reuse request_id and world_version. Also recovers pending builds."))
def context() -> dict[str, Any]:
    return invoke("context", "", runtime.context)


@server.tool(name="lc.receipt", description=(
    "Read the original request after timeout/reconnect; finalize saved build plans. "
    "Never retry a write with a new ID when its result is unknown."))
def receipt(request_id: str) -> CallToolResult:
    return tool_result(invoke("receipt", request_id, lambda: runtime.receipt(request_id)))


@server.tool(
    name="lgg_card",
    description="Design card: init --scene courtyard; status. Returns constitution and syntax.",
)
def lgg_card(command: str) -> dict[str, Any]:
    return invoke("lgg_card", command, lambda: DesignService(client).invoke("lgg_card", command))


@server.tool(
    name="lgg_draft",
    description=(
        "Publish #N site/coarse/module --json 'JSON' [--id N] [--complete]. "
        "Meters, Z-up. Read lgg_card init first."
    ),
)
def lgg_draft(command: str) -> dict[str, Any]:
    return invoke("lgg_draft", command, lambda: DesignService(client).invoke("lgg_draft", command))


@server.tool(
    name="lgg_run",
    description=(
        "play [--duration 8] queues one commit after flight; returns immediately. "
        "status; cancel. New module draft replaces pending preview."
    ),
)
def lgg_run(command: str) -> dict[str, Any]:
    return invoke("lgg_run", command, lambda: DesignService(client).invoke("lgg_run", command))


@server.tool(name="lgg_build", description=(
    "Build a complete editable scene in one call. plan: name, nodes, definitions?, "
    "anchor?, environment?, scene? (update only), expected_version?. "
    "Nodes: id; exactly one of asset/kind/parts/use; at=[x,y,z] meters, size=[x,y,z], "
    "yaw degrees, surface?, collision?, repeat={count:N or [nx,ny,nz],step:[x,y,z]}. "
    "parts are local child nodes; use references definitions; kind uses existing LGG "
    "constructors and params. Local +Y faces forward, +X right, Z up. "
    "Default anchor is automatically in front of player. Reuse host request_id. "
    "Set intent: new_structure for a whole new building/area, extension for an architectural "
    "addition (tower/wing/corridor), edit for adjustments, placement for props. "
    "Auto reveal is 4s/3s "
    "only for new structures/extensions. presentation={mode:auto|off|on,camera:player|overview,"
    "music:none|architect-pulse}; defaults auto/player/none. Explicit show uses on (5s)."))
def lgg_build(plan: BuildPlan, request_id: str) -> dict[str, Any]:
    document = plan.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    return invoke("lgg_build", request_id, lambda: BuildService(client).build(document, request_id))


def main() -> None:
    server.run(transport="stdio")


@server.tool(name="lgg_plan", description=(
    "Publish coarse building layout before detailing: name, expected_version, modules, anchor?, "
    "environment?. Modules: id, label, size, at (local meters), yaw?, brief?, depends_on?. "
    "Or relative_to+side(left/right/front/back/above/below)+gap instead of at. "
    "One designer only. Returns build_id and resolved layout. No detailed geometry yet."))
def lgg_plan(plan: CoarsePlan, request_id: str) -> dict[str, Any]:
    document = plan.model_dump(mode="json", exclude_none=True)
    return invoke("lgg_plan", request_id, lambda: StreamService(client).plan(document, request_id))


@server.tool(name="lgg_module", description=(
    "Deliver one module immediately after designing it, then design the next without waiting. "
    "module={id,revision:1,nodes,definitions?}; node format matches lgg_build. "
    "Use a unique request_id per module. Rejected delivery does not advance revision. "
    "Names/IDs are English kebab-case; labels may be Chinese. Limestone preset=stone-limestone. "
    "Coordinates relative to the module envelope CENTER, meters/Z-up; keep all geometry inside. "
    "repeat supports count+span or count+step. Preserve IDs; increment revision for edits. "
    "Reediting a committed build also requires expected_version from lc.context. "
    "Deliver dependencies first. Last valid module automatically submits one transaction. "
    "Preview applied is not completion; read construction_status and real receipt."))
def lgg_module(build_id: str, module: ModuleDelivery, request_id: str) -> dict[str, Any]:
    document = module.model_dump(mode="json", exclude_none=True)
    return invoke("lgg_module", request_id,
                  lambda: StreamService(client).module(build_id, document, request_id))


if __name__ == "__main__":
    main()
