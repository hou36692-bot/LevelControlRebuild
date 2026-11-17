"""Manual offscreen screenshots of the companion states (not a pytest test)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("LC_DISABLE_AUTOSTART", "1")
os.environ.setdefault("LC_DISABLE_ANIMATIONS", "1")
os.environ.setdefault("LC_DISABLE_IDLE_ANIMATION", "1")
_CAPTURE = Path(tempfile.gettempdir()) / "lc-capture-ui"
os.environ.setdefault("LC_UI_SETTINGS", str(_CAPTURE / "ui.json"))
os.environ.setdefault("LC_PROJECT_SETTINGS", str(_CAPTURE / "project.json"))
os.environ.setdefault("LC_AVATAR_SETTINGS", str(_CAPTURE / "avatar.json"))
os.environ.setdefault("LC_AVATAR_ROOT", str(_CAPTURE / "avatars"))
os.environ.setdefault("LC_MODEL_SETTINGS", str(_CAPTURE / "model.json"))

from lc.desktop import Companion  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def main() -> None:
    app = QApplication.instance() or QApplication([])
    out = Path(__file__).with_name("reports") / "ui"
    out.mkdir(parents=True, exist_ok=True)
    window = Companion(autostart=False)
    window.handle({"type": "ready", "provider": "inherited", "model": "gpt-6-astra",
                   "effort": "low", "models": []})
    window.online = True
    window.handle({"type": "world", "state": {"state": "completed"}})
    window.chat.add_user("做一个三面围合、中间有喷泉的庭院")
    window.chat.begin_assistant()
    window.chat.append_assistant("先确认场地，再发布草稿。")
    window.chat.finish_assistant("已提交庭院白盒，工具轨迹见下。")
    window.tool_trace.show()
    window.tool_trace.set_tools([{"id": "1", "name": "lc.edit", "phase": "finished",
                                  "seconds": 0.07, "arguments": {"command": "apply create ..."}}])
    window.show()
    app.processEvents()
    window.grab().save(str(out / "expanded-dark.png"))
    window.set_collapsed(True, animate=False)
    app.processEvents()
    window.grab().save(str(out / "collapsed-dark.png"))
    window.set_collapsed(False, animate=False)
    window.toggle_theme()
    app.processEvents()
    window.grab().save(str(out / "expanded-light.png"))
    window.close()
    app.processEvents()
    print(out)


if __name__ == "__main__":
    main()
