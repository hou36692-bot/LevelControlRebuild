"""Exercise packaged runtime, real desktop dialogue and four domains for thirty minutes."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from build_v2_reference import build
from lc.client import Client
from lc.desktop import Companion
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from run_soak import Signals, memory
from v2_language_cases import cross_cases, lighting_cases, scope_valid


def terminal(client: Client, receipt: dict) -> dict:
    deadline = time.monotonic() + 35
    while receipt.get("status") == "scheduled" and time.monotonic() < deadline:
        time.sleep(0.02)
        receipt = client.receipt(receipt["req_id"])
    assert receipt.get("status") == "applied", receipt
    return receipt


def snapshot(client: Client) -> dict:
    return {
        "entities": {
            item["id"]: item for item in client.read("objects * --limit 16384")["entities"]
        },
        "environment": client.read("environment"),
        "presentation": client.read("presentation"),
    }


class Frames:
    def __init__(self, port: int, path: Path) -> None:
        self.client = Client(port=port)
        self.path = path
        self.stop = threading.Event()
        self.values: list[float] = []
        self.errors: list[str] = []
        self.last = self.client.read("frame-history")["last"]
        self.thread = threading.Thread(target=self.collect, daemon=True)

    def collect(self) -> None:
        with gzip.open(self.path, "wt", encoding="utf-8", compresslevel=1) as output:
            while True:
                try:
                    data = self.client.read(f"frame-history --since {self.last}")
                    if data["truncated"] or data["last"] < self.last:
                        self.errors.append("Frame history was truncated or the game restarted")
                    self.last = data["last"]
                    self.values.extend(item["ms"] for item in data["frames"])
                    output.write(json.dumps(data, separators=(",", ":")) + "\n")
                    output.flush()
                except Exception as error:
                    self.errors.append(str(error))
                if self.stop.wait(5):
                    try:
                        data = self.client.read(f"frame-history --since {self.last}")
                        if data["truncated"]:
                            self.errors.append("Final frame history was truncated")
                        self.values.extend(item["ms"] for item in data["frames"])
                        output.write(json.dumps(data, separators=(",", ":")) + "\n")
                    except Exception as error:
                        self.errors.append(str(error))
                    return

    def finish(self) -> dict:
        self.stop.set()
        self.thread.join(timeout=40)
        values = sorted(self.values)
        return {
            "path": str(self.path),
            "count": len(values),
            "errors": self.errors,
            "frame_p95_ms": values[int((len(values) - 1) * 0.95)] if values else None,
            "max_ms": max(values) if values else None,
            "over_100_ms": sum(value > 100 for value in values),
        }


def run(window: Companion, signals: Signals, args) -> None:
    client = Client(port=args.port)
    session = uuid.uuid4().hex[:10]
    cases = (cross_cases() + lighting_cases())[: args.case_limit]
    report = {
        "complete": False,
        "passed": False,
        "pilot": args.minutes < 30,
        "requested_minutes": args.minutes,
        "game_pid": args.game_pid,
        "desktop_pid": os.getpid(),
        "cases": [],
        "cycles": [],
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    collector = None
    started = time.perf_counter()

    def save() -> None:
        report["elapsed_s"] = time.perf_counter() - started
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(args.output)

    def prompt(text: str) -> dict:
        previous = window.last_result
        signals.prompt.emit(text)
        deadline = time.monotonic() + 180
        while window.last_result is previous:
            if time.monotonic() >= deadline:
                raise TimeoutError("Desktop model turn did not finish within 180 s")
            time.sleep(0.05)
        result = dict(window.last_result)
        result["terminal"] = []
        for call in result.get("tools", []):
            receipt = call.get("receipt")
            if receipt and receipt.get("status") in {"scheduled", "applied"}:
                result["terminal"].append(terminal(client, receipt))
        return result

    try:
        deadline = time.monotonic() + 120
        while not window.ready or not window.online or window.backend.speech is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("Desktop/model/speech did not become ready")
            time.sleep(0.1)
        if client.read("objects * --count")["count"] == 0:
            report["reference_build"] = build(client)
        terminal(client, client.edit("restore --checkpoint reference-baseline"))
        assert client.read("objects * --count")["count"] == 314
        report["model"] = window.backend.agent.model
        report["effort"] = window.backend.agent.effort
        agent_id = window.backend.agent.thread_id
        report["agent_session"] = agent_id
        started = time.perf_counter()
        report["started_utc"] = datetime.now(timezone.utc).isoformat()
        collector = Frames(args.port, args.output.with_suffix(".frames.jsonl.gz"))
        collector.thread.start()
        save()
        duration = args.minutes * 60
        interval = duration / max(len(cases), 1)
        index = 0
        while time.perf_counter() - started < duration or index < len(cases):
            if window.backend.agent.thread_id != agent_id:
                raise AssertionError("Dialogue agent reconnected during stability run")
            memory(args.game_pid)
            if index < len(cases) and time.perf_counter() - started >= index * interval:
                if index == 20:
                    terminal(client, client.edit("restore --checkpoint reference-baseline"))
                case = cases[index]
                before = snapshot(client)
                result = prompt(case.prompt)
                after = snapshot(client)
                reason = ""
                try:
                    correct = (
                        result["status"] == "completed"
                        and case.check(before, after, result)
                        and scope_valid(case, before, after)
                    )
                except Exception as error:
                    correct, reason = False, str(error)
                rejected = sum(
                    call.get("receipt", {}).get("status") == "rejected"
                    for call in result.get("tools", [])
                    if call.get("receipt")
                )
                if case.domain == "lighting" and rejected > 2:
                    correct, reason = False, "More than two rejected tool attempts"
                first = [
                    call["completed_s"]
                    for call in result.get("tools", [])
                    if call.get("receipt")
                    and call["receipt"].get("status") in {"scheduled", "applied"}
                    and call["tool"] in {"lc.edit", "lc.capture"}
                ]
                report["cases"].append(
                    {
                        "index": index + 1,
                        "domain": case.domain,
                        "prompt": case.prompt,
                        "passed": bool(correct),
                        "reason": reason,
                        "first_tool_feedback_s": min(first) if first else None,
                        "turn": result,
                    }
                )
                index += 1
                print(
                    f"Language {index}/{len(cases)}: {correct}; "
                    f"{result['elapsed_s']:.2f} s; {case.domain}",
                    flush=True,
                )
                save()
            state = client.read("environment")
            presentation = client.read("presentation")
            if not state["preview_active"] and not presentation["design_preview_active"]:
                old = state["committed"]
                changed = terminal(client, client.edit("environment apply --sun-intensity 5"))
                terminal(client, client.edit("undo --tx-id " + changed["tx_id"]))
                assert client.read("environment")["committed"] == old
                changed = terminal(
                    client, client.edit("surface apply reference-column-0 --roughness 0.63")
                )
                terminal(client, client.edit("undo --tx-id " + changed["tx_id"]))
                temporary = "soak-" + session
                created = terminal(
                    client,
                    client.edit(f"prop place prop/lantern --id {temporary} --at 18m,-6m,1m --snap"),
                )
                terminal(client, client.edit("undo --tx-id " + created["tx_id"]))
                if len(report["cycles"]) % 5 == 0:
                    action = terminal(
                        client,
                        client.edit("act reference-column-0 spin --degrees 10 --duration 0.2"),
                    )
                    terminal(client, client.edit("undo --tx-id " + action["tx_id"]))
                if len(report["cycles"]) % 10 == 0:
                    name = f"soak-{session}-{len(report['cycles'])}"
                    terminal(client, client.edit(f"checkpoint save {name}"))
                    changed = terminal(
                        client, client.edit("apply move reference-column-0 --delta 1cm,0,0")
                    )
                    restored = terminal(client, client.edit(f"restore --checkpoint {name}"))
                    terminal(client, client.edit("undo --tx-id " + restored["tx_id"]))
                    terminal(client, client.edit("undo --tx-id " + changed["tx_id"]))
            consistent = client.read("consistency")
            assert consistent["ok"], consistent
            report["cycles"].append(
                {
                    "elapsed_s": time.perf_counter() - started,
                    "consistency": consistent,
                    "game_memory_bytes": memory(args.game_pid),
                    "desktop_memory_bytes": memory(os.getpid()),
                }
            )
            save()
            print(
                f"Soak {report['elapsed_s']:.1f}/{duration:.0f} s; "
                f"cycle {len(report['cycles'])}; consistent",
                flush=True,
            )
            target = min(duration, index * interval) if index < len(cases) else duration
            while time.perf_counter() - started < target:
                time.sleep(max(0.001, min(1, target - (time.perf_counter() - started))))
        terminal(client, client.edit("environment cancel"))
        terminal(client, client.edit("design cancel"))
        terminal(client, client.edit("restore --checkpoint reference-baseline"))
        report["final_consistency"] = client.read("consistency")
        report["frames"] = collector.finish()
        report["performance"] = terminal(client, client.edit("review performance --frames 300"))
        report["final_capture"] = terminal(
            client, client.capture("soak-final.png --center 20m,0,2m --radius 18m --height 8m")
        )
        report["cross_passed"] = sum(
            row["passed"] for row in report["cases"] if row["domain"] == "cross"
        )
        report["lighting_passed"] = sum(
            row["passed"] for row in report["cases"] if row["domain"] == "lighting"
        )
        report["stability_passed"] = (
            window.backend.agent.thread_id == agent_id
            and time.perf_counter() - started >= duration
            and not report["frames"]["errors"]
            and report["final_consistency"]["memory_count"] == 314
        )
        report["complete"] = True
        report["passed"] = (
            report["stability_passed"]
            and not report["pilot"]
            and report["cross_passed"] >= 16
            and report["lighting_passed"] >= 18
        )
        report["ended_utc"] = datetime.now(timezone.utc).isoformat()
        save()
        print(
            "SOAK_COMPLETE "
            + json.dumps(
                {
                    key: report[key]
                    for key in [
                        "passed",
                        "elapsed_s",
                        "cross_passed",
                        "lighting_passed",
                        "stability_passed",
                    ]
                }
            ),
            flush=True,
        )
    except Exception as error:
        report["error"] = str(error)
        if collector:
            report["frames"] = collector.finish()
        save()
        print("SOAK_FAILED " + str(error), flush=True)
    signals.done.emit(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--game-pid", type=int, required=True)
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--case-limit", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path, required=True)
    args = parser.parse_args()
    assert args.port >= 18849, "Use an isolated validation port, not a user's game"
    app = QApplication([])
    widget = Companion(args.port, screenshot=args.screenshot)
    widget.setWindowTitle("LevelControl 2.0 自动验收")
    widget.input.setReadOnly(True)
    widget.show()
    signals = Signals()
    signals.prompt.connect(widget.submit)

    def finish(report: dict) -> None:
        widget.capture()
        QTimer.singleShot(500, widget.exit_client)

    signals.done.connect(finish)
    worker = threading.Thread(target=run, args=(widget, signals, args), daemon=True)
    worker.start()
    app.exec()
