import importlib.util

import pytest
from lc.agent import arguments, find_receipt
from lc.client import ROOT


def test_receipt_extraction_from_real_mcp_shape() -> None:
    receipt = {"req_id": "abc", "tx_id": "tx-abc", "status": "applied"}
    assert find_receipt({"content": [], "structuredContent": receipt}) == receipt
    assert (
        find_receipt(
            {
                "content": [
                    {"type": "text", "text": '{"req_id":"abc","tx_id":"tx-abc","status":"applied"}'}
                ]
            }
        )
        == receipt
    )
    assert find_receipt({"error": {"message": "approval required"}}) is None


def test_runtime_agent_disables_shell_and_other_mcp() -> None:
    values = arguments(18844, True)
    assert "features.shell_tool=false" in values
    assert "mcp_servers.node_repl.enabled=false" in values
    assert 'mcp_servers.lc.default_tools_approval_mode="approve"' in values
    assert "--ephemeral" in values
    with pytest.raises(ValueError):
        arguments(70000, True)


def test_embedded_client_uses_bundled_python_for_mcp(tmp_path, monkeypatch):
    from lc import agent

    runtime = tmp_path / "Runtime/Python"
    runtime.mkdir(parents=True)
    python = runtime / "python.exe"
    python.touch()
    monkeypatch.setattr(agent.sys, "prefix", str(runtime))
    monkeypatch.setattr(agent.sys, "executable", str(tmp_path / "LevelControl.exe"))
    assert agent.python_executable() == str(python)


def test_latency_percentiles() -> None:
    specification = importlib.util.spec_from_file_location(
        "latency", ROOT / "tests/latency/run_w1.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    assert module.percentile([4, 1, 3, 2], 0.5) == 2.5
    assert module.percentile(list(range(1, 21)), 0.9) == pytest.approx(18.1)


def test_new_message_holds_pending_commit_and_resumes_on_error() -> None:
    from lc.resident import ResidentAgent

    class Game:
        commands = []

        def read(self, command):
            return {"commit_waiting": True}

        def edit(self, command):
            self.commands.append(command)
            return {"status": "applied"}

    agent = ResidentAgent.__new__(ResidentAgent)
    agent.client = Game()

    def fail(prompt):
        raise RuntimeError("model disconnected")

    agent._turn = fail
    with pytest.raises(RuntimeError, match="disconnected"):
        agent.turn("喷泉小一点")
    assert agent.client.commands == ["presentation hold", "presentation resume"]


def test_simple_task_finishes_only_after_matching_real_receipts():
    from lc.resident import completed_column_undo

    create = {
        "tool": "lc.edit",
        "arguments": {
            "command": "apply create front-column --template blockout/column "
            "--size 1m,1m,5m --front 4m"
        },
        "receipt": {
            "status": "applied",
            "tx_id": "tx-a",
            "entity_ids": ["front-column"],
            "applied": 1,
            "version": 1,
        },
    }
    undo = {
        "tool": "lc.edit",
        "arguments": {"command": "undo --tx-id tx-a"},
        "receipt": {
            "status": "applied",
            "tx_id": "tx-b",
            "entity_ids": ["front-column"],
            "applied": 1,
            "version": 2,
        },
    }
    prompt = "在我面前放一根 5 米高的柱子，然后撤销"
    assert not completed_column_undo(prompt, [create])
    assert completed_column_undo(prompt, [create, undo])
    assert not completed_column_undo(prompt + "，再造一堵墙", [create, undo])
    undo["receipt"]["status"] = "rejected"
    assert not completed_column_undo(prompt, [create, undo])
    undo["receipt"]["status"] = "applied"
    undo["receipt"]["entity_ids"] = ["another-column"]
    assert not completed_column_undo(prompt, [create, undo])
