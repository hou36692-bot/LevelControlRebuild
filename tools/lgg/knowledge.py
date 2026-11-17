"""Retrieve reviewed-status-aware design cases without a model or vector service."""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DIRECTORY = Path(__file__).with_name("knowledge")
TERMS = {"廊柱": "column colonnade", "柱子": "column", "庭院": "courtyard",
         "门洞": "doorway clearance", "门厅": "hall entrance", "走廊": "corridor route",
         "楼梯": "stairs elevation", "高差": "elevation stairs", "围合": "enclosure courtyard",
         "环廊": "cloister loop", "入口": "entrance route", "扩建": "extension override",
         "可达": "route clearance", "删除": "override identity", "修改": "override identity"}


def words(text: str) -> set[str]:
    expanded = text.lower()
    for term, replacement in TERMS.items():
        if term in expanded:
            expanded += " " + replacement
    return set(re.findall(r"[a-z0-9_-]+|[\u4e00-\u9fff]", expanded))


@lru_cache(maxsize=1)
def catalog() -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(path.read_text("utf-8"))
                 for path in sorted(DIRECTORY.glob("*/case.json")))


def retrieve(query: str = "", case_id: str | None = None) -> dict[str, Any]:
    if len(query) > 2048:
        raise ValueError("Knowledge query exceeds 2048 characters")
    if case_id is not None:
        entry = next((item for item in catalog() if item["id"] == case_id), None)
        if entry is None:
            raise ValueError("Unknown knowledge case_id")
        folder = DIRECTORY / entry["id"]
        result = {**entry, "plan": json.loads((folder / "plan.json").read_text("utf-8")),
                  "design": (folder / "design.md").read_text("utf-8"),
                  "artifacts": {
                      name: str(folder / name) for name in
                      ["plan.json", "whitebox.obj", "whitebox.json",
                       "identity-map.json"]}}
        if (folder / "extension.json").exists():
            result["extension_plan"] = json.loads(
                (folder / "extension.json").read_text("utf-8"))
        payload = json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
        result["content_hash"] = hashlib.sha256(payload).hexdigest()
        return result
    terms = words(query)
    ranked = []
    for entry in catalog():
        searchable = words(" ".join([entry["title"], *entry["tags"], *entry["principles"]]))
        score = len(terms & searchable)
        if terms and score == 0:
            continue
        ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
    keys = ["id", "title", "review_status", "tags", "principles", "sources"]
    results = [{key: entry[key] for key in keys} for _, entry in ranked[:3]]
    payload = json.dumps(results, sort_keys=True, ensure_ascii=False).encode()
    return {"schema": 1, "matches": results,
            "content_hash": hashlib.sha256(payload).hexdigest(),
            "notice": "案例为自建白盒，均待人工审核；"
                      "检索不等于运行时可达性验证。使用 case_id 按需加载计划。"}
