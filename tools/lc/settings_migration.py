"""One-time merge of legacy LevelControl3 state into the unified root."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings_io import base_directory, save_atomic

MARKER = "migration.json"


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _merge_projects(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    merged = {
        "version": 1,
        "active": target.get("active", ""),
        "targets": list(target.get("targets", [])),
        "recent": list(target.get("recent", [])),
    }
    index = {item.get("id"): position
             for position, item in enumerate(merged["targets"]) if item.get("id")}
    for item in source.get("targets", []):
        identity = item.get("id")
        if identity in index:
            merged["targets"][index[identity]] = item
        elif identity:
            index[identity] = len(merged["targets"])
            merged["targets"].append(item)
    active = source.get("active")
    if active and any(item.get("id") == active for item in merged["targets"]):
        merged["active"] = active
    merged["recent"] = list(dict.fromkeys(
        [*source.get("recent", []), *merged["recent"]]))[:8]
    return merged


def _copy_missing(source: Path, target: Path, *, suffix: bool) -> int:
    copied = 0
    if not source.is_dir():
        return copied
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.is_dir():
            destination = target / item.name
            if not destination.exists():
                shutil.copytree(item, destination)
                copied += 1
        elif item.is_file():
            destination = target / item.name
            if destination.exists() and suffix:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                destination = target / f"{item.stem}-{stamp}{item.suffix}"
            if not destination.exists():
                shutil.copy2(item, destination)
                copied += 1
    return copied


def migrate(target_root: Path | None = None,
            source_root: Path | None = None) -> dict[str, Any]:
    """Merge LevelControl3 project state once; never rewrite the source."""
    target = (target_root or base_directory()).resolve()
    source = (source_root or target.parent / "LevelControl3").resolve()
    marker = target / MARKER
    report: dict[str, Any] = {
        "version": 1,
        "source": str(source),
        "target": str(target),
        "targets": 0,
        "workspaces": 0,
        "logs": 0,
        "warnings": [],
        "status": "ok",
    }
    if marker.exists():
        previous = _load(marker)
        if (previous and previous.get("version") == 1
                and previous.get("status") in {"ok", "already_migrated"}):
            return {**previous, "status": "already_migrated"}
    target.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        report["status"] = "no_source"
        save_atomic(marker, report)
        return report

    source_projects = _load(source / "project-settings.json")
    if source_projects:
        destination = target / "project-settings.json"
        current = _load(destination) or {}
        merged = _merge_projects(source_projects, current)
        report["targets"] = len(merged["targets"])
        save_atomic(destination, merged)
    else:
        report["warnings"].append("source project-settings.json missing or invalid")

    report["workspaces"] = _copy_missing(source / "workspaces", target / "workspaces",
                                         suffix=False)
    report["logs"] = _copy_missing(source / "logs", target / "logs", suffix=True)
    report["migrated_utc"] = datetime.now(timezone.utc).isoformat()
    save_atomic(marker, report)
    return report
