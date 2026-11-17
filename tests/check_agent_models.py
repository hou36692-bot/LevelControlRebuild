"""Check real native model parameters against a read-only mock world and render the bubble."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lc.agent_claude import ClaudeSession
from lc.agent_codex import CodexSession
from lc.agent_models import AgentModelSelection
from lc.client import ROOT
from lc.desktop import Companion
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication


class World(BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.requests.append(("GET", self.path))
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "world": "ModelSelectionProbe",
                                    "port": self.server.server_port, "protocol": 3}).encode())

    def do_POST(self):
        self.requests.append(("POST", self.path))
        self.send_error(403, "World writes are forbidden in this check")


def main() -> None:
    root = ROOT / "Intermediate/AgentModelIntegration"
    (root / "codex").mkdir(parents=True, exist_ok=True)
    output = ROOT / "docs/validation/agent-model-selection"
    output.mkdir(parents=True, exist_ok=True)
    os.environ["LC_USER_DATA_ROOT"] = str(root / "user")
    os.environ["LC_DISABLE_AUTOSTART"] = "1"
    world = ThreadingHTTPServer(("127.0.0.1", 0), World)
    threading.Thread(target=world.serve_forever, daemon=True).start()
    report: dict = {"world": "read-only mock", "codex_turns": [], "claude": {}}
    try:
        codex = CodexSession(world.server_port, workspace=root / "codex",
                             data_dir=root / "codex-data", project_id="model-probe")
        try:
            report["codex_initial"] = {
                key: value for key, value in codex.model_state().items() if key != "models"}
            report["codex_models"] = [{
                key: entry.get(key) for key in (
                    "model", "displayName", "defaultReasoningEffort", "supportedReasoningEfforts")
            } for entry in codex.models]
            first = next(entry for entry in codex.models if "luna" in entry["model"]
                         and any(mode["reasoningEffort"] == "low"
                                 for mode in entry["supportedReasoningEfforts"]))
            second = next(entry for entry in codex.models if "sol" in entry["model"]
                          and any(mode["reasoningEffort"] == "medium"
                                  for mode in entry["supportedReasoningEfforts"]))
            thread_id = codex.thread_id
            for model, effort in ((first["model"], "low"), (second["model"], "medium")):
                codex.configure_model(AgentModelSelection(model, effort))
                result = codex.agent._turn(
                    "这是模型切换验证。只回复 MODEL_SWITCH_OK，不要使用工具、修改文件或关卡。")
                assert result["status"] == "completed", result["final"]
                assert result["final"].strip() == "MODEL_SWITCH_OK", result["final"]
                assert not result["tools"] and codex.thread_id == thread_id
                assert result["model"] == model and result["effort"] == effort
                report["codex_turns"].append({key: result[key] for key in (
                    "model", "effort", "status", "elapsed_s", "final")})
                print(f"Codex: {model} / {effort}: {result['status']}", flush=True)
            report["codex_same_thread"] = True
            state = codex.model_state()
        finally:
            codex.close()

        claude = ClaudeSession(world.server_port, workspace=root / "claude",
                               data_dir=root / "claude-data", project_id="model-probe")
        try:
            report["claude"]["catalog_count"] = len(claude.models)
            report["claude"]["native_default"] = claude.model_state()["resolved_model"]
            target = next(entry for entry in claude.models if entry["model"] == "default")
            effort = target["supportedReasoningEfforts"][0]["reasoningEffort"]
            thread_id = claude.thread_id
            claude.configure_model(AgentModelSelection(target["model"], effort))
            args = claude.process.args
            assert args[args.index("--model") + 1] == target["model"]
            assert args[args.index("--effort") + 1] == effort
            assert claude.thread_id == thread_id
            report["claude"].update(model=target["model"], effort=effort,
                                    startup_verified=True, inference_tested=False)
            print(f"Claude: {target['model']} / {effort}: startup verified", flush=True)
        finally:
            claude.close()

        (output / "native-check.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts/msyh.ttc"))
        window = Companion(autostart=False)
        window.backend = SimpleNamespace(speech=None, close=lambda: None)
        window.online = True
        try:
            window.handle({"type": "ready", "harness": "codex", **state})
            window.show()
            app.processEvents()
            for theme in ("dark", "light"):
                window._apply_theme(theme, persist=False)
                app.processEvents()
                assert window.model_picker.isEnabled() and window.effort_picker.isEnabled()
                assert window.chat.height() >= window.chat.minimumHeight()
                assert window.grab().save(str(output / f"bubble-{theme}.png"))
            window.model_picker.showPopup()
            app.processEvents()
            assert window.model_picker.view().grab().save(str(output / "model-options.png"))
            window.model_picker.hidePopup()
        finally:
            window._shutdown()
        assert all(method == "GET" and path == "/health" for method, path in World.requests)
        report["world_requests"] = World.requests
        report["ui_themes"] = ["dark", "light"]
        (output / "native-check.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    finally:
        world.shutdown()
        world.server_close()


if __name__ == "__main__":
    main()
