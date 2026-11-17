import json
from types import SimpleNamespace

import pytest
from lc.agent_codex import CodexSession
from lc.agent_models import AgentModelSelection

CATALOG = [
    {"model": "native-a", "displayName": "Native A", "defaultReasoningEffort": "high",
     "supportedReasoningEfforts": [{"reasoningEffort": "high"}, {"reasoningEffort": "max"}]},
    {"model": "route/native-b[1m]", "displayName": "Native B",
     "defaultReasoningEffort": "low",
     "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}]},
]


@pytest.mark.parametrize("model,effort", [
    ("native-a", "max"), ("route/native-b[1m]", "medium"), ("native-a", None),
])
def test_selection_preserves_native_ids_and_validates_per_model(model, effort):
    metadata = AgentModelSelection(model, effort).validate(CATALOG)
    assert metadata["model"] == model


@pytest.mark.parametrize("model,effort", [
    ("missing", "high"), ("native-a", "low"), ("route/native-b[1m]", "max"), ("", None),
])
def test_invalid_choice_is_rejected(model, effort):
    with pytest.raises(ValueError):
        AgentModelSelection(model, effort).validate(CATALOG)


def test_choices_are_separate_for_each_harness_and_invalid_files_are_preserved(
        tmp_path, monkeypatch):
    monkeypatch.setenv("LC_USER_DATA_ROOT", str(tmp_path))
    assert AgentModelSelection.load("codex") is None
    codex = AgentModelSelection("native-a", "max")
    claude = AgentModelSelection("route/native-b[1m]", None)
    codex.save("codex")
    claude.save("claude")
    assert AgentModelSelection.load("codex") == codex
    assert AgentModelSelection.load("claude") == claude
    path = tmp_path / "agent-models.json"
    path.write_text('{"version":99}', "utf-8")
    assert AgentModelSelection.load("codex") is None
    assert json.loads(path.read_text("utf-8")) == {"version": 99}


def test_codex_resolves_model_default_and_keeps_the_same_thread():
    session = CodexSession.__new__(CodexSession)
    session.agent = SimpleNamespace(model="native-a", effort="max", models=CATALOG,
                                    thread_id="same-thread")
    session.configure_model(AgentModelSelection("route/native-b[1m]", None))
    assert session.thread_id == "same-thread"
    assert session.model_state() == {"model": "route/native-b[1m]",
                                    "resolved_model": "route/native-b[1m]",
                                    "effort": "low", "models": CATALOG}
    with pytest.raises(ValueError):
        session.configure_model(AgentModelSelection("route/native-b[1m]", "max"))
    assert session.agent.effort == "low"


def test_codex_default_effort_uses_native_configuration_before_catalog_suggestion():
    session = CodexSession.__new__(CodexSession)
    session.agent = SimpleNamespace(model="native-a", effort="high", models=CATALOG)
    session._configured_effort = "max"
    session.configure_model(AgentModelSelection("native-a", None))
    assert session.agent.effort == "max"
