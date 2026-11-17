import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lc import desktop
from lc.agent_capabilities import AgentCapabilities
from lc.desktop import Companion, label, reserve_instance
from lc.project import ProjectTarget
from lc.settings_dialog import SettingsDialog
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


def _window(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_USER_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("LC_PROJECT_SETTINGS", str(tmp_path / "project-settings.json"))
    monkeypatch.setenv("LC_DISABLE_AUTOSTART", "1")
    return Companion(autostart=False)


def test_ui_text_is_plain_and_duplicate_instances_are_rejected():
    app = QApplication.instance() or QApplication([])
    text = label('<a href="https://invalid.example">payload</a>')
    assert text.textFormat() == Qt.TextFormat.PlainText
    first = reserve_instance(39844)
    assert first is not None
    try:
        assert reserve_instance(39844) is None
        app.processEvents()
    finally:
        first.close()


def test_bubble_shell_has_one_settings_entry_and_native_model_selectors(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, monkeypatch)
    try:
        assert window.chat is not None
        assert window.references is not None
        assert window.agent_chip.text() == "Codex"
        assert window.settings_button is not None
        assert not hasattr(window, "provider_picker")
        assert window.model_picker.accessibleName() == "模型"
        assert window.effort_picker.accessibleName() == "思考强度"
        assert not window.model_picker.isEditable()
        assert not window.model_picker.isEnabled()
        assert not window.effort_picker.isEnabled()
        window.handle({"type": "ready", "new_session": True})
        assert "已新建" in window.chat.text()
        window.handle({"type": "turn_started", "prompt": "你好"})
        window.handle({"type": "reply_delta", "text": "世界已读取。"})
        window.handle({"type": "turn_finished",
                       "result": {"final": "世界已读取。"}})
        assert "世界已读取。" in window.chat.text()
        window.handle({"type": "build_ready", "entity_count": 3, "seconds": 1.2})
        assert "构建完成" in window.partial.text()
        window.handle({"type": "world", "state": {
            "state": "previewing", "instant": False, "revision": 42}})
        assert not window.skip_presentation.isHidden()
        window.handle({"type": "world", "state": {
            "state": "completed", "instant": False}})
        assert window.skip_presentation.isHidden()
        app.processEvents()
    finally:
        window._shutdown()


def test_close_hides_to_tray_and_explicit_exit_stops_backend(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, monkeypatch)

    class Backend:
        speech = None
        voice_lease = "test"

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

        def cancel(self):
            pass

        def control(self, command):
            pass

    class Tray:
        visible = True

        def isVisible(self):
            return self.visible

        def showMessage(self, *args):
            pass

        def hide(self):
            self.visible = False

    backend = Backend()
    window.backend = backend
    window.tray = Tray()
    window.show()
    app.processEvents()
    window.close()
    assert not window.isVisible() and not backend.closed
    window.show_companion()
    assert window.isVisible()
    window.exit_client()
    assert backend.closed and not window.tray.isVisible()


def test_harness_and_project_switch_ignore_stale_events(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, monkeypatch)
    created = []

    class Backend:
        def __init__(self, target, settings, emit, *, harness, capabilities):
            self.port, self.harness, self.emit = target.port, harness, emit
            self.capabilities = capabilities
            self.closed = False
            created.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(desktop, "ConversationController", Backend)
    monkeypatch.setattr(desktop.threading, "Thread", lambda *, target, **kwargs:
                        SimpleNamespace(start=target))
    try:
        first = ProjectTarget("first", "attach", "First", port=18870)
        window.active_target = first
        window._ensure_backend(first)
        generation = window.backend_generation
        window.change_harness("claude")
        assert created[0].closed and created[-1].harness == "claude"
        assert window.ui_settings.harness == "claude"
        window.handle({"type": "ready", "harness": "codex"}, generation)
        assert not window.ready and window.agent_chip.text() == "Claude Code"
        second = ProjectTarget("second", "attach", "Second", port=18871)
        window._ensure_backend(second)
        assert created[1].closed and created[-1].port == 18871
        window.active_target = second
        policy = AgentCapabilities(skills=True)
        window.change_capabilities(policy)
        assert created[2].closed and created[-1].capabilities == policy
        app.processEvents()
    finally:
        window._shutdown()


def test_settings_enable_both_harnesses(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_USER_DATA_ROOT", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(workspace=tmp_path, data_dir=tmp_path, project_id="test",
                            port=18870, theme="dark", harness="claude")
    changed = []
    dialog.harness_changed.connect(changed.append)
    assert dialog.harness.currentData() == "claude"
    assert dialog.harness.model().item(1).isEnabled()
    dialog.harness.setCurrentIndex(0)
    assert changed == ["codex"]
    assert dialog.tabs.count() == 4
    assert len(dialog.capability_checks) == 4
    assert not any(check.isChecked() for check in dialog.capability_checks.values())
    policies = []
    dialog.capabilities_changed.connect(policies.append)
    dialog.capability_checks["skills"].setChecked(True)
    assert not (tmp_path / "agent-capabilities.json").exists()
    dialog._apply_capabilities()
    assert policies == [AgentCapabilities(skills=True)]
    assert AgentCapabilities.load() == policies[0]
    dialog._apply_capabilities()
    assert len(policies) == 1
    dialog.close()
    app.processEvents()


def test_voice_transcript_uses_original_bridge_and_keeps_images(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, monkeypatch)
    sent, callbacks = [], []
    window.ready = window.online = True
    original = desktop.Bridge()
    window.bridge = original
    original.event.connect(lambda event: window.handle(event, 0))
    backend = SimpleNamespace(
        recording=False, voice_lease="voice", control=lambda command: None,
        speech=SimpleNamespace(start=lambda partial, complete: callbacks.append(complete)),
        send=lambda text, images: sent.append((text, images)), close=lambda: None)
    window.backend = backend
    try:
        window.start_recording(True)
        assert backend.recording and callbacks
        callbacks[0]({"text": "放一根柱子", "finalization_s": 0.1})
        assert sent == [("放一根柱子", ())]
        window.backend_generation = 1
        window.bridge = desktop.Bridge()
        callbacks[0]({"text": "旧转写", "finalization_s": 0.1})
        assert len(sent) == 1
        app.processEvents()
    finally:
        window._shutdown()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_model_controls_map_backend_options_and_wait_for_confirmation(tmp_path, monkeypatch, theme):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path, monkeypatch)
    selections = []
    models = [
        {"model": "agent-a", "displayName": "Agent A", "defaultReasoningEffort": "max",
         "supportedReasoningEfforts": [{"reasoningEffort": "max"}]},
        {"model": "agent-b", "displayName": "Agent B", "defaultReasoningEffort": "low",
         "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]},
    ]
    window.backend = SimpleNamespace(
        speech=None, close=lambda: None,
        select_model=lambda model, effort: selections.append((model, effort)))
    window.online = True
    window._apply_theme(theme, persist=False)
    try:
        window.handle({"type": "ready", "harness": "codex", "model": "agent-a",
                       "resolved_model": "actual-agent-a", "effort": "max", "models": models})
        assert window.model_picker.currentData() == "agent-a"
        assert "actual-agent-a" in window.model_picker.toolTip()
        assert window.effort_picker.currentData() == "max"
        assert window.model_picker.isEnabled() and window.effort_picker.isEnabled()
        window.chat.add_user("保留这条对话")
        window.model_picker.setCurrentIndex(window.model_picker.findData("agent-b"))
        window.model_changed()
        assert selections == [("agent-b", "low")]
        assert window.effort_picker.findData("max") == -1
        assert window.model_pending and not window.model_picker.isEnabled()
        assert not window.send.isEnabled() and not window.submit("不能抢先发送")
        window.handle({"type": "model_changed", "harness": "codex", "model": "agent-b",
                       "effort": "low", "models": models})
        assert not window.model_pending and "保留这条对话" in window.chat.text()
        window.effort_picker.setCurrentIndex(window.effort_picker.findData("high"))
        window.effort_changed()
        assert selections[-1] == ("agent-b", "high")
        window.handle({"type": "model_error", "model": "agent-b", "effort": "low",
                       "models": models, "message": "后端拒绝了切换"})
        assert window.effort_picker.currentData() == "low" and not window.model_pending
        assert "后端拒绝了切换" in window.chat.text()
        window.handle({"type": "turn_started", "prompt": "继续"})
        assert not window.model_picker.isEnabled() and not window.effort_picker.isEnabled()
        window.handle({"type": "turn_finished", "result": {"final": "完成"}})
        assert window.model_picker.isEnabled()
        window.recording = True
        window.update_status()
        assert not window.effort_picker.isEnabled()
        window.handle({"type": "model_changed", "model": "stale", "models": []},
                      window.backend_generation - 1)
        assert window.model_picker.currentData() == "agent-b"
        app.processEvents()
    finally:
        window._shutdown()
