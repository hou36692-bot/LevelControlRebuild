"""A fresh MCP server must queue music without loading analysis libraries at call time."""

import asyncio
import json
import sys
import time

from lc.agent import find_receipt
from lc.client import ROOT, Client
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_w23_live import completed, make_design, terminal


async def main():
    game = Client(port=18844)
    assert game.read("objects * --count")["count"] == 0
    make_design(game, "mcp-music")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lc.mcp_server"],
        cwd=str(ROOT),
        env={"PYTHONUTF8": "1", "LC_PORT": "18844", "PYTHONPATH": str(ROOT / "tools")},
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            start = time.perf_counter()
            result = await session.call_tool(
                "lgg_run", {"command": "play --music architect-pulse --duration 2"}
            )
            elapsed = time.perf_counter() - start
            receipt = find_receipt(result.model_dump())
            assert not result.isError and receipt["status"] == "scheduled", result
            final = terminal(game, receipt["req_id"])
            assert final["status"] == "applied"
            completed(game)
            assert game.edit("undo --tx-id " + final["tx_id"])["status"] == "applied"
            game.edit("presentation cancel")
            report = {"passed": elapsed < 2, "fresh_mcp_music_call_s": elapsed, "receipt": receipt}
            (ROOT / "tests/reports/w4-mcp-music.json").write_text(
                json.dumps(report, indent=2), "utf-8"
            )
            print(json.dumps(report, indent=2))
            assert report["passed"]


if __name__ == "__main__":
    asyncio.run(main())
