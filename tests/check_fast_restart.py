"""Restart a private Shipping world and verify complete geometry/environment recovery."""

import json
import subprocess
import time
import uuid

from lc.client import ROOT, Client
from lgg.build import BuildService
from test_build_live import plan


def main() -> None:
    port = 18856
    client = Client(port, timeout=1)
    try:
        client._request("/health")
    except ConnectionError:
        pass
    else:
        raise RuntimeError("The isolated restart port is already occupied")
    client.timeout = 30
    database = ROOT / f"project/Saved/Tests/fast-restart-{uuid.uuid4().hex}.sqlite3"
    executable = ROOT / ("dist/LevelControlFast/Game/Windows/ProjectLC/Binaries/Win64/"
                         "ProjectLC-Win64-Shipping.exe")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE

    def start():
        process = subprocess.Popen([str(executable), "-game", "-windowed", "-ResX=1920",
                                    "-ResY=1080", f"-LCPort={port}", f"-LCStore={database}"],
                                   startupinfo=startup)
        deadline = time.perf_counter() + 45
        while time.perf_counter() < deadline:
            try:
                if client._request("/health").get("fast_build"):
                    return process
            except ConnectionError:
                pass
            if process.poll() is not None:
                raise RuntimeError("Isolated game exited during startup")
            time.sleep(0.2)
        process.terminate()
        process.wait(10)
        raise RuntimeError("Isolated startup timed out")

    process = start()
    try:
        service = BuildService(client)
        value, request = plan(), "restart-" + uuid.uuid4().hex
        result = service.build(value, request)
        assert result["status"] == "applied" and result["presentation_ready"], result
        entities = client.read("objects * --limit 16384")["entities"]
        environment = client.read("environment")["committed"]
        process.terminate()
        process.wait(15)
        process = start()
        assert client.read("objects * --limit 16384")["entities"] == entities
        assert client.read("environment")["committed"] == environment
        assert client.read("presentation")["state"] == "idle"
        replay = service.build(value, request)
        assert replay["tx_id"] == result["tx_id"] and replay["presentation_ready"], replay
        assert client.read("consistency")["memory_count"] == len(entities)
        undone = client.edit(f"undo --tx-id {result['tx_id']}")
        assert undone["status"] == "applied", undone
        assert client.read("consistency")["memory_count"] == 0
        report = {"passed": True, "database": str(database), "entities": len(entities),
                  "initial": result, "replayed_after_restart": replay, "undo": undone,
                  "consistency": client.read("consistency")}
        (ROOT / "tests/reports/fast-restart.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        print("Restart, replay, combined undo and consistency passed")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(15)


if __name__ == "__main__":
    main()
