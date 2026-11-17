"""Install project-scoped native harness integrations without managing models or credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .client import ROOT, Client
from .settings_io import save_atomic

BEGIN = "# BEGIN LevelControl managed integration"
END = "# END LevelControl managed integration"
NAME = "levelcontrol"


def python_executable() -> str:
    bundled = Path(sys.prefix) / "python.exe"
    return str(bundled if bundled.is_file() else Path(sys.executable))


def executable(harness: str) -> str | None:
    override = os.environ.get("LC_CODEX" if harness == "codex" else "LC_CLAUDE")
    if override:
        return override if Path(override).is_file() else None
    located = shutil.which(harness)
    if located and (os.name != "nt" or Path(located).suffix.lower() == ".exe"):
        return located
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    root = local / ("OpenAI/Codex/bin" if harness == "codex" else "Claude-3p/claude-code")
    candidates = list(root.rglob(f"{harness}.exe")) if root.exists() else []
    if harness == "claude":
        candidates += list((Path.home() / ".local/bin").glob("claude.exe"))
    return str(max(candidates, key=lambda path: path.stat().st_mtime)) if candidates else None


def managed_text(original: str, content: str | None) -> str:
    if BEGIN in original or END in original:
        if original.count(BEGIN) != 1 or original.count(END) != 1:
            raise ValueError("Invalid LevelControl managed block; preserve file and inspect it")
        before, tail = original.split(BEGIN, 1)
        _, after = tail.split(END, 1)
        original = before + after.removeprefix("\n")
    if content is None:
        return original
    separator = "" if not original or original.endswith("\n") else "\n"
    return original + separator + BEGIN + "\n" + content.rstrip() + "\n" + END + "\n"


def read_text(path: Path) -> str:
    return path.read_text("utf-8") if path.exists() else ""


def server_config(workspace: Path) -> dict[str, Any]:
    state = json.loads((workspace / ".levelcontrol/installation.json").read_text("utf-8"))
    return state["server"]


def runtime_server(workspace: Path, port: int, project_id: str,
                   data_dir: Path, design_file: Path | None = None) -> dict[str, Any]:
    if isinstance(port, bool) or not 1024 <= port <= 65535 or not project_id.strip():
        raise ValueError("Provide a project ID and port 1024..65535")
    environment = {
        "PYTHONPATH": str(ROOT / "tools"), "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1",
        "LC_PORT": str(port), "LC_BIND_PORT": str(port), "LC_PROJECT_ID": project_id,
        "LC_DATA_DIR": str(data_dir.resolve()), "LC_WORKSPACE": str(workspace.resolve()),
    }
    if os.environ.get("PYTHONHOME"):
        environment["PYTHONHOME"] = os.environ["PYTHONHOME"]
    if design_file is not None:
        environment["LC_DESIGN_FILE"] = str(design_file.resolve())
    return {"command": python_executable(), "args": ["-m", "lc.mcp_server"],
            "env": environment}


def install(workspace: Path, port: int, project_id: str,
            data_dir: Path | None = None, design_file: Path | None = None) -> dict[str, Any]:
    workspace = workspace.resolve()
    data_dir = (data_dir or workspace / ".levelcontrol/data").resolve()
    if isinstance(port, bool) or not 1024 <= port <= 65535 or not project_id.strip():
        raise ValueError("Provide a project ID and port 1024..65535")
    metadata = workspace / ".levelcontrol/installation.json"
    previous = json.loads(metadata.read_text("utf-8")) if metadata.exists() else None
    if previous and previous["project_id"] != project_id:
        raise ValueError("This workspace belongs to another LevelControl project")
    server = runtime_server(workspace, port, project_id, data_dir, design_file)
    codex_path, claude_path = workspace / ".codex/config.toml", workspace / ".mcp.json"
    codex_text = read_text(codex_path)
    codex = tomllib.loads(codex_text)
    current = codex.get("mcp_servers", {}).get(NAME)
    if current and (not previous or current != previous["codex"]):
        raise ValueError("Codex levelcontrol entry was not created here or was edited; preserved")
    claude = json.loads(read_text(claude_path) or "{}")
    servers = claude.get("mcpServers", {})
    current = servers.get(NAME)
    if current and (not previous or current != previous["claude"]):
        raise ValueError("Claude Code levelcontrol entry is foreign or edited; preserved")
    codex_entry = {**server, "startup_timeout_sec": 20, "tool_timeout_sec": 60}
    claude_entry = {"type": "stdio", **server}
    block = "[mcp_servers.levelcontrol]\n" + "\n".join(
        f"{key} = {json.dumps(value, ensure_ascii=False)}"
        for key, value in codex_entry.items() if key != "env")
    block += "\n[mcp_servers.levelcontrol.env]\n" + "\n".join(
        f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in server["env"].items())
    updated_codex = managed_text(codex_text, block)
    tomllib.loads(updated_codex)
    guidance = (ROOT / "docs/lc_help/native.md").read_text("utf-8")
    guidance += "\n构建、实体命令及目录语法按需通过 lc.help 查询。不要猜测资产 ID。\n"
    instructions = {workspace / name: managed_text(read_text(workspace / name), guidance)
                    for name in ("AGENTS.md", "CLAUDE.md")}
    servers[NAME] = claude_entry
    claude["mcpServers"] = servers
    # Validate every destination before writing any integration file.
    workspace.mkdir(parents=True, exist_ok=True)
    codex_path.parent.mkdir(parents=True, exist_ok=True)
    codex_path.write_text(updated_codex, "utf-8")
    save_atomic(claude_path, claude)
    for path, content in instructions.items():
        path.write_text(content, "utf-8")
    state = {"version": 1, "project_id": project_id, "port": port, "data_dir": str(data_dir),
             "workspace": str(workspace), "server": server,
             "codex": codex_entry, "claude": claude_entry}
    save_atomic(metadata, state)
    return state


def uninstall(workspace: Path) -> list[str]:
    workspace = workspace.resolve()
    metadata = workspace / ".levelcontrol/installation.json"
    if not metadata.exists():
        return []
    state = json.loads(metadata.read_text("utf-8"))
    codex_path, claude_path = workspace / ".codex/config.toml", workspace / ".mcp.json"
    codex_text = read_text(codex_path)
    codex = tomllib.loads(codex_text)
    claude = json.loads(read_text(claude_path) or "{}")
    if codex.get("mcp_servers", {}).get(NAME) not in (None, state["codex"]):
        raise ValueError("Codex integration changed; uninstall left files untouched")
    if claude.get("mcpServers", {}).get(NAME) not in (None, state["claude"]):
        raise ValueError("Claude Code integration changed; uninstall left files untouched")
    texts = {path: managed_text(read_text(path), None) for path in (
        codex_path, workspace / "AGENTS.md", workspace / "CLAUDE.md")}
    for path, content in texts.items():
        if path.exists():
            path.write_text(content, "utf-8")
    if claude_path.exists():
        claude.get("mcpServers", {}).pop(NAME, None)
        save_atomic(claude_path, claude)
    metadata.unlink()
    return ["LevelControl integration removed; project data preserved"]


async def check_mcp(server: dict[str, Any]) -> dict[str, Any]:
    params = StdioServerParameters(command=server["command"], args=server["args"],
                                   env={**os.environ, **server["env"]})
    async with asyncio.timeout(25):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                result = await session.initialize()
                tools = await session.list_tools()
                return {"server": result.serverInfo.name,
                        "tools": [tool.name for tool in tools.tools]}


def diagnose(workspace: Path) -> dict[str, Any]:
    server = server_config(workspace)
    report: dict[str, Any] = {"workspace": str(workspace.resolve()), "harnesses": {}}
    for name in ("codex", "claude"):
        path = executable(name)
        info: dict[str, Any] = {"path": path, "available": False}
        if path:
            try:
                process = subprocess.run([path, "--version"], capture_output=True, text=True,
                                         timeout=15, creationflags=(
                                             subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
                info.update(available=process.returncode == 0, version=process.stdout.strip())
            except (OSError, subprocess.TimeoutExpired) as error:
                info["error"] = str(error)
        report["harnesses"][name] = info
    try:
        report["mcp"] = asyncio.run(check_mcp(server))
    except Exception as error:
        report["mcp_error"] = str(error)
    try:
        report["game"] = Client(int(server["env"]["LC_PORT"])).health(timeout=2)
    except (OSError, ConnectionError) as error:
        report["game_error"] = str(error)
    report["capabilities_note"] = "Search, vision and approvals require a real native Harness test"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "remove", "doctor"])
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18870)
    parser.add_argument("--project-id", default="levelcontrol")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--design-file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "setup":
            result = install(args.workspace, args.port, args.project_id, args.data_dir,
                             args.design_file)
        elif args.action == "remove":
            result = uninstall(args.workspace)
        else:
            result = diagnose(args.workspace)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.action == "doctor" and ("mcp_error" in result or "game_error" in result):
            return 1
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
