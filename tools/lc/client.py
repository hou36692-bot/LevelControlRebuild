"""Supported scripting boundary: commands and JSON over loopback HTTP."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from .command import parse

ROOT = Path(__file__).resolve().parents[2]
CONNECTION = ROOT / "project/Saved/LevelControl/client.json"


class Client:
    def __init__(self, port: int | None = None, timeout: float = 30, *,
                 data_dir: Path | None = None, workspace: Path | None = None,
                 project_id: str | None = None, design_file: Path | None = None) -> None:
        configured = data_dir or os.environ.get("LC_DATA_DIR")
        self.data_dir = Path(configured).resolve() if configured else None
        self.connection_file = self.data_dir / "client.json" if self.data_dir else CONNECTION
        self.workspace = Path(workspace or os.environ.get("LC_WORKSPACE", ROOT)).resolve()
        self.project_id = project_id or os.environ.get("LC_PROJECT_ID", "legacy")
        configured_design = design_file or os.environ.get("LC_DESIGN_FILE")
        self.design_file = (Path(configured_design).resolve() if configured_design else
                            self.data_dir / "design.json" if self.data_dir else None)
        self.bound_port = int(os.environ["LC_BIND_PORT"]) if "LC_BIND_PORT" in os.environ else None
        if port is None:
            saved = (json.loads(self.connection_file.read_text())
                     if self.connection_file.exists() else {})
            port = int(os.environ.get("LC_PORT", saved.get("port", 18844)))
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError("Port must be 1024..65535")
        self.port = port
        if self.bound_port is not None and port != self.bound_port:
            raise ValueError("Port differs from the bound project")
        self.timeout = timeout
        self._opener = build_opener(ProxyHandler({}))

    def _request(self, path: str, data: dict[str, Any] | None = None,
                 timeout: float | None = None) -> dict[str, Any]:
        payload = None if data is None else json.dumps(data, allow_nan=False).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError) as error:
            raise ConnectionError(
                f"ProjectLC request failed on port {self.port}: {error}"
            ) from error

    def health(self, timeout: float = 0.4) -> dict[str, Any]:
        return self._request("/health", timeout=timeout)

    def connect(self, command: str = "--port 18844") -> dict[str, Any]:
        port = parse("connect", command).data["port"]
        if self.bound_port is not None and port != self.bound_port:
            raise ValueError("This session cannot switch the bound project port")
        previous = self.port
        self.port = port
        try:
            health = self._request("/health")
            if health.get("ok") is not True or health.get("port") != port:
                raise ConnectionError("Unexpected health response")
        except Exception:
            self.port = previous
            raise
        self.connection_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.connection_file.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps({"port": port}), encoding="utf-8")
        temporary.replace(self.connection_file)
        return health

    def help(self, command: str = "") -> str:
        filename = parse("help", command).path
        return (ROOT / "docs/lc_help" / filename).read_text(encoding="utf-8")

    def read(self, command: str) -> dict[str, Any]:
        parsed = parse("read", command)
        if parsed.path == "catalog":
            catalog = self._request("/v1/catalog")
            for kind in ["props", "prefabs"]:
                catalog[kind] = [item for item in catalog[kind]
                                 if (not parsed.data["category"] or
                                     item["category"] == parsed.data["category"])
                                 and (not parsed.data["tag"] or
                                      parsed.data["tag"] in item["tags"])
                                 and parsed.data["search"].lower() in
                                     (item["id"] + " " + " ".join(item["tags"])).lower()]
            return catalog
        if parsed.path == "/v1/checks":
            return self.check(parsed.data)
        if parsed.path == "walk-graph":
            from checks.walk_graph import walk_graph

            return walk_graph(self, **parsed.data)
        query = "?" + urlencode(parsed.data) if parsed.data else ""
        return self._request(parsed.path + query)

    def check(self, data: dict[str, Any]) -> dict[str, Any]:
        before = self.read("objects * --count")["version"]
        player = self.read("player-status")
        result = self._request("/v1/checks", data)
        after = self.read("objects * --count")["version"]
        result["world_version"] = before
        result["player_parameters"] = {key: player[key] for key in (
            "capsule_radius_cm", "capsule_half_height_cm", "max_step_height_cm",
            "walkable_floor_angle_deg", "max_walk_speed_cm_s", "jump_velocity_cm_s",
            "gravity_cm_s2", "gravity_scale") if key in player}
        result["valid_for_snapshot"] = before == after
        return result

    def receipt(self, request_id: str) -> dict[str, Any]:
        return self._request("/v1/receipt?" + urlencode({"req_id": request_id}))

    def _write(
        self, path: str, body: dict[str, Any], request_id: str | None, wait: bool = True
    ) -> dict[str, Any]:
        request_id = request_id or f"lc-{uuid.uuid4().hex}"
        started = time.monotonic()
        try:
            receipt = self._request(path, {**body, "req_id": request_id})
            while wait and receipt.get("status") == "scheduled":
                if time.monotonic() - started >= self.timeout:
                    raise TimeoutError(f"Request remains scheduled: {request_id}; poll this req_id")
                time.sleep(0.01)
                receipt = self.receipt(request_id)
            return receipt
        except ConnectionError as error:
            raise ConnectionError(f"{error}; replay with the same req_id={request_id}") from error

    def query(self, query: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        result = self._request("/v1/query", query)
        result["tool_ms"] = (time.perf_counter() - started) * 1000
        result["response_bytes"] = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        return result

    def changes(self, cursor: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("/v1/changes", {"cursor": cursor} if cursor else {})

    def semantic_state(self, source: str = "") -> dict[str, Any]:
        return self._request("/v1/semantic-state?" + urlencode({"source": source}))

    def annotate(self, ids: list[str], semantic: dict[str, Any], request_id: str,
                 expected_version: int) -> dict[str, Any]:
        return self._write("/v1/annotations", {"ids": ids, "semantic": semantic,
                            "expected_version": expected_version}, request_id)

    def edit(self, command: str, request_id: str | None = None,
             expected_version: int | None = None,
             selection_id: str | None = None) -> dict[str, Any]:
        parsed = parse("edit", command)
        if ((selection_id is not None or expected_version is not None)
                and parsed.path != "/v1/edits"):
            raise ValueError("Selection/version arguments require an apply edit")
        if parsed.path in {"place", "layout"}:
            from .placement import layout_operations, place_operations

            if parsed.path == "place":
                operations = place_operations(self.read("catalog"), parsed.data)
                body = {"operations": operations, "command": command}
                preview = parsed.data["action"] == "preview"
                if preview:
                    body["action"] = "preview"
                return self._write("/v1/design" if preview else "/v1/edits", body, request_id)
            current = self.read(f"objects {parsed.data['selector']} --limit 16384")
            if "entities" not in current:
                return current
            return self._write("/v1/edits", {
                "operations": layout_operations(current["entities"], parsed.data),
                "command": command, "expected_version": current["version"],
            }, request_id)
        if parsed.file is not None:
            path = (self.workspace / parsed.file).resolve()
            if not path.is_relative_to(self.workspace) or path.suffix.lower() != ".json":
                raise ValueError("Batch files must be JSON inside this workspace")
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("Batch file exceeds 8 MiB")
            contents = json.loads(path.read_text(encoding="utf-8"))
            parsed.data = {"operations": contents} if isinstance(contents, list) else contents
            if not isinstance(parsed.data, dict) or not isinstance(
                parsed.data.get("operations"), list
            ):
                raise ValueError("Batch file must contain an operations list")
        for operation in parsed.data.get("operations", []):
            if operation.get("op") == "create" and parsed.file is None:
                entity = operation["entity"]
                transform = entity["transform"]
                if transform["size_cm"] is None:
                    templates = self.read("templates")["templates"]
                    template = next(
                        (item for item in templates if item["id"] == entity["template"]), None
                    )
                    if template is None:
                        raise ValueError(f"Unknown template: {entity['template']}")
                    transform["size_cm"] = template["size_cm"]
                if parsed.front_cm is not None:
                    player = self.read("player-status")
                    transform["position_cm"] = [
                        position + facing * parsed.front_cm
                        for position, facing in zip(
                            player["feet_cm"], player["forward"], strict=True
                        )
                    ]
                    transform["position_cm"][2] += transform["size_cm"][2] / 2
        body = {**parsed.data, "command": command}
        if expected_version is not None:
            body["expected_version"] = expected_version
        if selection_id is not None:
            body["selection_id"] = selection_id
        return self._write(parsed.path, body, request_id,
                           wait=parsed.path not in {"/v1/actions", "/v1/review"})

    def apply(
        self, operations: list[dict[str, Any]], request_id: str | None = None,
        *, expected_version: int | None = None, selection_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply computed operations; protocol distances are already centimeters."""
        return self._write(
            "/v1/edits",
            {"command": "apply computed operations", "operations": operations,
             **({"expected_version": expected_version} if expected_version is not None else {}),
             **({"selection_id": selection_id} if selection_id is not None else {})},
            request_id,
        )

    def capture(self, command: str = "photo.png", request_id: str | None = None) -> dict[str, Any]:
        parsed = parse("capture", command)
        return self._write(parsed.path, parsed.data, request_id,
                           wait=parsed.path != "/v1/review" and "center_cm" not in parsed.data)

    def preview(self, body: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
        return self._write("/v1/preview", body, request_id)

    def commit(self, body: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
        """Queue a commit without occupying the dialogue for the presentation duration."""
        return self._write("/v1/commit", body, request_id, wait=False)
