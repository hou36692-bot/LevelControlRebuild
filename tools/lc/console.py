"""Lightweight native-Harness control window; never starts a model conversation."""

from __future__ import annotations

import hashlib
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .client import ROOT, Client
from .controller import ProjectController
from .integration import install
from .project import ProjectSettings, ProjectTarget, store_for
from .project_panel import ProjectPanel
from .settings_io import base_directory


def workspace_for(target: ProjectTarget) -> Path:
    key = hashlib.sha256(target.id.encode()).hexdigest()[:16]
    return base_directory() / "workspaces" / key


def client_for(target: ProjectTarget, settings: ProjectSettings) -> Client:
    store = store_for(target, settings.bundle)
    data = store.parent if store else workspace_for(target) / ".levelcontrol/data"
    return Client(target.port, data_dir=data, workspace=workspace_for(target), project_id=target.id)


class Results(QObject):
    update = Signal(int, str, object)


class ControlWindow(QWidget):
    def __init__(self, settings: ProjectSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or ProjectSettings()
        self.controller = ProjectController(self.settings.bundle, parent=self)
        self.controller.connected.connect(self.connected)
        self.controller.failed.connect(self.failed)
        self.controller.status.connect(self.start_status)
        self.results = Results(self)
        self.results.update.connect(self.result)
        self.client: Client | None = None
        self.generation = 0
        self.monitor_busy = False
        self.command_busy = False
        self.setWindowTitle("LevelControl 3.0")
        self.resize(720, 470)
        layout = QVBoxLayout(self)
        title = QLabel("LevelControl 3.0 · 关卡控制台")
        title.setStyleSheet("font-size: 22px; font-weight: bold; padding: 8px 0;")
        layout.addWidget(title)
        layout.addWidget(QLabel("在 Codex 或 Claude Code 中与 AI 协作，在这里管理游戏。"))
        row = QHBoxLayout()
        self.projects = QComboBox()
        row.addWidget(self.projects, 1)
        manage = QPushButton("管理项目…")
        manage.clicked.connect(self.manage_projects)
        row.addWidget(manage)
        layout.addLayout(row)
        actions = QHBoxLayout()
        for text, action in (("启动并连接", lambda: self.activate(True)),
                             ("仅连接", lambda: self.activate(False)),
                             ("准备 Agent 工作区", self.prepare_workspace)):
            button = QPushButton(text)
            button.clicked.connect(action)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel("未连接游戏")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        self.cancel_button = QPushButton("取消未提交建造")
        self.cancel_button.clicked.connect(lambda: self.command("presentation cancel"))
        self.undo_button = QPushButton("撤销最近修改")
        self.undo_button.clicked.connect(lambda: self.command("undo --last"))
        self.skip_button = QPushButton("跳过演出")
        self.skip_button.clicked.connect(lambda: self.command("presentation skip"))
        for button in (self.cancel_button, self.undo_button, self.skip_button):
            controls.addWidget(button)
        layout.addLayout(controls)
        self.overlay_toggle = QCheckBox("显示游戏状态浮层")
        self.overlay_toggle.toggled.connect(self.toggle_overlay)
        layout.addWidget(self.overlay_toggle)
        self.overlay = QLabel("LevelControl · 未连接")
        self.overlay.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.overlay.setWindowTitle("LevelControl 状态")
        self.overlay.setStyleSheet("padding: 16px; font-size: 15px;")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("连接后在原生 Agent 中选择准备好的工作区。")
        layout.addWidget(self.log, 1)
        self.tray = QSystemTrayIcon(QIcon(str(ROOT / "tools/lc/assets/levelcontrol.svg")), self)
        self.setWindowIcon(self.tray.icon())
        menu = QMenu(self)
        show = QAction("显示控制台", self)
        show.triggered.connect(self.reveal)
        menu.addAction(show)
        quit_action = QAction("退出控制台（保留游戏）", self)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.reveal()
                                    if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.observe)
        self.timer.start(1000)
        self.refresh_projects()
        self.projects.currentIndexChanged.connect(self.selection_changed)

    def selection_changed(self) -> None:
        self.controller.cancel()
        self.client = None
        self.generation = -1
        self.status.setText("项目已切换，请启动或连接游戏。")
        self.overlay.setText("LevelControl · 未连接")

    def refresh_projects(self) -> None:
        self.projects.clear()
        for target in self.settings.targets:
            self.projects.addItem(f"{target.name} · {target.port}", target.id)
        index = self.projects.findData(self.settings.active)
        if index >= 0:
            self.projects.setCurrentIndex(index)

    def target(self) -> ProjectTarget | None:
        return self.settings.by_id(self.projects.currentData())

    def manage_projects(self) -> None:
        if self.command_busy:
            return
        panel = ProjectPanel(self.settings, parent=self)
        panel.exec()
        self.refresh_projects()
        if panel.action_target is not None:
            self.activate(panel.action_launch)

    def activate(self, launch: bool) -> None:
        target = self.target()
        if target is None or self.command_busy:
            return
        self.client = None
        self.settings.active = target.id
        self.settings.save()
        self.generation = self.controller.activate(target, launch=launch)
        self.status.setText(f"正在连接 {target.name}…")

    def connected(self, identity: str, health: dict, generation: int) -> None:
        if generation != self.generation:
            return
        target = self.settings.by_id(identity)
        if target is None:
            return
        self.client = client_for(target, self.settings)
        self.status.setText(f"已连接 {target.name} · 端口 {target.port}")
        self.log.appendPlainText(f"已连接 {target.name}。模型会话由原生 Agent 管理。")
        self.observe()

    def failed(self, identity: str, message: str, generation: int) -> None:
        if generation == self.generation:
            self.status.setText(message)

    def start_status(self, text: str, generation: int) -> None:
        if generation == self.generation:
            self.status.setText(text)

    def observe(self) -> None:
        if self.client is None or self.monitor_busy or self.command_busy:
            return
        self.monitor_busy = True
        client, generation = self.client, self.generation

        def work() -> None:
            try:
                data = {"world": client.read("objects * --count"),
                        "presentation": client.read("presentation")}
                self.results.update.emit(generation, "status", data)
            except Exception as error:
                self.results.update.emit(generation, "error", str(error))
            finally:
                self.monitor_busy = False

        threading.Thread(target=work, daemon=True).start()

    def command(self, command: str) -> None:
        if self.client is None or self.command_busy:
            return
        self.command_busy = True
        self.projects.setEnabled(False)
        client, generation = self.client, self.generation

        def work() -> None:
            try:
                result = client.edit(command)
                self.results.update.emit(generation, "receipt", result)
            except Exception as error:
                self.results.update.emit(generation, "command_error", str(error))

        threading.Thread(target=work, daemon=True).start()

    def result(self, generation: int, kind: str, data: object) -> None:
        if kind in {"receipt", "command_error"}:
            self.command_busy = False
            self.projects.setEnabled(True)
        if generation != self.generation:
            return
        if kind == "status":
            world, presentation = data["world"], data["presentation"]
            state = {"idle": "空闲", "previewing": "预览中", "settled": "预览就绪",
                     "committed": "已提交", "completed": "已完成"}.get(
                         presentation.get("state"), "状态未知")
            text = (f"世界版本 {world.get('version')} · 实体 {world.get('count', '?')} · "
                    f"建造 {state}")
            self.status.setText(text)
            self.overlay.setText(text)
        elif kind == "receipt":
            status = {"applied": "已提交", "scheduled": "处理中", "rejected": "已拒绝",
                      "failed": "失败"}.get(data.get("status"), "结果待确认")
            self.log.appendPlainText(
                f"{status} · 影响 {data.get('applied', 0)} 个实体 · "
                f"世界版本 {data.get('version', '?')}")
            for warning in data.get("warnings", []):
                self.log.appendPlainText(str(warning))
        else:
            self.status.setText(str(data))

    def prepare_workspace(self) -> None:
        target = self.target()
        if target is None:
            return
        client = client_for(target, self.settings)
        try:
            install(client.workspace, target.port, target.id, client.data_dir)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "工作区配置失败", str(error))
            return
        self.log.appendPlainText(
            f"Agent 工作区：{client.workspace}\n"
            "在 Codex 或 Claude Code 中选择此文件夹，并按客户端提示信任项目。\n"
            "已有模型、搜索、读图、技能和权限设置继续由 Agent 管理。")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(client.workspace)))

    def toggle_overlay(self, enabled: bool) -> None:
        self.overlay.setVisible(enabled)

    def reveal(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            event.ignore()
        else:
            self.quit()
            event.accept()

    def quit(self) -> None:
        self.timer.stop()
        self.overlay.hide()
        self.tray.hide()
        self.controller.close()
        QApplication.instance().quit()


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("LevelControl3")
    name = "LevelControl3-control-window"
    existing = QLocalSocket()
    existing.connectToServer(name)
    if existing.waitForConnected(250):
        existing.write(b"show")
        existing.waitForBytesWritten(250)
        return
    QLocalServer.removeServer(name)
    server = QLocalServer()
    if not server.listen(name):
        raise RuntimeError("Could not reserve the LevelControl control window")
    window = ControlWindow()
    def reveal_connection() -> None:
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.disconnectFromServer()
            connection.deleteLater()
        window.reveal()

    server.newConnection.connect(reveal_connection)
    window.show()
    app.exec()
    server.close()


if __name__ == "__main__":
    main()
