import time

from lc import turn_context


class Game:
    def read(self, command):
        return {"version": 42} if command.startswith("objects") else {
            "feet_cm": [0, 0, 0], "forward": [1, 0, 0]}


def test_queue_context_and_build_ready_are_distinct_clocks():
    metrics = turn_context.prepare(Game(), time.perf_counter() - 2)
    assert metrics["queue_s"] >= 2
    assert "world_version" in turn_context.model_input("build", metrics)
    assert turn_context.build_event({"status": "scheduled"}, metrics) is None
    assert turn_context.build_event({"status": "applied", "presentation_ready": False},
                                    metrics) is None
    receipt = {"status": "applied", "presentation_ready": True,
               "scene": "test", "entity_count": 100}
    event = turn_context.build_event(receipt, metrics)
    assert event["seconds"] >= 2
    assert turn_context.summary(metrics)["builds"] == [event]
    assert "context" not in turn_context.summary(metrics)


def test_build_ready_is_emitted_before_model_final_text_in_api():
    import json

    from test_api_agent import agent_fixture

    agent, events = agent_fixture()
    agent.turn_metrics = turn_context.prepare(Game())
    agent.names["lgg_build"] = "lgg_build"
    responses = iter([
        {"output": [{"type": "function_call", "name": "lgg_build", "call_id": "build",
                     "arguments": json.dumps({"plan": {}, "request_id": "test"})}]},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]},
    ])
    agent.stream = lambda body: next(responses)
    agent.invoke_tool = lambda name, args: {"structuredContent": {
        "req_id": "test", "tx_id": "tx-test", "status": "applied", "scene": "test",
        "presentation_ready": True, "entity_count": 100}}
    result = agent._turn("build")
    assert result["status"] == "completed"
    kinds = [event["type"] for event in events]
    assert kinds.index("build_ready") < kinds.index("model_result")
    assert result["timings"]["builds"][0]["entity_count"] == 100


def test_background_receipt_updates_original_turn_only(monkeypatch):
    import threading
    from types import SimpleNamespace

    from lc import conversation

    events = []
    metrics = turn_context.prepare(Game())
    backend = conversation.ConversationController.__new__(
        conversation.ConversationController)
    backend.pending_builds = {}
    backend.pending_lock = threading.Lock()
    backend.agent = SimpleNamespace(turn_metrics=metrics)
    backend.emit = events.append
    receipt = {"req_id": "test", "status": "scheduled", "scene": "test", "entity_count": 10}
    backend.on_agent_event(turn_context.build_event(receipt, metrics))
    later = turn_context.prepare(Game())
    backend.agent.turn_metrics = later
    ready = {**receipt, "status": "applied", "presentation_ready": True}
    monkeypatch.setattr(conversation, "BuildService", lambda client: SimpleNamespace(
        finalize_receipt=lambda request, result: ready))
    backend.observe_builds(SimpleNamespace(receipt=lambda request: {"status": "applied"}))
    assert events[-1]["type"] == "build_ready"
    assert metrics["builds"] and not later["builds"]
    assert not backend.pending_builds
