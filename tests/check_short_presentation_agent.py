"""Verify the real model selects short reveals without extra presentation tool calls."""

import json
import uuid

from lc.client import ROOT, Client
from lc.resident import ResidentAgent


def main() -> None:
    client = Client(18861)
    assert client.read("objects * --count")["count"] == 0, "Use the isolated test world"
    checkpoint = "short-agent-" + uuid.uuid4().hex
    client.edit(f"checkpoint save {checkpoint}")
    report = {"runs": [], "passed": False}
    output = ROOT / "tests/reports/short-presentation-agent.json"
    try:
        with ResidentAgent(port=18861, model="gpt-6-astra", effort="low") as agent:
            prompts = [
                "在我面前建一座小礼拜堂，有入口、三面墙和屋顶。",
                "保留刚才礼拜堂的原有结构不变，在它右侧新增一座独立小钟楼。",
                "把钟楼的颜色改成浅灰色，不改变位置和尺寸。",
            ]
            for index, prompt in enumerate(prompts):
                turn = agent.turn(prompt)
                builds = [c["receipt"] for c in turn["tools"] if c["tool"] == "lgg_build"
                          and c.get("receipt") and c["receipt"]["status"] == "applied"]
                row = {"prompt": prompt, "turn": turn, "presentation": client.read("presentation")}
                report["runs"].append(row)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
                if index < 2:
                    assert len(builds) == 1, turn
                    expected = 4 if index == 0 else 3
                    assert builds[0]["presentation"]["duration_s"] == expected, builds
                    assert builds[0]["presentation"]["camera"] == "player"
                    assert builds[0]["presentation"]["music"] == "none"
                else:
                    assert all(r["presentation"]["duration_s"] == 0 for r in builds)
                    assert any((c.get("receipt") or {}).get("status") == "applied"
                               for c in turn["tools"])
                print(json.dumps({"index": index, "seconds": turn["elapsed_s"],
                                  "builds": [r["presentation"] for r in builds]}), flush=True)
        report["passed"] = True
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    finally:
        client.edit("presentation cancel")
        assert client.edit(f"restore --checkpoint {checkpoint}")["status"] == "applied"


if __name__ == "__main__":
    main()
