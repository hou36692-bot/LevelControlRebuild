"""Actual collision, action and audio-clock behavior in a disposable game."""

import json
import os
import time

import pytest
from lc.client import ROOT, Client
from test_w23_live import completed, make_design, terminal

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("LC_LIVE") != "1", reason="Set LC_LIVE=1"),
]


def client() -> Client:
    result = Client(port=int(os.getenv("LC_PORT", "18846")))
    assert result.read("objects * --count")["count"] == 0, "Use a disposable empty world"
    return result


def test_actions_pause_stop_and_checkpoint():
    game = client()
    created = game.edit(
        "apply create action-column --template blockout/column --at 10m,0m,2.5m --size 1m,1m,5m"
    )
    assert created["status"] == "applied"
    for command, expected in [
        ("spin --degrees 90 --duration 0.3", ("rotation_deg", 1, 90)),
        ("move --delta 1m,0m,0m --duration 0.3", ("position_cm", 0, 1100)),
        ("scale --factor 1.2 --duration 0.3", ("size_cm", 2, 600)),
    ]:
        request = game.edit("act action-column " + command)
        assert request["status"] == "scheduled", request
        assert terminal(game, request["req_id"])["status"] == "applied"
        entity = game.read("objects action-column transform")["entities"][0]
        assert entity["transform"][expected[0]][expected[1]] == pytest.approx(expected[2])
        assert game.edit("undo --last")["status"] == "applied"
    request = game.edit("act action-column oscillate --amplitude 0m,0m,0.2m --duration 2")
    time.sleep(0.15)
    assert game.edit("action pause")["status"] == "applied"
    before = game.read("presentation")["action_elapsed_s"]
    time.sleep(0.1)
    assert game.read("presentation")["action_elapsed_s"] == before
    assert game.edit("action resume")["status"] == "applied"
    assert game.edit("action stop")["status"] == "applied"
    stopped = terminal(game, request["req_id"])
    assert stopped["status"] == "failed", stopped
    assert stopped["checkpoint"] in {c["name"] for c in game.read("checkpoints")["checkpoints"]}
    assert game.read("consistency")["ok"]
    assert game.read("objects action-column transform")["entities"][0]["transform"][
        "position_cm"
    ] == [1000, 0, 250]
    invalid = game._write(
        "/v1/actions",
        {"selector": "action-column", "kind": "scale", "factor": -1, "duration_s": 1},
        None,
        wait=False,
    )
    assert invalid["status"] == "rejected"
    assert game.edit("undo --tx-id " + created["tx_id"])["status"] == "applied"
    assert game.read("consistency")["memory_count"] == 0


def test_jump_landing_clearance_and_walk_shortcut():
    game = client()

    def platform(name, x, width):
        return {
            "op": "create",
            "entity": {
                "id": name,
                "template": "blockout/box",
                "tags": ["walkable"],
                "transform": {"position_cm": [x, 0, 200], "size_cm": [width, 400, 50]},
            },
        }

    assert (
        game.apply([platform("platform-a", 1000, 400), platform("platform-b", 1600, 400)])["status"]
        == "applied"
    )
    player = game.read("player-status")
    assert player["jump_velocity_cm_s"] == 500 and player["gravity_cm_s2"] < 0
    jump = game.read("jump-check --from 11.5m,0m,2.25m --heading 0 --speed 5m")
    assert jump["ok"] and jump["landed"] and jump["entity_id"] == "platform-b", jump
    assert 480 <= jump["horizontal_distance_cm"] <= 540
    blocked = game.read("clearance --from 10m,0m,1m --to 16m,0m,1m")
    assert blocked["blocked"]
    disconnected = game.read("walk-graph tag:walkable --from-id platform-a --to-id platform-b")
    assert disconnected["reachable"] is False, disconnected
    assert game.apply([platform("platform-bridge", 1300, 200)])["status"] == "applied"
    connected = game.read("walk-graph tag:walkable --from-id platform-a --to-id platform-b")
    assert connected["reachable"] and connected["path"] == [
        "platform-a",
        "platform-bridge",
        "platform-b",
    ], connected
    assert game.edit("player teleport --feet 10m,0m,1m")["status"] == "rejected"
    assert game.edit("player configure --speed 100m")["status"] == "rejected"
    assert (
        game.edit("player configure --speed 6m --jump-velocity 5m --gravity-scale 1")["status"]
        == "applied"
    )
    assert game.read("player-status")["max_walk_speed_cm_s"] == 600
    assert game.edit("player configure --speed 5m")["status"] == "applied"
    assert game.edit("apply delete platform-*")["status"] == "applied"
    assert game.read("consistency")["memory_count"] == 0
    (ROOT / "tests/reports/w4-checks.json").write_text(
        json.dumps(
            {
                "jump": jump,
                "blocked": blocked,
                "before_bridge": disconnected,
                "after_bridge": connected,
            },
            indent=2,
        ),
        "utf-8",
    )


def test_music_clock_and_independent_hold_leases():
    game = client()
    service = make_design(game, "music-court")
    assert game.edit("presentation hold --lease-id voice-test")["status"] == "applied"
    assert game.edit("presentation hold")["status"] == "applied"
    assert game.edit("presentation resume")["status"] == "applied"
    assert game.read("presentation")["commit_held"] is True
    assert game.edit("presentation resume --lease-id voice-test")["status"] == "applied"
    queued = service.invoke("lgg_run", "play --duration 4 --music architect-pulse")
    assert queued["status"] == "scheduled", queued
    receipt = terminal(game, queued["req_id"])
    assert receipt["status"] == "applied", receipt
    state = completed(game)
    assert state["music"] == "architect-pulse" and state["audio_clock_started_s"] > 0, state
    assert state["max_beat_error_ms"] <= 80, state
    report = {"receipt": receipt, "presentation": state, "events": game.read("events --since 0")}
    (ROOT / "tests/reports/w4-music.json").write_text(json.dumps(report, indent=2), "utf-8")
    assert game.edit("undo --tx-id " + receipt["tx_id"])["status"] == "applied"
    game.edit("presentation cancel")
