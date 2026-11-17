import asyncio
import json
import sys

import pytest
from lc.client import ROOT, Client
from lc.command import length, parse
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.parametrize("text,expected", [("5m", 500), ("2.5cm", 2.5), ("-1m", -100), ("0", 0)])
def test_units(text: str, expected: float) -> None:
    assert length(text) == expected


@pytest.mark.parametrize("text", ["5", "NaNm", "infcm", "1e309m", "1km"])
def test_bad_units(text: str) -> None:
    with pytest.raises(ValueError):
        length(text)


@pytest.mark.parametrize(
    "tool,command",
    [
        ("edit", "apply create bad* --size 1m,1m,5m"),
        ("edit", "apply create a --size 1m,1m,-5m"),
        ("edit", "apply create a --front 4m --at 0,0,0"),
        ("edit", "apply set a"),
        ("edit", "select a,,b"),
        ("edit", "undo"),
        ("edit", "shatter *"),
        ("read", "objects * --limit 0"),
        ("read", "objects * --fields actor"),
        ("read", "objects *\n--count"),
        ("capture", "../photo.png"),
        ("help", "../../AGENTS.md"),
        ("connect", "--port 65536"),
    ],
)
def test_invalid_commands(tool: str, command: str) -> None:
    with pytest.raises(ValueError):
        parse(tool, command)


def test_selectors_and_fields() -> None:
    command = parse("read", "objects column-*,wall-east transform,bounds --nearest")
    assert command.data["selector"] == "column-*,wall-east"
    assert command.data["fields"] == "transform,bounds"
    assert command.data["nearest"] == "true"
    assert parse("read", "objects --count").data["count"] == "true"


def test_front_conversion_occurs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Client(port=18844)
    posted = []

    def request(path: str, data: dict | None = None) -> dict:
        if path == "/v1/player":
            return {"feet_cm": [100, 200, 5], "forward": [0, 1, 0]}
        posted.append(data)
        return {"status": "applied", "req_id": data["req_id"]}

    monkeypatch.setattr(client, "_request", request)
    client.edit(
        "apply create column --template blockout/column --size 1m,1m,5m --front 4m",
        request_id="same-request",
    )
    entity = posted[0]["operations"][0]["entity"]
    assert entity["transform"]["size_cm"] == [100, 100, 500]
    assert entity["transform"]["position_cm"] == [100, 600, 255]
    assert posted[0]["req_id"] == "same-request"


def test_poll_preserves_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Client(port=18844)
    paths = []

    def request(path: str, data: dict | None = None) -> dict:
        paths.append(path)
        return {"status": "scheduled" if data else "applied", "req_id": "durable-id"}

    monkeypatch.setattr(client, "_request", request)
    result = client.edit("undo --last", request_id="durable-id")
    assert result["status"] == "applied"
    assert paths == ["/v1/undo", "/v1/receipt?req_id=durable-id"]


def test_batch_cannot_escape_repository(tmp_path) -> None:
    path = tmp_path / "outside.json"
    path.write_text("[]")
    with pytest.raises(ValueError, match="inside this workspace"):
        Client(port=18844).edit(f'apply --path "{path.as_posix()}"')


def test_help_is_read_from_docs() -> None:
    assert Client(port=18844).help("lc_edit") == (ROOT / "docs/lc_help/edit.md").read_text("utf-8")


def test_mcp_stdio_lists_tools_and_reads_help() -> None:
    async def check() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "lc.mcp_server"],
            cwd=str(ROOT),
            env={"PYTHONUTF8": "1"},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.list_tools()
                assert {tool.name for tool in result.tools} == {
                    "lc.connect",
                    "lc.help",
                    "lc.read",
                    "lc.edit",
                    "lc.query",
                    "lc.changes",
                    "lc.annotate",
                    "lc.capture",
                    "lc.context",
                    "lc.receipt",
                    "lgg_card",
                    "lgg_draft",
                    "lgg_run",
                    "lgg_build",
                    "lgg_plan",
                    "lgg_module",
                    "lgg_knowledge",
                }
                help_result = await session.call_tool("lc.help", {"command": "lc_edit"})
                assert not help_result.isError
                assert "undo --last" in json.dumps(help_result.model_dump())

    asyncio.run(check())
