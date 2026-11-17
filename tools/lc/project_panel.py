"""UE project picker: validate, launch, attach, stop and install the plugin."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .project import (
    ProjectError,
    ProjectSettings,
    ProjectTarget,
    enable_plugin,
    find_free_port,
    install_plugin,
    target_id,
    validate_target,
)


class ProjectPanel(QDialog):
    def __init__(self, settings: ProjectSettings, theme_name: str = "dark",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.theme_name = theme_name
        self.action_target: ProjectTarget | None = None
        self.action_launch = False
        self.setWindowTitle("LevelControl · UE 项目")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(820, 640)
        outer = QVBoxLayout(self)
        columns = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.select)
        left.addWidget(self.list, 1)
        add_project = QPushButton("添加 .uproject…")
        add_project.clicked.connect(self.add_uproject)
        left.addWidget(add_project)
        add_exe = QPushButton("添加已打包游戏…")
        add_exe.clicked.connect(self.add_executable)
        left.addWidget(add_exe)
        add_attach = QPushButton("仅附加端口…")
        add_attach.clicked.connect(self.add_attach)
        left.addWidget(add_attach)
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit()
        self.path = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.protocol = QComboBox()
        self.protocol.addItem("协议 4（语义核心）", 4)
        self.protocol.addItem("协议 3（2.0）", 3)
        self.protocol.addItem("协议 2（1.0 兼容）", 2)
        self.store = QLineEdit()
        self.engine = QLineEdit()
        form.addRow("名称", self.name)
        form.addRow("路径", self.path)
        form.addRow("端口", self.port)
        form.addRow("协议", self.protocol)
        form.addRow("世界数据", self.store)
        form.addRow("编辑器", self.engine)
        right.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        row = QHBoxLayout()
        validate = QPushButton("验证")
        validate.clicked.connect(self.validate)
        row.addWidget(validate)
        self.install = QPushButton("复制插件到项目…")
        self.install.clicked.connect(self.copy_plugin)
        row.addWidget(self.install)
        row.addStretch()
        right.addLayout(row)
        actions = QHBoxLayout()
        start = QPushButton("启动并连接")
        start.setObjectName("primary")
        start.clicked.connect(lambda: self.activate(True))
        connect = QPushButton("仅连接")
        connect.clicked.connect(lambda: self.activate(False))
        stop = QPushButton("停止游戏进程")
        stop.setObjectName("danger")
        stop.clicked.connect(self.stop_game)
        actions.addWidget(start)
        actions.addWidget(connect)
        actions.addWidget(stop)
        right.addLayout(actions)
        columns.addLayout(right, 2)
        outer.addLayout(columns, 1)

        footer = QHBoxLayout()
        remove = QPushButton("删除目标")
        remove.clicked.connect(self.remove_target)
        footer.addWidget(remove)
        log = QPushButton("打开日志")
        log.clicked.connect(self.open_log)
        footer.addWidget(log)
        footer.addStretch()
        save = QPushButton("保存")
        save.clicked.connect(self.save)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        footer.addWidget(save)
        footer.addWidget(close)
        outer.addLayout(footer)
        self.refresh()

    def refresh(self, select_id: str = "") -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for target in self.settings.targets:
            entry = QListWidgetItem(f"{target.name}  ·  {target.kind}  ·  {target.port}")
            entry.setData(Qt.ItemDataRole.UserRole, target.id)
            self.list.addItem(entry)
        self.list.blockSignals(False)
        wanted = select_id or self.settings.active
        index = next((i for i, item in enumerate(self.settings.targets)
                      if item.id == wanted), 0)
        if self.settings.targets:
            self.list.setCurrentRow(index)
            self.select(index)

    def current(self) -> ProjectTarget | None:
        row = self.list.currentRow()
        return self.settings.targets[row] if 0 <= row < len(self.settings.targets) else None

    def select(self, row: int) -> None:
        target = self.settings.targets[row] if 0 <= row < len(self.settings.targets) else None
        if target is None:
            return
        self.name.setText(target.name)
        self.path.setText(target.path)
        self.port.setValue(target.port)
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(target.protocol)))
        self.store.setText(target.store)
        self.engine.setText(target.engine)
        self.path.setEnabled(target.kind in {"uproject", "exe"})
        self.install.setEnabled(target.kind == "uproject")
        self.status.setText("")

    def store_form(self) -> None:
        target = self.current()
        if target is None:
            return
        target.name = self.name.text().strip() or target.name
        target.path = self.path.text().strip()
        target.port = self.port.value()
        target.protocol = int(self.protocol.currentData())
        target.store = self.store.text().strip()
        target.engine = self.engine.text().strip()
        if target.kind in {"uproject", "exe"}:
            target.id = target_id(target.kind, target.path, target.port)

    def _new(self, kind: str, path: str) -> ProjectTarget:
        port = find_free_port(18871, [item.port for item in self.settings.targets])
        target = ProjectTarget(target_id(kind, path, port), kind,
                               Path(path).stem if path else f"已有游戏 {port}",
                               path, port, 3)
        self.settings.upsert(target)
        self.refresh(target.id)
        return target

    def add_uproject(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 UE 项目", "",
                                              "UE 项目 (*.uproject)")
        if path:
            self._new("uproject", path)

    def add_executable(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择打包游戏", "",
                                              "Windows 程序 (*.exe)")
        if path:
            self._new("exe", path)

    def add_attach(self) -> None:
        port = find_free_port(18871, [item.port for item in self.settings.targets])
        target = ProjectTarget(f"attach:{port}", "attach", f"已有游戏 {port}", "", port, 3)
        self.settings.upsert(target)
        self.refresh(target.id)

    def validate(self) -> None:
        self.store_form()
        target = self.current()
        if target is None:
            return
        status = validate_target(target, self.settings.bundle)
        messages = list(status.errors) + list(status.warnings)
        self.status.setText("\n".join(messages) or "项目看起来可用。")
        self.status.setToolTip("\n".join(messages))

    def copy_plugin(self) -> None:
        target = self.current()
        if target is None or target.kind != "uproject":
            return
        source = self.settings.bundle.companion / "ue/Plugins/LevelControlRuntime"
        if not source.exists():
            source = Path(__file__).resolve().parents[2] / "ue/Plugins/LevelControlRuntime"
        if QMessageBox.question(
                self, "复制插件",
                f"将把 LevelControlRuntime 复制到\n{Path(target.path).parent / 'Plugins'}\n"
                "需要重新编译 C++。继续？") != QMessageBox.StandardButton.Yes:
            return
        try:
            destination = install_plugin(Path(target.path), source)
            enable_plugin(Path(target.path))
        except (ProjectError, OSError) as error:
            self.status.setText(str(error))
            return
        self.status.setText(f"插件已复制到 {destination}；请在 UE 5.8 中重新编译。")

    def activate(self, launch: bool) -> None:
        self.store_form()
        target = self.current()
        if target is None:
            return
        try:
            self.settings.active = target.id
            self.settings.save()
        except ProjectError as error:
            self.status.setText(str(error))
            return
        self.action_target = target
        self.action_launch = launch
        self.accept()

    def stop_game(self) -> None:
        target = self.current()
        if target is None:
            return
        if QMessageBox.question(
                self, "停止游戏进程",
                "只停止由本客户端启动的游戏；未保存的游戏内状态可能丢失。继续？"
        ) != QMessageBox.StandardButton.Yes:
            return
        controller = self.parent().controller if isinstance(self.parent(), QWidget) else None
        if controller is not None and controller.stop_owned(target):
            self.status.setText("已停止本客户端启动的游戏。")
        else:
            self.status.setText("没有可停止的本客户端游戏进程。")

    def open_log(self) -> None:
        target = self.current()
        if target is None:
            return
        from .project import store_for

        store = store_for(target, self.settings.bundle)
        log = store.parent / "game.log" if store else None
        if log is not None and log.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log)))
        else:
            self.status.setText(f"日志尚不存在：{log}" if log else "该目标没有日志文件。")

    def remove_target(self) -> None:
        target = self.current()
        if target is None:
            return
        if QMessageBox.question(self, "删除目标",
                                "只删除项目配置，不删除世界数据。继续？"
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.settings.remove(target.id)
        self.refresh()

    def save(self) -> None:
        self.store_form()
        try:
            self.settings.save()
        except ProjectError as error:
            self.status.setText(str(error))
            return
        self.status.setText("已保存。")
