"""Run the existing Codex CLI with only the runtime MCP integration enabled."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any

from .agent_capabilities import AgentCapabilities
from .client import ROOT


def child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # A spawned runtime agent must not inherit this development chat's tools or identity.
    for name in list(environment):
        if name != "CODEX_HOME" and name.startswith(("CODEX_", "TCODEX_")):
            environment.pop(name)
    environment.pop("LC_MODEL_API_KEY", None)
    return environment


def executable() -> str:
    if override := os.environ.get("LC_CODEX"):
        return override
    located = shutil.which("codex")
    if located and Path(located).suffix.lower() == ".exe":
        return located
    # The installed desktop wrapper resolves its executable in this directory.
    directory = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin"
    candidates = list(directory.rglob("codex.exe")) if directory.exists() else []
    if candidates:
        return str(max(candidates, key=lambda path: path.stat().st_mtime))
    if located and os.name != "nt":
        return located
    raise FileNotFoundError("Set LC_CODEX to the installed codex.exe path")


def python_executable() -> str:
    if os.name == "nt":
        bundled = Path(sys.prefix) / "python.exe"
        if bundled.exists():
            return str(bundled)
        return str(Path(sys.executable).with_name("python.exe"))
    return sys.executable


def arguments(port: int, noninteractive: bool, model: str | None = None,
              effort: str | None = None, design_file: Path | None = None,
              *, workspace: Path | None = None, data_dir: Path | None = None,
              project_id: str | None = None, native: bool = False,
              capabilities: AgentCapabilities | None = None) -> list[str]:
    if not 1024 <= port <= 65535:
        raise ValueError("Port must be 1024..65535")
    cwd = (workspace or ROOT / "project").resolve()
    if native:
        from .integration import NAME, runtime_server

        server = runtime_server(cwd, port, project_id or "levelcontrol",
                                data_dir or cwd / ".levelcontrol/data", design_file)
        result = [executable()]
        for key, value in (capabilities or AgentCapabilities()).codex_config().items():
            result += ["-c", f"{key}=" + json.dumps(value)]
        for key, value in server.items():
            values = value.items() if key == "env" else [(None, value)]
            for name, setting in values:
                suffix = f".{name}" if name else ""
                result += ["-c", f"mcp_servers.{NAME}.{key}{suffix}=" + json.dumps(setting)]
        if model:
            result += ["-c", "model=" + json.dumps(model)]
        if effort:
            result += ["-c", "model_reasoning_effort=" + json.dumps(effort)]
        return result
    result = [executable()]
    if noninteractive:
        result += ["exec", "--json", "--ephemeral", "--color", "never"]
    result += ["-C", str(cwd), "--sandbox", "read-only"]
    config_path = ROOT / "project/.codex/config.toml"
    project_settings = (tomllib.loads(config_path.read_text(encoding="utf-8"))
                        if config_path.exists() else {})
    settings: dict[str, Any] = {
        "approval_policy": "never",
        "model_reasoning_effort": effort or project_settings.get("model_reasoning_effort", "low"),
        "model": model or project_settings.get("model", "gpt-6-luna"),
        "developer_instructions": project_settings.get(
            "developer_instructions", "You control ProjectLC through LC tools."),
        "mcp_servers.node_repl.enabled": False,
        "mcp_servers.lc.command": python_executable(),
        "mcp_servers.lc.args": ["-m", "lc.mcp_server"],
        "mcp_servers.lc.cwd": str(ROOT),
        "mcp_servers.lc.env.PYTHONUTF8": "1",
        "mcp_servers.lc.env.PYTHONPATH": str(ROOT / "tools"),
        "mcp_servers.lc.env.LC_PORT": str(port),
        "mcp_servers.lc.env.LC_BIND_PORT": str(port),
        "mcp_servers.lc.required": True,
        "mcp_servers.lc.startup_timeout_sec": 15,
        "mcp_servers.lc.tool_timeout_sec": 40,
        "mcp_servers.lc.default_tools_approval_mode": "approve",
    }
    if os.environ.get("PYTHONHOME"):
        settings["mcp_servers.lc.env.PYTHONHOME"] = os.environ["PYTHONHOME"]
    if design_file is not None:
        settings["mcp_servers.lc.env.LC_DESIGN_FILE"] = str(design_file)
    if data_dir is not None:
        settings["mcp_servers.lc.env.LC_DATA_DIR"] = str(data_dir)
    if workspace is not None:
        settings["mcp_servers.lc.env.LC_WORKSPACE"] = str(workspace)
    if project_id:
        settings["mcp_servers.lc.env.LC_PROJECT_ID"] = project_id
    for feature in ["shell_tool", "plugins", "apps", "multi_agent", "memories"]:
        settings[f"features.{feature}"] = False
    settings["features.skip_host_skill_discovery"] = True
    for key, value in settings.items():
        # These are argument values passed directly to an executable, never shell text.
        result += ["-c", f"{key}={json.dumps(value)}"]
    return result


def find_receipt(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if {"req_id", "status", "tx_id"} <= value.keys():
            return value
        for child in value.values():
            if found := find_receipt(child):
                return found
    elif isinstance(value, list):
        for child in value:
            if found := find_receipt(child):
                return found
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return None
        if not isinstance(decoded, str):
            return find_receipt(decoded)
    return None


def run(
    prompt: str, port: int = 18844, name: str | None = None, model: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    directory = ROOT / "tests/reports/agent"
    directory.mkdir(parents=True, exist_ok=True)
    name = name or uuid.uuid4().hex
    event_path, error_path = directory / f"{name}.jsonl", directory / f"{name}.stderr.log"
    started = time.perf_counter()
    tools: dict[str, dict[str, Any]] = {}
    final = ""
    turn_started = None
    with (
        error_path.open("w", encoding="utf-8") as errors,
        event_path.open("w", encoding="utf-8") as events,
    ):
        process = subprocess.Popen(
            arguments(port, True, model, effort) + ["-"],
            env=child_environment(),
            cwd=ROOT / "project",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(prompt)
        process.stdin.close()
        for line in process.stdout:
            elapsed = time.perf_counter() - started
            try:
                event = json.loads(line)
            except ValueError:
                continue
            events.write(
                json.dumps({"elapsed_s": elapsed, "event": event}, ensure_ascii=False) + "\n"
            )
            events.flush()
            if event.get("type") == "turn.started":
                turn_started = elapsed
            item = event.get("item", {})
            if item.get("type") == "mcp_tool_call":
                identifier = item.get("id", "unknown")
                call = tools.setdefault(identifier, {"started_s": elapsed})
                call.update(tool=item.get("tool", ""), arguments=item.get("arguments", {}))
                if event["type"] == "item.started":
                    print(f"{call['tool']} {call['arguments']}", file=sys.stderr, flush=True)
                if event["type"] == "item.completed":
                    call["completed_s"] = elapsed
                    call["receipt"] = find_receipt(item.get("result"))
                    print(
                        f"{call['tool']} | {elapsed - call['started_s']:.3f} s",
                        file=sys.stderr,
                        flush=True,
                    )
            if item.get("type") == "agent_message" and event["type"] == "item.completed":
                final = item.get("text", "")
        code = process.wait()
    return {
        "exit_code": code,
        "elapsed_s": time.perf_counter() - started,
        "turn_started_s": turn_started,
        "tools": list(tools.values()),
        "final": final,
        "events": str(event_path),
        "stderr": str(error_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", help="Run one ephemeral turn; omit for interactive Codex")
    parser.add_argument("--port", type=int, default=18844)
    parser.add_argument("--model", help="Override the project's P0 model")
    parser.add_argument("--effort", help="Override the model reasoning effort")
    parser.add_argument(
        "--cold", action="store_true", help="Measure the separate cold CLI exec path"
    )
    args = parser.parse_args()
    if args.cold:
        if args.prompt is None:
            parser.error("--cold requires --prompt")
        result = run(args.prompt, args.port, model=args.model, effort=args.effort)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result["exit_code"]
    from .resident import ResidentAgent

    with ResidentAgent(args.port, args.model, effort=args.effort) as agent:
        if args.prompt is not None:
            result = agent.turn(args.prompt)
            result["agent_setup_s"] = agent.setup_s
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "completed" else 1
        print("ProjectLC 对话已就绪。输入关卡指令；/exit 退出。", flush=True)
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if prompt == "/exit":
                break
            if prompt:
                print(agent.turn(prompt)["final"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
