"""Prepare or verify a persisted staged build across a real Shipping restart."""

import copy
import json
import sys
import time
import uuid

from capture_growth import REPORT, input_record, wait
from lc.client import Client
from lgg.stream import StreamService

game = Client(18872)
path = REPORT / "restart.json"
service = StreamService(game, REPORT / "restart-data")
if sys.argv[1] == "prepare":
    assert game.read("objects * --count")["count"] == 0
    before = game.read("environment")["state"]
    source = input_record()
    plan = copy.deepcopy(source["original_plan"])
    plan["environment"] = {"preset": "golden-hour"}
    plan["expected_version"] = game.read("objects * --count")["version"]
    result = service.plan(plan, uuid.uuid4().hex)
    build = result["build_id"]
    for brief in source["modules"]:
        module = source["delivered"][brief["id"]]["module"]
        request = uuid.uuid4().hex
        result = service.module(build, module, request)
    receipt = wait(game, result["req_id"])
    time.sleep(1.2)
    report = {"build": build, "receipt": receipt, "last_module": module,
              "last_request": request, "before_environment": before,
              "environment": game.read("environment")["state"],
              "entities": game.read(f"objects {build}-* --limit 16384")["entities"],
              "session": game.read("events")["session_id"]}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print("Prepared persistent staged build", len(report["entities"]))
else:
    report = json.loads(path.read_text("utf-8"))
    assert game.read("events")["session_id"] != report["session"]
    entities = game.read(f"objects {report['build']}-* --limit 16384")["entities"]
    assert entities == report["entities"]
    assert game.read("environment")["state"] == report["environment"]
    replay = service.module(report["build"], report["last_module"], report["last_request"])
    assert replay["construction_status"] == "applied" and replay["presentation_ready"]
    assert game.read("objects * --count")["count"] == len(entities)
    assert game.edit(f"undo --tx-id {report['receipt']['tx_id']}")["status"] == "applied"
    assert game.read("objects * --count")["count"] == 0
    assert game.read("environment")["state"] == report["before_environment"]
    report.update(passed=True, replay=replay, consistency=game.read("consistency"))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print("Restart, idempotent receipt recovery and geometry/environment undo passed")
