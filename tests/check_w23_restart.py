"""Verify durable checkpoints/source ownership and discard transient previews on restart."""

from __future__ import annotations

import json
import subprocess
import time
import uuid

from lc.client import ROOT, Client
from lgg.compiler import delta
from test_w23_live import draft, make_design, terminal


def main() -> None:
    port = 18847
    client = Client(port=port)
    try:
        client._request("/health")
    except ConnectionError:
        pass
    else:
        raise RuntimeError("Port 18847 is already occupied; do not reuse another test world")
    database = ROOT / f"project/Saved/Tests/restart-w23-{uuid.uuid4().hex}.sqlite3"
    arguments = [
        r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe",
        str(ROOT / "project/ProjectLC.uproject"),
        "-game",
        "-windowed",
        "-ResX=1280",
        "-ResY=720",
        "-NoSound",
        "-DisablePython",
        "-nosplash",
        f"-LCPort={port}",
        f"-LCStore={database}",
        f"-abslog={ROOT / 'project/Saved/Logs/restart-w23.log'}",
    ]
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE

    def start() -> subprocess.Popen:
        process = subprocess.Popen(arguments, startupinfo=startup)
        deadline = time.perf_counter() + 60
        while time.perf_counter() < deadline:
            try:
                if client._request("/health")["ok"]:
                    return process
            except ConnectionError:
                pass
            if process.poll() is not None:
                raise AssertionError("Game exited before health")
            time.sleep(0.2)
        process.terminate()
        process.wait(timeout=10)
        raise AssertionError("Game startup timed out")

    process = start()
    try:
        assert client.edit("checkpoint save empty")["status"] == "applied"
        service = make_design(client, "restart-court")
        queued = service.invoke("lgg_run", "play --duration 1")
        receipt = terminal(client, queued["req_id"])
        assert receipt["status"] == "applied"
        assert client.edit("checkpoint save built")["status"] == "applied"
        expected = client.read("objects * --limit 16384")["entities"]
        body = service.load()["commit_body"]
        draft(service, 9, "module", {"size_m": [3, 3, 2.4]}, "--id 3 --complete")
        time.sleep(0.2)
        assert client.read("presentation")["preview_count"] > 0
        process.terminate()
        process.wait(timeout=15)
        process = start()
        restored = client.read("objects * --limit 16384")["entities"]
        assert restored == expected
        assert client.read("presentation")["state"] == "idle"
        assert client.read("presentation")["preview_count"] == 0
        assert client.commit(body, queued["req_id"]) == receipt
        assert client.edit("restore --checkpoint empty")["status"] == "applied"
        assert client.read("consistency")["memory_count"] == 0
        assert client.edit("undo --last")["status"] == "applied"
        assert not any(delta(expected, client.read("objects * --limit 16384")["entities"]).values())
        assert client.edit("undo --last")["status"] == "applied"
        assert client.read("consistency")["memory_count"] == 0
        report = {
            "passed": True,
            "entities_restored": len(expected),
            "database": str(database),
            "sources_restored": sorted({e["source"] for e in restored}),
            "receipt_replayed_identically": True,
            "previews_discarded": True,
            "checkpoint_restore_and_undo": True,
            "original_commit_undo": True,
            "final_consistency": client.read("consistency"),
        }
        (ROOT / "tests/reports/w23-restart.json").write_text(json.dumps(report, indent=2), "utf-8")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()
