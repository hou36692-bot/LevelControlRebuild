"""Render a reviewable courtyard demo in an empty game and retain its formal entities."""

from __future__ import annotations

import argparse
import json
import shutil
import time

from lc.client import ROOT, Client
from lgg.service import DesignService
from test_w23_live import MODULES, SITE, completed, draft, terminal


def main(port: int) -> None:
    client = Client(port=port)
    assert client.read("objects * --count")["count"] == 0, "Demo requires an empty game"
    service = DesignService(client)
    service.invoke("lgg_card", "init --scene courtyard-demo")
    draft(service, 1, "site", SITE)
    draft(service, 2, "coarse", MODULES[:3])
    time.sleep(0.1)
    captures = {}

    def capture(name: str) -> None:
        receipt = client.capture(name + ".png")
        assert receipt["status"] == "applied", receipt
        destination = ROOT / f"docs/validation/{name}.png"
        shutil.copy2(receipt["path"], destination)
        captures[name] = str(destination)

    capture("w23-coarse")
    for number in range(1, 4):
        draft(
            service,
            number + 2,
            "module",
            {},
            f"--id {number}" + (" --complete" if number == 3 else ""),
        )
    queued = service.invoke("lgg_run", "play --duration 8")
    assert queued["status"] == "scheduled", queued
    time.sleep(3)
    capture("w23-flight")
    applied = terminal(client, queued["req_id"])
    assert applied["status"] == "applied", applied
    time.sleep(0.2)
    capture("w23-hero")
    state = completed(client)
    report = {
        "captures": captures,
        "commit": applied,
        "presentation": state,
        "consistency": client.read("consistency"),
        "port": port,
    }
    (ROOT / "tests/reports/w23-visual.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18844)
    main(parser.parse_args().port)
