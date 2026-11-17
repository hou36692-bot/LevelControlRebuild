"""Transparent desktop companion for the runtime agent and local speech."""

from __future__ import annotations

import argparse
import copy
import ctypes
import json
import os
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCloseEvent, QIcon, QKeyEvent, QMouseEvent
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedLayout,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .agent_capabilities import AgentCapabilities
from .avatar import AvatarSettings, AvatarView
from .avatar_panel import AvatarPanel
from .chat import ChatView
from .controller import ProjectController
from .conversation import ConversationController, workspace_for
from .image_input import ImageLineEdit, ReferenceImages
from .images import validate_images
from .models import EFFORTS
from .project import ProjectError, ProjectSettings, ProjectTarget, design_for, store_for
from .project_panel import ProjectPanel
from .settings_dialog import SettingsDialog
from .settings_migration import migrate
from .theme import colors, stylesheet
from .ui_settings import UiSettings, clamp_point, screen_rects
from .widgets import PillButton, ProjectChip, StatusPill, ToolTrace, label

PANEL_WIDTH = 464
PANEL_HEIGHT = 820
PILL_SIZE = QSize(224, 52)


class Bridge(QObject):
    event = Signal(dict)


class Companion(QWidget):
    def __init__(
        self, port: int | None = None, prompt: str | None = None,
        screenshot: Path | None = None, *, autostart: bool = True,
        project_settings: ProjectSettings | None = None,
        ui_settings: UiSettings | None = None,
        avatar_settings: AvatarSettings | None = None,
        controller: ProjectController | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("LevelControl · 关卡助手")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.ui_settings = ui_settings or UiSettings()
        self.capabilities = AgentCapabilities.load()
        self.theme_name = self.ui_settings.theme
        self.project_settings = project_settings or ProjectSettings()
        self.avatar_settings = avatar_settings or AvatarSettings()
        self.busy = False
        self.ready = False
        self.online = False
        self.connection_failed = False
        self.model_pending = False
        self.model_catalog: list[dict[str, Any]] = []
        self.recording = False
        self.recording_keyboard = False
        self.passthrough = False
        self.world: dict[str, Any] = {}
        self.tools: dict[str, dict[str, Any]] = {}
        self.reply = ""
        self.f8 = False
        self.f9 = False
        self.auto_prompt = prompt
        self.screenshot = screenshot
        self.last_result: dict[str, Any] | None = None
        self.quitting = False
        self.tray_notice = False
        self.tray: QSystemTrayIcon | None = None
        self.collapsed = False
        self._animating = False
        self._anim: QPropertyAnimation | None = None
        self._panel_pos = QPoint()
        self._drag = QPoint()
        self.backend: ConversationController | None = None
        self.bridge: Bridge | None = None
        self.backend_generation = 0
        self.backend_target = ""
        self.active_target: ProjectTarget | None = None
        self.world_name = ""
        self.port = port or 0
        self._disable_autostart = os.environ.get("LC_DISABLE_AUTOSTART") == "1"
        self.controller = controller or ProjectController(self.project_settings.bundle, parent=self)
        self._panel_size = self._preferred_panel_size()
        self._build_ui()
        self._apply_theme(self.theme_name, persist=False)
        self._load_avatar()
        self.controller.status.connect(self._project_status)
        self.controller.connected.connect(self._project_connected)
        self.controller.failed.connect(self._project_failed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.keys)
        self.timer.start(20)
        icon = QIcon(str(Path(__file__).with_name("assets") / "levelcontrol.svg"))
        self.setWindowIcon(icon)
        self._setup_tray(icon)
        QApplication.instance().aboutToQuit.connect(self._shutdown)
        if port is not None:
            self.active_target = ProjectTarget(
                f"attach:{port}", "attach", "已连接游戏", port=port,
                protocol=int(os.environ.get("LC_PROTOCOL", 3)))
            self.port = port
            self._update_project_chip()
            if autostart and not self._disable_autostart:
                self._activate_target(self.active_target, launch=False)
        else:
            self.active_target = self.project_settings.active_target()
            if self.active_target is not None:
                self.port = self.active_target.port
                self._update_project_chip()
                if autostart and not self._disable_autostart:
                    self._activate_target(self.active_target, launch=False)
            else:
                self._set_status("未配置 UE 项目 · 点击项目", "offline")
        self._restore_position()

    # ---------------------------------------------------------------- UI
    def _preferred_panel_size(self) -> QSize:
        screen = QApplication.primaryScreen()
        rect = screen.availableGeometry() if screen else QRect(0, 0, 800, 800)
        height = PANEL_HEIGHT
        if rect.height() < 900:
            height = 700
        if rect.height() < 760:
            height = 640
        return QSize(min(PANEL_WIDTH, rect.width() - 24), min(height, rect.height() - 24))

    def _build_ui(self) -> None:
        self.panel_page = QWidget()
        outer = QVBoxLayout(self.panel_page)
        outer.setContentsMargins(10, 10, 10, 8)
        outer.setSpacing(6)
        self.card = QFrame()
        self.card.setObjectName("card")
        panel = QVBoxLayout(self.card)
        panel.setContentsMargins(18, 16, 18, 16)
        panel.setSpacing(8)

        header = QHBoxLayout()
        brand = label("LEVEL CONTROL", "brand")
        brand.setWordWrap(False)
        header.addWidget(brand)
        header.addStretch()
        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("iconButton")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(self.open_agent_settings)
        self.fold = QPushButton("收起")
        self.fold.setObjectName("iconButton")
        self.fold.clicked.connect(self.toggle_fold)
        close = QPushButton("×")
        close.setObjectName("iconButton")
        close.clicked.connect(self.close)
        header.addWidget(self.settings_button)
        header.addWidget(self.fold)
        header.addWidget(close)
        panel.addLayout(header)

        self.status_pill = StatusPill(self.theme_name)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(2, 0, 2, 0)
        status_row.addWidget(self.status_pill, 1)
        self.world_label = label("", "caption")
        self.world_label.setWordWrap(False)
        status_row.addWidget(self.world_label)
        panel.addLayout(status_row)

        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        self.project_chip = ProjectChip()
        self.project_chip.clicked.connect(self.open_project_settings)
        self.agent_chip = QPushButton(
            "Claude Code" if self.ui_settings.harness == "claude" else "Codex")
        self.agent_chip.setObjectName("chip")
        self.agent_chip.setMaximumWidth(120)
        self.agent_chip.setToolTip("本机对话 Agent；模型与思考强度在下方选择。")
        self.agent_chip.clicked.connect(self.open_agent_settings)
        chips.addWidget(self.project_chip)
        chips.addStretch()
        chips.addWidget(self.agent_chip)
        panel.addLayout(chips)

        model_row = QHBoxLayout()
        self.model_picker = QComboBox()
        self.model_picker.setAccessibleName("模型")
        self.model_picker.setMinimumWidth(180)
        self.model_picker.addItem("读取 Agent 模型…", "")
        self.model_picker.setEnabled(False)
        self.model_picker.activated.connect(self.model_changed)
        self.effort_picker = QComboBox()
        self.effort_picker.setAccessibleName("思考强度")
        self.effort_picker.setMinimumWidth(105)
        self.effort_picker.addItem("Agent 默认", None)
        self.effort_picker.setEnabled(False)
        self.effort_picker.activated.connect(self.effort_changed)
        self.effort_picker.setToolTip("当前模型返回的思考档位；下一条消息生效。")
        model_row.addWidget(self.model_picker, 1)
        model_row.addWidget(self.effort_picker)
        panel.addLayout(model_row)

        self.body = QWidget()
        content = QVBoxLayout(self.body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(6)
        self.chat = ChatView()
        self.chat.add_notice("可以创建、修改、撤销，也可以边说边搭。")
        content.addWidget(self.chat, 1)
        self.tool_trace = ToolTrace()
        self.tool_trace.hide()
        content.addWidget(self.tool_trace)
        self.partial = label("", "partial")
        content.addWidget(self.partial)
        self.reconnect = QPushButton("重新连接")
        self.reconnect.clicked.connect(self.retry_connection)
        self.reconnect.hide()
        content.addWidget(self.reconnect)
        self.references = ReferenceImages(self)
        content.addWidget(self.references)
        self.references.changed.connect(self.update_status)
        row = QHBoxLayout()
        self.input = ImageLineEdit()
        self.input.images_added.connect(self.references.add_sources)
        self.input.setPlaceholderText("描述你想怎么改…")
        self.input.returnPressed.connect(self.send_input)
        self.send = QPushButton("发送")
        self.send.clicked.connect(self.send_input)
        row.addWidget(self.input, 1)
        row.addWidget(self.send)
        content.addLayout(row)
        controls = QHBoxLayout()
        self.talk = QPushButton("按住说话 · F8")
        self.talk.setObjectName("talk")
        self.talk.setEnabled(False)
        self.talk.pressed.connect(self.start_recording)
        self.talk.released.connect(self.stop_recording)
        self.stop_button = QPushButton("停止当前操作")
        self.stop_button.clicked.connect(lambda: self.backend and self.backend.cancel())
        self.camera = QPushButton("还给玩家")
        self.camera.clicked.connect(
            lambda: self.backend and self.backend.control("presentation camera-player"))
        controls.addWidget(self.talk, 2)
        controls.addWidget(self.stop_button, 1)
        controls.addWidget(self.camera, 1)
        content.addLayout(controls)
        self.skip_presentation = QPushButton("跳过演出")
        self.skip_presentation.clicked.connect(
            lambda: self.backend and self.backend.control(
                f"presentation skip --revision {self.world.get('revision', 0)}"))
        self.skip_presentation.hide()
        content.addWidget(self.skip_presentation)
        content.addWidget(label("拖动顶部移动窗口 · F9 切换点击穿透 · 收起为悬浮球", "hint"))
        panel.addWidget(self.body, 1)
        outer.addWidget(self.card, 1)

        self.avatar = AvatarView()
        if self._panel_size.height() <= 650:
            avatar_size = QSize(150, 200)
        elif self._panel_size.height() <= 720:
            avatar_size = QSize(180, 250)
        else:
            avatar_size = QSize(220, 300)
        self.avatar.setFixedSize(avatar_size)
        avatar_row = QHBoxLayout()
        avatar_row.addStretch()
        avatar_row.addWidget(self.avatar)
        outer.addLayout(avatar_row)

        self.pill_page = QWidget()
        pill_layout = QVBoxLayout(self.pill_page)
        pill_layout.setContentsMargins(0, 0, 0, 0)
        pill_layout.addStretch()
        self.pill = PillButton()
        self.pill.setFixedSize(PILL_SIZE)
        self.pill.clicked_pill.connect(self.toggle_fold)
        pill_row = QHBoxLayout()
        pill_row.addStretch()
        pill_row.addWidget(self.pill)
        pill_layout.addLayout(pill_row)

        self.stack = QStackedLayout(self)
        self.stack.addWidget(self.panel_page)
        self.stack.addWidget(self.pill_page)
        self.stack.setCurrentWidget(self.panel_page)
        self.setFixedSize(self._panel_size)
        self.avatar.fallback_changed.connect(self._avatar_fallback)
        self.avatar.activated.connect(self.open_avatar_settings)

    def _setup_tray(self, icon: QIcon) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icon, self)
        menu = QMenu(self)
        menu.addAction("显示助手", self.show_companion)
        menu.addAction("设置…", self.open_agent_settings)
        menu.addAction("重新连接对话", self.retry_connection)
        menu.addSeparator()
        menu.addAction("退出客户端", self.exit_client)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.setToolTip("LevelControl 客户端")
        self.tray.show()
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def _apply_theme(self, name: str, persist: bool = True) -> None:
        self.theme_name = name if name in {"dark", "light"} else "dark"
        self.setStyleSheet(stylesheet(self.theme_name))
        self.status_pill.set_theme(self.theme_name)
        self.ui_settings.theme = self.theme_name
        if persist:
            self.ui_settings.save()
        self.update_status()

    def toggle_theme(self) -> None:
        self._apply_theme("light" if self.theme_name == "dark" else "dark")

    # ------------------------------------------------------------ avatar
    def _load_avatar(self) -> None:
        config = self.avatar_settings.active_config()
        resolved = self.avatar_settings.resolve(config)
        self.avatar.apply(config, resolved)
        if self.avatar_settings.error:
            self.partial.setText(self.avatar_settings.error)

    def _avatar_fallback(self, failed: bool) -> None:
        if failed:
            self.partial.setText("形象文件无法读取，已恢复默认形象。")

    def open_avatar_settings(self) -> None:
        dialog = AvatarPanel(AvatarSettings(), self.theme_name, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.avatar_settings = AvatarSettings()
            self._load_avatar()

    # ------------------------------------------------------------- project
    def _update_project_chip(self) -> None:
        target = self.active_target
        if target is None:
            self.project_chip.set_target("未配置项目", 0, "点击选择 UE 项目")
            return
        detail = [target.kind]
        if target.path:
            detail.append(target.path)
        self.project_chip.set_target(target.name, target.port, "\n".join(detail))

    def open_project_settings(self) -> None:
        draft = copy.deepcopy(self.project_settings)
        dialog = ProjectPanel(draft, self.theme_name, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        action = draft.by_id(dialog.action_target.id) if dialog.action_target else None
        # Re-read the file: closing without saving must not keep draft edits.
        self.project_settings = ProjectSettings()
        self.active_target = action or self.project_settings.active_target()
        if self.active_target is not None:
            self.port = self.active_target.port
        self._update_project_chip()
        if accepted and action is not None:
            self._activate_target(action, launch=dialog.action_launch)

    def _activate_target(self, target: ProjectTarget, *, launch: bool) -> None:
        current = self.active_target
        if (current is not None and current.id != target.id
                and self.controller.owns(current) and self.isVisible()):
            from PySide6.QtWidgets import QMessageBox

            answer = QMessageBox.question(
                self, "切换项目",
                "当前游戏由本客户端启动。切换前是否停止它？\n"
                "停止会丢失未保存的游戏内状态；保持运行则客户端不再控制该进程。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if answer == QMessageBox.StandardButton.Yes:
                self.controller.stop_owned(current)
        if self.busy and self.backend is not None:
            self.backend.cancel()
        self.active_target = target
        self.port = target.port
        self._update_project_chip()
        self._set_status("正在检查项目…", "busy")
        self.controller.activate(target, launch=launch)

    def _project_status(self, text: str, generation: int) -> None:
        if generation != self.controller.latest_generation():
            return
        self.partial.setText(text)
        self._set_status(text, "busy")

    def _project_connected(self, target_id: str, health: dict[str, Any], generation: int) -> None:
        if generation != self.controller.latest_generation():
            return
        if self.active_target is None or self.active_target.id != target_id:
            return
        self.online = True
        self.world_name = str(health.get("world", ""))
        if self.project_settings.by_id(target_id) is not None:
            self.project_settings.active = target_id
            try:
                self.project_settings.save()
            except ProjectError as error:
                self.partial.setText(str(error))
        self._ensure_backend(self.active_target)
        self._set_status("已连接游戏 · 正在连接对话…", "busy")

    def _project_failed(self, target_id: str, message: str, generation: int) -> None:
        if generation != self.controller.latest_generation():
            return
        if self.active_target is None or self.active_target.id != target_id:
            return
        self.online = False
        self.partial.setText(message[:220])
        self.partial.setToolTip(message)
        self._set_status("未运行 · 点击启动", "offline")
        self.reconnect.show()

    def _ensure_backend(self, target: ProjectTarget) -> None:
        if (self.backend is not None and self.backend.port == target.port
                and self.backend_target == target.id
                and self.backend.harness == self.ui_settings.harness
                and self.backend.capabilities == self.capabilities):
            return
        self.backend_generation += 1
        generation = self.backend_generation
        bridge = Bridge()
        bridge.event.connect(lambda event, gen=generation: self.handle(event, gen))
        old = self.backend
        old_bridge = self.bridge
        self.ready = False
        self.busy = False
        self.recording = False
        self.model_pending = False
        self.model_catalog = []
        self.model_picker.clear()
        self.model_picker.addItem("读取 Agent 模型…", "")
        self.refresh_efforts(None)
        self.model_picker.setEnabled(False)
        self.bridge = bridge
        self.backend = ConversationController(
            target, self.project_settings, bridge.event.emit, harness=self.ui_settings.harness,
            capabilities=self.capabilities)
        self.backend_target = target.id
        self.port = target.port
        if old is not None:
            self.tools.clear()
            self.world = {}
            self.world_name = ""
            self.tool_trace.set_tools([])
            self.tool_trace.hide()
            self.chat.add_notice("已切换项目，对话会话已重建；关卡与设计卡保留。")
            try:
                if old_bridge is not None:
                    old_bridge.event.disconnect()
            except (RuntimeError, TypeError):
                pass
            threading.Thread(target=old.close, name="close-old-backend", daemon=True).start()

    # -------------------------------------------------------------- agent
    def refresh_model_state(self, event: dict[str, Any]) -> None:
        self.model_catalog = event.get("models", [])
        current = event.get("model", "")
        self.model_picker.clear()
        for metadata in self.model_catalog:
            model = metadata["model"]
            display = metadata.get("displayName") or model
            self.model_picker.addItem(f"{display} ({model})" if display != model else model, model)
            self.model_picker.setItemData(self.model_picker.count() - 1,
                                          metadata.get("description", model),
                                          Qt.ItemDataRole.ToolTipRole)
        if current and self.model_picker.findData(current) < 0:
            self.model_picker.addItem(current, current)
        if not self.model_picker.count():
            self.model_picker.addItem("Agent 配置", "")
        self.model_picker.setCurrentIndex(max(0, self.model_picker.findData(current)))
        resolved = event.get("resolved_model") or current
        self.model_picker.setToolTip(f"本机 Agent 配置与模型列表；当前模型：{resolved}")
        self.refresh_efforts(event.get("effort"))

    def refresh_efforts(self, effort: str | None) -> None:
        metadata = next((item for item in self.model_catalog
                         if item["model"] == self.model_picker.currentData()), {})
        self.effort_picker.clear()
        self.effort_picker.addItem("Agent 默认", None)
        for option in metadata.get("supportedReasoningEfforts", []):
            mode = option["reasoningEffort"]
            self.effort_picker.addItem(f"{EFFORTS.get(mode, mode)} ({mode})", mode)
            if option.get("description"):
                self.effort_picker.setItemData(self.effort_picker.count() - 1,
                                               option["description"],
                                               Qt.ItemDataRole.ToolTipRole)
        if effort is not None and self.effort_picker.findData(effort) < 0:
            self.effort_picker.addItem(f"Agent 配置 ({effort})", effort)
        self.effort_picker.setCurrentIndex(max(0, self.effort_picker.findData(effort)))

    def model_changed(self, *args: Any) -> None:
        if not self.ready or self.busy or self.recording or self.model_pending:
            return
        metadata = next((item for item in self.model_catalog
                         if item["model"] == self.model_picker.currentData()), {})
        effort = self.effort_picker.currentData()
        modes = [item["reasoningEffort"]
                 for item in metadata.get("supportedReasoningEfforts", [])]
        if effort is not None and effort not in modes:
            effort = metadata.get("defaultReasoningEffort")
        self.refresh_efforts(effort)
        self.effort_changed()

    def effort_changed(self, *args: Any) -> None:
        if (self.backend is None or not self.ready or self.busy
                or self.recording or self.model_pending):
            return
        model = self.model_picker.currentData()
        if model:
            self.model_pending = True
            self.update_status()
            self.backend.select_model(model, self.effort_picker.currentData())

    def open_agent_settings(self) -> None:
        target = self.active_target
        if target is None:
            self.chat.add_notice("请先选择 UE 项目。")
            return
        if self.backend is not None:
            workspace, data_dir = self.backend.workspace, self.backend.data_dir
        else:
            workspace = workspace_for(target)
            store = store_for(target, self.project_settings.bundle)
            data_dir = store.parent if store else workspace / ".levelcontrol/data"
        dialog = SettingsDialog(
            workspace=workspace, data_dir=data_dir, project_id=target.id,
            port=target.port, theme=self.theme_name, harness=self.ui_settings.harness,
            design_file=design_for(target, self.project_settings.bundle),
            capabilities=self.capabilities, parent=self)
        dialog.theme_changed.connect(self._apply_theme)
        dialog.project_requested.connect(self.open_project_settings)
        dialog.avatar_requested.connect(self.open_avatar_settings)
        dialog.harness_changed.connect(self.change_harness)
        dialog.capabilities_changed.connect(self.change_capabilities)
        dialog.exec()

    def change_capabilities(self, capabilities: AgentCapabilities) -> None:
        self.capabilities = capabilities
        if self.backend is not None and self.active_target is not None:
            self._ensure_backend(self.active_target)

    def change_harness(self, harness: str) -> None:
        if harness == self.ui_settings.harness:
            return
        previous = self.ui_settings.harness
        self.ui_settings.harness = harness
        try:
            self.ui_settings.save()
        except OSError as error:
            self.ui_settings.harness = previous
            self.chat.add_error(str(error))
            return
        self.agent_chip.setText("Claude Code" if harness == "claude" else "Codex")
        if self.backend is not None and self.active_target is not None:
            self._ensure_backend(self.active_target)

    def answer_agent(self, event: dict[str, Any]) -> None:
        backend = self.backend
        if backend is None:
            return
        arguments = event.get("arguments", {})
        answer: dict[str, Any] = {"allow": False}
        if event["type"] == "question":
            answers = {}
            for question in arguments.get("questions", []):
                prompt = question.get("question", "")
                options = "\n".join(str(item.get("label", "")) + "："
                                    + str(item.get("description", ""))
                                    for item in question.get("options", []))
                text, accepted = QInputDialog.getMultiLineText(
                    self, "Agent 提问", prompt + "\n" + options)
                if not accepted:
                    break
                answers[question.get("id", prompt)] = text
            else:
                answer = {"allow": True, "answers": answers}
        else:
            box = QMessageBox(self)
            box.setWindowTitle("Agent 请求授权")
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(event.get("name", "工具请求"))
            box.setInformativeText(str(arguments.get("command") or arguments.get("reason")
                                       or arguments.get("description")
                                       or arguments.get("message")
                                       or "请展开详细信息核对请求。"))
            box.setDetailedText(json.dumps(arguments, ensure_ascii=False, indent=2))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            answer["allow"] = box.exec() == QMessageBox.StandardButton.Yes
        if backend is self.backend:
            backend.respond(event["request_id"], answer)

    # -------------------------------------------------------------- input
    def send_input(self) -> None:
        text = self.input.text().strip()
        if (text or self.references.images) and self.submit(text):
            self.input.clear()

    def submit(self, text: str) -> bool:
        if self.model_pending:
            self.partial.setText("模型配置应用后即可发送。")
            return False
        if not self.ready or not self.online or self.backend is None:
            self.partial.setText("连接完成后即可发送。")
            return False
        if self.references.blocked:
            self.partial.setText("请完成或取消图片处理后发送。")
            return False
        images = tuple(self.references.images)
        try:
            validate_images(images, self.model_catalog, self.model_picker.currentData() or "")
        except ValueError as error:
            self.partial.setText(str(error))
            return False
        self.chat.add_user(text or "[图片分析]")
        self.partial.setText("已排队，当前操作完成后处理。" if self.busy else "")
        self.backend.send(text, images)
        self.references.mark_submitted()
        return True

    def start_recording(self, keyboard: bool = False) -> None:
        if (self.recording or self.backend is None or self.backend.speech is None
                or not self.ready or not self.online or self.model_pending):
            return
        self.recording = True
        self.recording_keyboard = keyboard
        self.backend.recording = True
        self.partial.setText("正在听…松开后发送")
        self.talk.setText("正在录音…")
        self.backend.control(f"presentation hold --lease-id {self.backend.voice_lease}")
        bridge = self.bridge
        self.backend.speech.start(
            lambda text: bridge and bridge.event.emit({"type": "partial",
                                                                 "text": text}),
            lambda result: bridge and bridge.event.emit({"type": "transcript",
                                                                   "result": result}),
        )
        self.update_status()

    def stop_recording(self) -> None:
        if self.recording and self.backend is not None and self.backend.speech:
            self.backend.speech.stop()
            self.talk.setText("识别中…")

    # ---------------------------------------------------------- interaction
    def keys(self) -> None:
        if QApplication.activeModalWidget() is not None:
            return
        f8 = bool(ctypes.windll.user32.GetAsyncKeyState(0x77) & 0x8000)
        f9 = bool(ctypes.windll.user32.GetAsyncKeyState(0x78) & 0x8000)
        if f8 and not self.f8 and not self.isActiveWindow():
            self.start_recording(True)
        elif not f8 and self.f8:
            self.stop_recording()
        if (self.recording and not self.recording_keyboard
                and not (ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)):
            self.stop_recording()
        if f9 and not self.f9 and not self.isActiveWindow():
            self.toggle_passthrough()
        self.f8, self.f9 = f8, f9

    def toggle_passthrough(self) -> None:
        position, size = self.pos(), self.size()
        self.passthrough = not self.passthrough
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, self.passthrough)
        self.show()
        self.move(position)
        self.setFixedSize(size)
        self.setStyleSheet(stylesheet(self.theme_name))
        self.update_status()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not event.isAutoRepeat() and event.key() == Qt.Key.Key_F9:
            self.f9 = True
            self.toggle_passthrough()
        elif not event.isAutoRepeat() and event.key() == Qt.Key.Key_F8:
            self.f8 = True
            self.start_recording(True)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_F8 and not event.isAutoRepeat():
            self.stop_recording()
        else:
            super().keyReleaseEvent(event)

    def update_status(self) -> None:
        if self.active_target is not None and not self.online:
            text = "未运行 · 点击启动" if self.active_target.kind != "attach" else "未连接"
            state = "offline"
        elif self.recording:
            text = "● 正在录音 · 松开后发送"
            state = "recording"
        elif not self.online:
            text = "游戏未连接 · 等待启动"
            state = "offline"
        elif self.connection_failed:
            text = "对话连接失败 · 请重新连接"
            state = "error"
        elif not self.ready:
            text = "正在连接游戏与对话…"
            state = "busy"
        elif self.model_pending:
            text = "正在应用模型与思考强度…"
            state = "busy"
        elif self.busy:
            text = "正在理解与处理…"
            state = "busy"
        elif self.world.get("action_state") in {"running", "paused", "checkpoint"}:
            action = "已暂停" if self.world["action_state"] == "paused" else "执行中"
            text = f"动作{action} · {self.world.get('action_elapsed_s', 0):.1f} s"
            state = "busy"
        elif self.world.get("state") in {"previewing", "settled", "committed"}:
            total = self.world.get("desired_count", 0)
            text = f"施工中 · {self.world.get('settled_count', 0)}/{total} 构件"
            if self.world.get("commit_held"):
                text += " · 等待你的修改"
            state = "busy"
        else:
            text = "就绪 · 可以继续修改关卡"
            state = "ready"
        if self.passthrough:
            text += " · 穿透中，F9 恢复"
        self.status_pill.set_status(text, state)
        state_color = colors(self.theme_name).get(
            StatusPill.STATE_TOKENS.get(state, "warm"), "#F2C57C")
        self.pill.set_status_color(state_color)
        self.pill.setText(f"{text.split(' · ')[0]}\n"
                          f"{(self.active_target.name if self.active_target else 'LevelControl')}"
                          f" · {self.port}")
        self.pill.setToolTip(text + "\n单击展开 · 拖动移动")
        self.world_label.setText(self.world_name)
        self.send.setEnabled(self.ready and self.online and self.backend is not None
                             and not self.references.blocked and not self.model_pending)
        self.talk.setEnabled(self.ready and self.online and self.backend is not None
                             and self.backend.speech is not None and not self.model_pending)
        can_select = self.ready and self.backend is not None and not (
            self.busy or self.recording or self.model_pending)
        self.model_picker.setEnabled(can_select and bool(self.model_catalog))
        has_efforts = any(item["model"] == self.model_picker.currentData()
                          and item.get("supportedReasoningEfforts") for item in self.model_catalog)
        self.effort_picker.setEnabled(can_select and has_efforts)
        self.skip_presentation.setVisible(
            self.world.get("state") in {"previewing", "settled", "committed"}
            and not self.world.get("instant", True))

    def handle(self, event: dict[str, Any], generation: int | None = None) -> None:
        if generation is not None and generation != self.backend_generation:
            return
        kind = event["type"]
        if kind == "connecting":
            self.ready = False
            self.model_pending = False
            self.connection_failed = False
        elif kind == "ready":
            self.ready = True
            self.connection_failed = False
            self.reconnect.hide()
            self.agent_chip.setText("Claude Code" if event.get("harness") == "claude" else "Codex")
            self.model_pending = False
            self.refresh_model_state(event)
            if event.get("new_session"):
                self.reply = ""
                self.chat.clear()
                self.tools.clear()
                self.tool_trace.set_tools([])
                self.tool_trace.hide()
                self.chat.add_notice("已新建本机 Agent 对话会话。关卡与设计卡保留。")
            if self.auto_prompt:
                QTimer.singleShot(500, self.send_auto_prompt)
        elif kind == "model_configuring":
            self.model_pending = True
        elif kind in {"model_changed", "model_error"}:
            self.model_pending = False
            if "models" in event:
                self.refresh_model_state(event)
            if kind == "model_error":
                self.chat.add_error(event["message"])
            else:
                self.partial.setText("模型与思考强度已应用；下一条消息使用新配置。")
        elif kind in {"approval", "question"}:
            self.answer_agent(event)
        elif kind == "speech_ready":
            self.talk.setEnabled(self.ready and self.online)
        elif kind in {"speech_error", "error", "connection_error", "notice"}:
            message = event["message"]
            if kind == "speech_error" and "bootstrap_voice" in message:
                message = "本地语音模型未安装；文字对话仍可用。"
            self.partial.setText(message[:180])
            self.partial.setToolTip(event["message"])
            if kind == "error":
                self.busy = False
                self.chat.add_error(message)
            if kind == "connection_error":
                self.ready = False
                self.model_pending = False
                self.connection_failed = True
                self.reconnect.show()
                self.chat.add_error(message)
        elif kind == "world":
            self.online = True
            if (self.world.get("state") != "completed"
                    and event["state"].get("state") == "completed"
                    and not self.busy and not self.recording):
                self.partial.setText(
                    f"施工完成 · {event['state'].get('desired_count', 0)} 个构件")
            self.world = event["state"]
        elif kind == "world_offline":
            self.online = False
        elif kind == "build_pending":
            self.partial.setText("构建已排队，等待当前输入或提交租约结束。")
        elif kind == "build_ready":
            self.partial.setText(f"构建完成 · {event['entity_count']} 个构件 · "
                                 f"{event['seconds']:.2f} s")
        elif kind == "turn_started":
            self.busy = True
            self.reply = ""
            self.chat.begin_assistant()
        elif kind == "reply_delta":
            self.reply += event["text"]
            self.chat.append_assistant(event["text"])
        elif kind == "turn_finished":
            self.busy = False
            self.last_result = event["result"]
            self.chat.finish_assistant(
                self.last_result["final"] or "已停止当前对话操作。")
            if self.screenshot:
                QTimer.singleShot(500, self.capture)
        elif kind == "partial":
            self.partial.setText(event["text"] or "正在听…")
        elif kind == "transcript":
            self.recording = False
            if self.backend is not None:
                self.backend.recording = False
            self.talk.setText("按住说话 · F8")
            result = event["result"]
            if result.get("text"):
                self.partial.setText(f"已转写 · {result['finalization_s']:.2f} s")
                self.submit(result["text"])
            else:
                self.partial.setText(result.get("error", "没有听清，请再说一次。"))
                if self.backend is not None:
                    self.backend.control(
                        f"presentation resume --lease-id {self.backend.voice_lease}")
        elif kind == "tool":
            self.tools[event["id"]] = event
            self.tool_trace.show()
            self.tool_trace.set_tools(list(self.tools.values()))
        self.update_status()

    def _set_status(self, text: str, state: str) -> None:
        self.status_pill.set_status(text, state)
        self.world_label.setText(self.world_name)

    def send_auto_prompt(self) -> None:
        if self.auto_prompt and self.ready and self.online:
            self.submit(self.auto_prompt)
            self.auto_prompt = None
        elif self.auto_prompt:
            QTimer.singleShot(500, self.send_auto_prompt)

    def retry_connection(self) -> None:
        self.ready = False
        self.connection_failed = False
        self.reconnect.hide()
        self.partial.clear()
        if self.backend is not None:
            self.backend.reconnect()
        elif self.active_target is not None:
            self._activate_target(self.active_target, launch=False)

    # ------------------------------------------------------------- collapse
    def toggle_fold(self) -> None:
        self.set_collapsed(not self.collapsed)

    def set_collapsed(self, collapsed: bool, animate: bool = True) -> None:
        if collapsed == self.collapsed or self._animating:
            return
        current = self.geometry()
        if not self.collapsed:
            self._panel_pos = self.pos()
            self.ui_settings.panel = (self._panel_pos.x(), self._panel_pos.y())
        target_size = PILL_SIZE if collapsed else self._panel_size
        target = self._anchored_rect(current, target_size)
        self.stack.setCurrentWidget(self.pill_page if collapsed else self.panel_page)
        self.avatar.setVisible(not collapsed)
        if not animate or os.environ.get("LC_DISABLE_ANIMATIONS") == "1":
            self.setFixedSize(target_size)
            self.move(target.topLeft())
            self._finish_collapsed(collapsed, target)
            return
        self._animating = True
        animation = QPropertyAnimation(self, b"geometry", self)
        animation.setDuration(200)
        animation.setStartValue(current)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(
            lambda: self._finish_collapsed(collapsed, target))
        self._anim = animation
        animation.start()

    def _anchored_rect(self, current: QRect, size: QSize) -> QRect:
        return QRect(current.right() - size.width() + 1,
                     current.bottom() - size.height() + 1,
                     size.width(), size.height())

    def _finish_collapsed(self, collapsed: bool, target: QRect) -> None:
        self._animating = False
        self.collapsed = collapsed
        self.setFixedSize(target.size())
        self.move(target.topLeft())
        self.fold.setText("展开" if collapsed else "收起")
        self.ui_settings.collapsed = collapsed
        if collapsed:
            self.ui_settings.pill = (self.x(), self.y())
        else:
            self._panel_pos = self.pos()
            self.ui_settings.panel = (self.x(), self.y())
        self.ui_settings.save()
        self.update_status()

    def _restore_position(self) -> None:
        rects = screen_rects()
        if self.ui_settings.collapsed:
            point = self.ui_settings.pill or self.ui_settings.panel
            self.stack.setCurrentWidget(self.pill_page)
            self.avatar.hide()
            self.setFixedSize(PILL_SIZE)
            self.collapsed = True
            self.fold.setText("展开")
            self._place(point, PILL_SIZE, rects)
        else:
            self._place(self.ui_settings.panel, self._panel_size, rects)
            self._panel_pos = self.pos()

    def _place(self, point: tuple[int, int] | None, size: QSize,
               rects: list[tuple[int, int, int, int]]) -> None:
        if rects:
            x, y, width, height = rects[0]
            default = (x + width - size.width() - 24, y + height - size.height() - 24)
        else:
            default = (0, 0)
        placed = clamp_point(point, (size.width(), size.height()), rects) or default
        self.move(*placed)

    # -------------------------------------------------------------- window
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if not self.collapsed:
            self._panel_pos = self.pos()
            self.ui_settings.panel = (self.x(), self.y())
            self.ui_settings.save()

    def capture(self) -> None:
        if self.screenshot:
            self.screenshot.parent.mkdir(parents=True, exist_ok=True)
            self.grab().save(str(self.screenshot))
            self.screenshot.with_suffix(".json").write_text(
                json.dumps({"result": self.last_result, "world": self.world,
                            "ready": self.ready}, ensure_ascii=False, indent=2),
                "utf-8")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.tray and self.tray.isVisible() and not self.quitting:
            event.ignore()
            self.hide()
            if not self.tray_notice:
                self.tray.showMessage("LevelControl 仍在运行",
                                      "点击托盘图标恢复；右键可重新连接或退出。")
                self.tray_notice = True
            return
        self._shutdown()
        super().closeEvent(event)

    def _shutdown(self) -> None:
        self.timer.stop()
        self.backend_generation += 1
        if self.tray is not None:
            self.tray.hide()
        self.controller.close()
        if self.backend is not None:
            self.backend.close()
            self.backend = None
        try:
            self.ui_settings.save()
        except OSError:
            pass

    def show_companion(self) -> None:
        if self.passthrough:
            self.toggle_passthrough()
        if self.collapsed:
            self.set_collapsed(False)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick}:
            self.show_companion()

    def exit_client(self) -> None:
        self.quitting = True
        self.close()
        QApplication.instance().quit()


def main() -> None:
    migrate()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--instance", default=os.environ.get("LC_INSTANCE", ""))
    parser.add_argument("--prompt")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    app = QApplication([])
    app.setApplicationName("LevelControl")
    name = args.instance or "LevelControl-companion"
    server = reserve_instance(name)
    if server is None:
        show_existing(name)
        return
    widget = Companion(args.port, args.prompt, args.screenshot)
    server.newConnection.connect(lambda: raise_existing(server, widget))
    widget.show()
    app.exec()


def reserve_instance(name: str | int = "LevelControl-companion") -> QLocalServer | None:
    if isinstance(name, int):
        name = f"LevelControl-companion-{name}"
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(250):
        probe.disconnectFromServer()
        return None
    server = QLocalServer()
    return server if server.listen(name) else None


def show_existing(name: str) -> None:
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(500):
        socket.write(b'{"action":"show"}')
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()


def raise_existing(server: QLocalServer, widget: QWidget) -> None:
    connection = server.nextPendingConnection()
    if connection:
        connection.waitForReadyRead(200)
        connection.disconnectFromServer()
        connection.deleteLater()
    if isinstance(widget, Companion):
        widget.show_companion()
    else:
        widget.showNormal()
        widget.raise_()
        widget.activateWindow()


if __name__ == "__main__":
    main()
