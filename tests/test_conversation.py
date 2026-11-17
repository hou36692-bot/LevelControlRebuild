from types import SimpleNamespace

import pytest
from lc import conversation
from lc.agent_capabilities import AgentCapabilities
from lc.agent_models import AgentModelSelection
from lc.project import ProjectSettings, ProjectTarget


@pytest.mark.parametrize("harness", ["codex", "claude"])
def test_controller_selects_harness_and_binds_prompt_and_project(tmp_path, monkeypatch, harness):
    monkeypatch.setenv("LC_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(conversation.threading, "Thread", lambda **kwargs: SimpleNamespace(
        start=lambda: None, join=lambda **kwargs: None))
    captures, events = [], []

    class Session:
        model = "native-model"

        def model_state(self):
            return {"model": self.model, "resolved_model": self.model,
                    "effort": "high", "models": []}

        def __init__(self, port, **kwargs):
            captures.append((port, kwargs))

        def turn(self, prompt, **kwargs):
            captures.append((prompt, kwargs))
            backend.stopping.set()
            return {"final": "done"}

        def close(self):
            pass

    monkeypatch.setattr(conversation, "CodexSession" if harness == "codex" else "ClaudeSession",
                        Session)
    target = ProjectTarget("sample", "exe", "Sample", port=19990,
                           store=str(tmp_path / "store/entities.sqlite3"),
                           design_file=str(tmp_path / "design.json"))
    backend = conversation.ConversationController(target, ProjectSettings(), events.append,
                                                   harness=harness,
                                                   capabilities=AgentCapabilities(skills=True))
    monkeypatch.setattr(backend, "control", lambda command: None)
    attachments = (object(),)
    backend.send("放置柱子", attachments)
    backend.run()
    assert captures[0][0] == 19990
    assert captures[0][1]["project_id"] == "sample"
    assert captures[0][1]["data_dir"] == tmp_path / "store"
    assert captures[0][1]["design_file"] == tmp_path / "design.json"
    assert captures[0][1]["capabilities"] == AgentCapabilities(skills=True)
    assert captures[1][0] == "放置柱子" and captures[1][1]["images"] == attachments
    assert captures[1][1]["submitted_at"] > 0
    assert events[1]["harness"] == harness
    assert events[-1]["type"] == "turn_finished"


def test_controller_reports_missing_harness_without_ready(tmp_path, monkeypatch):
    backend = conversation.ConversationController.__new__(conversation.ConversationController)
    backend.harness = "claude"
    backend.capabilities = AgentCapabilities()
    backend.port = 18870
    backend.workspace = backend.data_dir = tmp_path
    backend.target = ProjectTarget("sample", "attach", "Sample")
    backend.design_file = None
    events = []
    backend.emit = events.append

    def missing(*args, **kwargs):
        raise FileNotFoundError("Claude Code unavailable")

    monkeypatch.setattr(conversation, "ClaudeSession", missing)
    backend.open_session()
    assert [event["type"] for event in events] == ["connecting", "connection_error"]
    assert backend.agent is None


@pytest.mark.parametrize("harness", ["codex", "claude"])
def test_model_change_is_serialized_before_prompt_and_persisted(tmp_path, monkeypatch, harness):
    monkeypatch.setenv("LC_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(conversation.threading, "Thread", lambda **kwargs: SimpleNamespace(
        start=lambda: None, join=lambda **kwargs: None))
    created, turns, events = [], [], []
    models = [{"model": name, "supportedReasoningEfforts": [{"reasoningEffort": "high"}]}
              for name in ("native", "chosen")]

    class Session:
        model, effort = "native", None

        def __init__(self, *args, **kwargs):
            created.append(self)

        def model_state(self):
            return {"model": self.model, "resolved_model": self.model,
                    "effort": self.effort, "models": models}

        def configure_model(self, selection):
            selection.validate(models)
            self.model, self.effort = selection.model, selection.effort

        def turn(self, text, **kwargs):
            turns.append((text, self.model, self.effort))
            backend.stopping.set()
            return {"final": "done"}

        def close(self):
            pass

    monkeypatch.setattr(conversation, "CodexSession" if harness == "codex" else "ClaudeSession",
                        Session)
    backend = conversation.ConversationController(
        ProjectTarget("sample", "attach", "Sample", port=19990), ProjectSettings(),
        events.append, harness=harness)
    monkeypatch.setattr(backend, "control", lambda command: None)
    backend.select_model("chosen", "high")
    backend.send("继续当前对话")
    backend.run()
    assert len(created) == 1 and turns == [("继续当前对话", "chosen", "high")]
    assert [event["type"] for event in events] == [
        "connecting", "ready", "model_configuring", "model_changed", "turn_started",
        "turn_finished"]
    assert AgentModelSelection.load(harness) == AgentModelSelection("chosen", "high")
    backend.stopping.clear()
    backend.open_session()
    assert backend.agent.model == "chosen" and events[-1]["effort"] == "high"
    before = (tmp_path / "agent-models.json").read_bytes()
    backend.configure_model(AgentModelSelection("chosen", "unsupported"))
    assert events[-1]["type"] == "model_error"
    assert events[-1]["model"] == "chosen" and events[-1]["effort"] == "high"
    assert (tmp_path / "agent-models.json").read_bytes() == before
    AgentModelSelection("removed-model", "high").save(harness)
    before = (tmp_path / "agent-models.json").read_bytes()
    backend.agent.close()
    backend.open_session()
    assert backend.agent.model == "native"
    assert events[-2]["type"] == "notice" and events[-1]["type"] == "ready"
    assert (tmp_path / "agent-models.json").read_bytes() == before
