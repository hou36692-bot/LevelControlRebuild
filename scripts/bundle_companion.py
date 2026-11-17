"""Assemble a local runnable desktop distribution without copying user credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sync_documentation(output: Path) -> None:
    shutil.copytree(ROOT / "docs/lc_help", output / "docs/lc_help", dirs_exist_ok=True)
    semantic_guide = ROOT / "docs/语义核心-使用指南.md"
    if semantic_guide.is_file():
        shutil.copy2(semantic_guide, output / "docs/语义核心-使用指南.md")
    text = (ROOT / "docs/3.2-使用指南.md").read_text("utf-8")
    (output / "README.md").write_text(text, "utf-8")


def main(engine: Path, output: Path, port: int = 18870,
         game_source: Path | None = None, runtime_binary: Path | None = None) -> None:
    if not 1024 <= port <= 65535:
        raise ValueError("Port must be 1024..65535")
    companion = output / "Companion"
    runtime = output / "Runtime/Python"
    game_provenance = None
    if game_source is not None:
        game_source = game_source.resolve()
        if not (game_source / "Windows/ProjectLC.exe").is_file():
            raise FileNotFoundError("game-source must contain Windows/ProjectLC.exe")
        game_provenance = str(game_source)
        shutil.copytree(game_source, output / "Game", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("Saved", "Intermediate", "*.pdb"))
    if runtime_binary is not None:
        if runtime_binary.name != "ProjectLC-Win64-Shipping.exe" or not runtime_binary.is_file():
            raise FileNotFoundError("runtime-binary must be the compiled ProjectLC Shipping executable")
        destination = output / "Game/Windows/ProjectLC/Binaries/Win64/ProjectLC-Win64-Shipping.exe"
        if not destination.parent.is_dir():
            raise FileNotFoundError("Cooked game content must be supplied before the runtime binary")
        shutil.copy2(runtime_binary, destination)
    shutil.copytree(engine / "Engine/Binaries/ThirdParty/Python3/Win64", runtime,
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    packages = Path(sys.prefix) / "Lib/site-packages"
    shutil.copytree(packages, runtime / "Lib/site-packages", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "__editable__*"))
    shutil.copytree(ROOT / "tools", companion / "tools", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", "*.egg-info", "api_agent.py", "model_panel.py"))
    for source in ["docs/lc_help", "ue/Plugins/LevelControlRuntime"]:
        shutil.copytree(ROOT / source, companion / source, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("Binaries", "Intermediate", "Saved", "__pycache__", "*.pdb"))
    (companion / "project").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "project/AGENTS.md", companion / "project/AGENTS.md")
    shutil.copytree(ROOT / "project/.codex", companion / "project/.codex",
                    dirs_exist_ok=True)
    shutil.copy2(ROOT / "scripts/launch_bundle.py", output / "launch.py")
    from build_client import build_client

    build_client(output)
    (output / "Start-LevelControl.cmd").write_text(
        '@echo off\r\nstart "" "%~dp0LevelControl.exe"\r\n', encoding="ascii")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-c", "core.fsmonitor=false", "status", "--porcelain"], cwd=ROOT))
    shipping = output / "Game/Windows/ProjectLC/Binaries/Win64/ProjectLC-Win64-Shipping.exe"
    (output / "build-info.json").write_text(json.dumps({
        "version": "3.2.0", "native_harness": True, "protocol": 4, "port": port,
        "semantic_core": True, "entity_target": 10000, "database_schema": 3,
        "runtime_binary_source": str(runtime_binary) if runtime_binary else None,
        "knowledge_review": "pending_user_review",
        "harnesses": ["codex", "claude"], "full_bubble_chat": True,
        "native_model_selection": True,
        "fast_build": True, "short_presentation": True,
        "upward_growth": True, "incremental_preview": True, "single_designer": True,
        "resolution": [1920, 1080], "git_revision": revision, "dirty": dirty,
        "game_source": game_provenance,
        "game_sha256": hashlib.sha256(shipping.read_bytes()).hexdigest()
        if shipping.is_file() else None,
        "acceptance": "user_testing_pending",
        "built_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2), "utf-8")
    sync_documentation(output)
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", type=Path, default=Path(r"C:\Program Files\Epic Games\UE_5.8"))
    parser.add_argument("--output", type=Path, default=ROOT / "dist/LevelControl")
    parser.add_argument("--port", type=int, default=18870)
    parser.add_argument("--game-source", type=Path,
                        help="Reuse an existing packaged game; record its source and SHA-256")
    parser.add_argument("--runtime-binary", type=Path, help="Replace only the runtime executable; reuse unchanged cooked content")
    args = parser.parse_args()
    main(args.engine_root, args.output, args.port, args.game_source, args.runtime_binary)
