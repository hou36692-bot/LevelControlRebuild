"""Thirty real minutes of packaged-game work with the actual desktop and resident model."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lc.client import Client
from lc.desktop import Companion
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication
from test_w23_live import completed, draft, make_design, terminal


class ProcessMemory(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
        (name, ctypes.c_size_t)
        for name in [
            "peak",
            "working",
            "peak_paged",
            "paged",
            "peak_nonpaged",
            "nonpaged",
            "pagefile",
            "peak_pagefile",
        ]
    ]


def memory(pid: int) -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    process = kernel.OpenProcess(0x410, False, pid)
    if not process:
        raise RuntimeError(f"Process {pid} exited")
    try:
        data = ProcessMemory()
        data.cb = ctypes.sizeof(data)
        call = ctypes.WinDLL("psapi").GetProcessMemoryInfo
        call.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        if not call(process, ctypes.byref(data), data.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return data.working
    finally:
        kernel.CloseHandle(process)


class Signals(QObject):
    prompt = Signal(str)
    done = Signal(dict)


def run(window: Companion, signals: Signals, output: Path, game_pid: int, minutes: int) -> None:
    report = {
        "complete": False,
        "passed": False,
        "cycles": [],
        "model_turns": [],
        "game_pid": game_pid,
        "desktop_pid": os.getpid(),
        "requested_minutes": minutes,
        "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
    }
    client = Client(port=18844)
    session = uuid.uuid4().hex[:8]
    empty_name = "soak-empty-" + session

    def write() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(output)

    def prompt(text):
        previous = window.last_result
        signals.prompt.emit(text)
        deadline = time.perf_counter() + 120
        while window.last_result is previous:
            if time.perf_counter() > deadline:
                raise TimeoutError("Desktop model turn timed out")
            time.sleep(0.1)
        result = window.last_result
        if result["status"] != "completed":
            raise RuntimeError(result)
        report["model_turns"].append(result)
        return result

    started = time.perf_counter()
    try:
        deadline = started + 90
        while not window.ready or not window.online or window.backend.speech is None:
            if time.perf_counter() >= deadline:
                raise TimeoutError("Desktop did not become ready")
            time.sleep(0.1)
        assert client.read("objects * --count")["count"] == 0, "Use an empty dedicated game"
        assert client.edit(f"checkpoint save {empty_name}")["status"] == "applied"
        started = time.perf_counter()
        while time.perf_counter() - started < minutes * 60:
            cycle_started = time.perf_counter()
            index = len(report["cycles"])
            if index % 5 == 0:
                prompt("在我面前放一根5米高的柱子然后撤销")
                assert client.read("consistency")["memory_count"] == 0
            service = make_design(client, "soak-courtyard")
            request = service.invoke("lgg_run", "play --duration 4 --music architect-pulse")
            assert request["status"] == "scheduled", request
            if index % 3 == 0:
                time.sleep(0.25)
                draft(service, 9, "module", {"size_m": [3, 3, 2.4]}, "--id 3 --complete")
                assert client.receipt(request["req_id"])["status"] == "rejected"
                assert client.read("consistency")["memory_count"] == 0
                request = service.invoke("lgg_run", "play --duration 4 --music architect-pulse")
            receipt = terminal(client, request["req_id"])
            assert receipt["status"] == "applied"
            state = completed(client)
            assert state["max_beat_error_ms"] <= 80
            request = client.edit("act soak-courtyard-01-04-00 spin --degrees 90 --duration 0.3")
            assert terminal(client, request["req_id"])["status"] == "applied"
            assert client.edit("undo --last")["status"] == "applied"
            name = f"soak-built-{session}-{index}"
            assert client.edit(f"checkpoint save {name}")["status"] == "applied"
            assert client.edit("apply delete soak-courtyard-*")["status"] == "applied"
            assert client.edit(f"restore --checkpoint {name}")["status"] == "applied"
            assert client.read("consistency")["memory_count"] == 188
            assert client.edit(f"restore --checkpoint {empty_name}")["status"] == "applied"
            consistent = client.read("consistency")
            assert consistent["ok"] and consistent["memory_count"] == 0
            client.edit("presentation cancel")
            row = {
                "index": index + 1,
                "elapsed_s": time.perf_counter() - started,
                "cycle_s": time.perf_counter() - cycle_started,
                "presentation": state,
                "consistency": consistent,
                "game_memory_bytes": memory(game_pid),
                "desktop_memory_bytes": memory(os.getpid()),
            }
            report["cycles"].append(row)
            report["elapsed_s"] = time.perf_counter() - started
            write()
            print(
                f"Soak {report['elapsed_s']:.1f}/{minutes * 60} s; cycle {index + 1}; consistent",
                flush=True,
            )
            while (
                time.perf_counter() - cycle_started < 60
                and time.perf_counter() - started < minutes * 60
            ):
                time.sleep(1)
        result = prompt("做一个三面围合、中间有喷泉的庭院，开启配乐。")
        requests = [
            c["receipt"]
            for c in result["tools"]
            if c["tool"] == "lgg_run" and c["receipt"] and c["receipt"]["status"] == "scheduled"
        ]
        assert requests, result
        assert terminal(client, requests[-1]["req_id"])["status"] == "applied"
        report["final_presentation"] = completed(client)
        report["final_consistency"] = client.read("consistency")
        report["elapsed_s"] = time.perf_counter() - started
        report["complete"] = True
        report["passed"] = report["elapsed_s"] >= 1800 and report["final_consistency"]["ok"]
        report["ended_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        write()
        print(
            "SOAK_COMPLETE "
            + json.dumps(
                {k: report[k] for k in ["passed", "elapsed_s", "game_pid", "desktop_pid"]}
            ),
            flush=True,
        )
    except Exception as error:
        report["error"] = str(error)
        report["elapsed_s"] = time.perf_counter() - started
        write()
        print("SOAK_FAILED " + repr(error), flush=True)
    signals.done.emit(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-pid", type=int, required=True)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--screenshot", type=Path, required=True)
    args = parser.parse_args()
    app = QApplication([])
    widget = Companion(18844, screenshot=args.screenshot)
    widget.show()
    signals = Signals()
    signals.prompt.connect(widget.submit)
    signals.done.connect(lambda report: QTimer.singleShot(400, widget.capture))
    worker = threading.Thread(
        target=run, args=(widget, signals, args.output, args.game_pid, args.minutes), daemon=True
    )
    worker.start()
    app.exec()
