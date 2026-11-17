"""One real native designer, against the explicitly disposable growth test world."""

import json
import threading
import time
from pathlib import Path

from lc.agent_codex import CodexSession
from lc.client import Client
from lc.integration import install
from lgg.build import BuildService

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests/reports/upward-growth/agent"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    workspace, data = OUTPUT / "workspace", OUTPUT / "data"
    client = Client(18872, workspace=workspace, data_dir=data, project_id="growth-validation")
    assert client.health().get("upward_growth")
    assert client.edit("presentation cancel")["status"] == "applied"
    before = client.read("objects * --count")["count"]
    install(workspace, 18872, "growth-validation", data)
    holder = {}
    events = []

    def event(value):
        events.append(value)
        if value["type"] == "approval":
            # Only the isolated LevelControl integration is authorized by this test.
            allowed = "levelcontrol" in value.get("name", "").lower()
            holder["session"].respond(value["request_id"], {"allow": allowed})
        elif value["type"] == "question":
            holder["session"].interrupt()

    session = CodexSession(18872, workspace=workspace, data_dir=data,
                           project_id="growth-validation", on_event=event)
    holder["session"] = session
    (OUTPUT / "preparation.json").write_text(
        json.dumps({"model": session.model, "baseline_entities": before}), "utf-8")
    timeout = threading.Timer(180, session.interrupt)
    timeout.start()
    started = time.perf_counter()
    try:
        turn = session.turn("在我前方建一个可进入的小门廊，有台基、四根柱和顶部横梁。"
                            "整体约 8 米宽、6 米深、4 米高，石灰石材质。"
                            "不要围墙，不要额外装饰。设计完一部分就让它向上生长出来。")
        calls = turn.get("tools", [])
        names = [c.get("tool", "") for c in calls]
        assert "lgg_plan" in names and names.count("lgg_module") >= 2, names
        assert not any("spawn" in name or "fork" in name for name in names), names
        results = [c["receipt"] for c in calls if (c.get("receipt") or {}).get("scene")]
        assert results, calls
        result = results[-1]
        until = time.perf_counter() + 20
        receipt = client.receipt(result["req_id"])
        while receipt["status"] == "scheduled" and time.perf_counter() < until:
            time.sleep(.05)
            receipt = client.receipt(result["req_id"])
        assert receipt["status"] == "applied", receipt
        finalized = BuildService(client).finalize_receipt(result["req_id"], receipt)
        assert finalized["presentation_ready"], finalized
        report = {"passed": True, "model": session.model,
                  "seconds": time.perf_counter() - started, "tools": names,
                  "turn": turn, "finalized": finalized, "events": events}
        assert client.edit(f"undo --tx-id {receipt['tx_id']}")["status"] == "applied"
        assert client.read("objects * --count")["count"] == before
        (OUTPUT / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps({"passed": True, "model": session.model, "tools": names,
                          "seconds": report["seconds"]}, ensure_ascii=False))
    finally:
        timeout.cancel()
        session.close()
        (OUTPUT / "events.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    main()
