"""Run prepare, restart the same game database, then run verify."""

import argparse
import json

from lc.client import ROOT, Client

RECORD = ROOT / "tests/reports/restart.json"
COMMAND = "apply create restart-column --template blockout/column --size 1m,1m,5m --front 4m"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "verify"])
    args = parser.parse_args()
    client = Client(port=18844)
    if args.phase == "prepare":
        assert client.read("objects * --count")["count"] == 0
        receipt = client.edit(COMMAND)
        assert receipt["status"] == "applied", receipt
        record = {
            "receipt": receipt,
            "entity": client.read("objects restart-column")["entities"][0],
        }
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        RECORD.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print("Restart fixture persisted")
    else:
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        receipt = record["receipt"]
        assert client.read("objects restart-column")["entities"][0] == record["entity"]
        assert client.receipt(receipt["req_id"]) == receipt
        assert client.edit(COMMAND, request_id=receipt["req_id"]) == receipt
        assert client.read("objects * --count")["count"] == 1
        assert client.edit(f"undo --tx-id {receipt['tx_id']}")["status"] == "applied"
        state = client.read("consistency")
        assert state["ok"] and state["memory_count"] == state["sqlite_count"] == 0
        record.update(verified=True, final_state=state)
        RECORD.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print("Restart, stable ID, receipt replay and persisted undo passed")


if __name__ == "__main__":
    main()
