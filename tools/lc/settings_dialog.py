"""One settings entry for the unified companion."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .agent_capabilities import AgentCapabilities
from .integration import diagnose, executable, install
from .settings_io import base_directory
from .theme import stylesheet


class SettingsDialog(QDialog):
    theme_changed = Signal(str)
    project_requested = Signal()
    avatar_requested = Signal()
    harness_changed = Signal(str)
    capabilities_changed = Signal(object)

    def __init__(self, *, workspace: Path, data_dir: Path, project_id: str,
                 port: int, theme: str, harness: str = "codex",
                 design_file: Path | None = None,
                 capabilities: AgentCapabilities | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self.data_dir = data_dir
        self.project_id = project_id
        self.port = port
        self.design_file = design_file
        self.selected_harness = harness
        self.capabilities = capabilities or AgentCapabilities.load()
        self.setWindowTitle("LevelControl · 设置")
        self.resize(760, 560)
        self.setStyleSheet(stylesheet(theme))
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        for page, title in ((self._agent_page(), "Agent 与输入"),
                            (self._project_page(), "项目与游戏"),
                            (self._appearance_page(theme), "外观"),
                            (self._diagnostics_page(), "诊断")):
            page.setObjectName("settingsPage")
            self.tabs.addTab(page, title)
        layout.addWidget(self.tabs, 1)
        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)
        for label in self.findChildren(QLabel):
            label.setWordWrap(True)

    def _agent_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "模型与思考强度从本机 Agent 读取，在气泡中选择。搜索、读图与权限由 Agent 管理。"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Agent"))
        self.harness = QComboBox()
        self.harness.addItem("Codex", "codex")
        self.harness.addItem("Claude Code", "claude")
        self.harness.setCurrentIndex(max(0, self.harness.findData(self.selected_harness)))
        self.harness.currentIndexChanged.connect(
            lambda: self.harness_changed.emit(self.harness.currentData()))
        row.addWidget(self.harness, 1)
        layout.addLayout(row)
        layout.addWidget(QLabel("切换 Agent 会新建对话；关卡与设计卡保留。"))
        layout.addWidget(QLabel("外挂能力 · 默认关闭，仅影响 LevelControl 对话"))
        self.capability_checks: dict[str, QCheckBox] = {}
        for key, title, description in (
            ("plugins", "插件", "允许加载原生配置中已启用的插件；其他类别开关仍然生效。"),
            ("skills", "技能", "SKILL.md、技能目录与自定义命令；关闭时禁用实际技能清单。"),
            ("workflows", "工作流", "动态工作流、自动指令文件、hooks 和自动记忆。"),
            ("mcp", "外部 MCP", "外部服务器、连接器及插件 MCP。LevelControl 关卡 MCP 始终启用。"),
        ):
            check = QCheckBox(title)
            check.setToolTip(description)
            check.setChecked(getattr(self.capabilities, key))
            self.capability_checks[key] = check
            layout.addWidget(check)
        layout.addWidget(QLabel("联网搜索、图片输入与原生读图：默认开启。\n"
                               "原生权限继续生效；应用后新建对话，关卡与设计卡保留。"))
        apply = QPushButton("应用并新建对话")
        apply.clicked.connect(self._apply_capabilities)
        layout.addWidget(apply)
        self.detected = QLabel(self._detected_text())
        self.detected.setWordWrap(True)
        layout.addWidget(self.detected)
        buttons = QHBoxLayout()
        prepare = QPushButton("准备 Agent 工作区")
        prepare.clicked.connect(self._prepare)
        open_workspace = QPushButton("打开工作区")
        open_workspace.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workspace))))
        doctor = QPushButton("诊断")
        doctor.clicked.connect(self._doctor)
        buttons.addWidget(prepare)
        buttons.addWidget(open_workspace)
        buttons.addWidget(doctor)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.agent_log = QPlainTextEdit()
        self.agent_log.setReadOnly(True)
        self.agent_log.setPlaceholderText("工作区安装与诊断结果")
        layout.addWidget(self.agent_log, 1)
        layout.addWidget(QLabel("语音：按住 F8，松开后转写发送；F9 切换鼠标穿透。\n"
                               "图片：拖拽、粘贴或选择，最多 4 张；只发图片时仅分析。"))
        self.diagnosed.connect(self._show_diagnosis)
        return page

    def _apply_capabilities(self) -> None:
        selected = AgentCapabilities(**{key: check.isChecked()
                                        for key, check in self.capability_checks.items()})
        try:
            selected.save()
        except OSError as error:
            self.agent_log.setPlainText("保存失败：" + str(error))
            return
        if selected != self.capabilities:
            self.capabilities = selected
            self.capabilities_changed.emit(selected)
            self.agent_log.setPlainText("外挂能力已保存；新的对话会话将使用这些开关。")

    def _project_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "项目、游戏启动、端口、世界库与插件复制都在项目设置中。"
            "项目 chip 也可以直接打开它。"))
        button = QPushButton("打开 UE 项目设置…")
        button.clicked.connect(self._project)
        layout.addWidget(button)
        layout.addStretch()
        return page

    def _appearance_page(self, theme: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        row.addWidget(QLabel("主题"))
        self.theme = QComboBox()
        self.theme.addItem("深色", "dark")
        self.theme.addItem("浅色", "light")
        self.theme.setCurrentIndex(max(0, self.theme.findData(theme)))
        self.theme.currentIndexChanged.connect(
            lambda: self.theme_changed.emit(self.theme.currentData()))
        row.addWidget(self.theme, 1)
        layout.addLayout(row)
        avatar = QPushButton("形象设置…")
        avatar.clicked.connect(self._avatar)
        layout.addWidget(avatar)
        layout.addStretch()
        return page

    def _diagnostics_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(f"配置根目录：{base_directory()}"))
        layout.addWidget(QLabel(f"项目工作区：{self.workspace}"))
        layout.addWidget(QLabel(f"数据目录：{self.data_dir}"))
        layout.addWidget(QLabel(f"项目：{self.project_id} · 端口 {self.port}"))
        open_logs = QPushButton("打开日志目录")
        open_logs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(base_directory() / "logs"))))
        layout.addWidget(open_logs)
        self.diagnostic_log = QPlainTextEdit()
        self.diagnostic_log.setReadOnly(True)
        layout.addWidget(self.diagnostic_log, 1)
        return page

    def _detected_text(self) -> str:
        parts = []
        for name in ("codex", "claude"):
            path = executable(name)
            parts.append(f"{name}: {path or '未找到'}")
        return "  ·  ".join(parts)

    def _prepare(self) -> None:
        try:
            state = install(self.workspace, self.port, self.project_id, self.data_dir,
                            self.design_file)
            self.agent_log.setPlainText(
                f"工作区已准备：{state['workspace']}\n"
                "在 Codex 或 Claude Code 中打开此目录，按客户端提示信任项目。")
        except (OSError, ValueError) as error:
            self.agent_log.setPlainText(str(error))

    def _doctor(self) -> None:
        self.agent_log.setPlainText("正在诊断…")
        threading.Thread(target=self._run_doctor, name="agent-doctor", daemon=True).start()

    diagnosed = Signal(object)

    def _run_doctor(self) -> None:
        try:
            self.diagnosed.emit(diagnose(self.workspace))
        except Exception as error:
            self.diagnosed.emit({"error": str(error)})

    def _show_diagnosis(self, report: Any) -> None:
        self.agent_log.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))

    def _project(self) -> None:
        self.project_requested.emit()
        self.accept()

    def _avatar(self) -> None:
        self.avatar_requested.emit()
        self.accept()
