"""Small versioned JSON settings helpers shared by the desktop client."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def load_versioned(path: Path, version: int = 1) -> dict[str, Any] | None:
    """Return parsed settings or None when the file is missing/invalid.

    The original file is never rewritten here.  Callers keep an ``error``
    string and fall back to defaults, matching ModelSettings behaviour.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != version:
        return None
    return data


def save_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON with a same-directory temporary file and ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def base_directory(override: str | None = None) -> Path:
    """The per-user LevelControl data directory used by desktop settings."""
    if override:
        return Path(override)
    if os.environ.get("LC_USER_DATA_ROOT"):
        return Path(os.environ["LC_USER_DATA_ROOT"])
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LevelControl"


def default_path(name: str, env_name: str) -> Path:
    value = os.environ.get(env_name)
    return Path(value) if value else base_directory() / name
