"""Qt provider settings with asynchronous, non-mutating API checks."""

from __future__ import annotations

import copy
import threading
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .models import ModelSettings, Provider, fetch_models, preset, probe_model


class Diagnostics(QObject):
    result = Signal(dict)


class ModelPanel(QDialog):
    def __init__(self, settings: ModelSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LevelControl · API 与供应商设置")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(780, 660)
        self.setStyleSheet("""
            QDialog { background: #142536; color: #e2edf2; }
            QWidget { font-family: 'Microsoft YaHei UI'; font-size: 13px; color: #e2edf2; }
            QLineEdit, QPlainTextEdit, QListWidget, QComboBox {
                background: #102432; border: 1px solid #3e6073; border-radius: 5px; padding: 7px; }
            QComboBox QAbstractItemView { background: #102432;
                                         selection-background-color: #376f72; }
            QListWidget::item:selected { background: #376f72; }
            QPushButton { background: #2b495b; border: none;
                          border-radius: 6px; padding: 8px 12px; }
            QPushButton:hover { background: #3a6476; }
            QPushButton:disabled { color: #708798; }
            QLabel#message { color: #f2c57c; }
        """)
        self.settings = settings
        self.providers = copy.deepcopy(settings.providers)
        self.current: Provider | None = None
        self.loading = False
        self.pending = False
        self.diagnostics = Diagnostics(self)
        self.diagnostics.result.connect(self.diagnostic_finished)
        outer = QVBoxLayout(self)
        intro = QLabel("配置 Responses 兼容 API。OpenAI 与 DeepSeek 均可直接接入。")
        intro.setWordWrap(True)
        outer.addWidget(intro)
        columns = QHBoxLayout()
        left = QVBoxLayout()
        self.provider_list = QListWidget()
        self.provider_list.setFixedWidth(175)
        self.provider_list.currentRowChanged.connect(self.select_provider)
        left.addWidget(self.provider_list)
        self.new_kind = QComboBox()
        for text, value in [("OpenAI", "openai"), ("DeepSeek", "deepseek"),
                            ("自定义 Responses", "custom")]:
            self.new_kind.addItem(text, value)
        left.addWidget(self.new_kind)
        add = QPushButton("新增供应商")
        add.clicked.connect(self.add_provider)
        left.addWidget(add)
        self.remove = QPushButton("删除供应商")
        self.remove.clicked.connect(self.remove_provider)
        left.addWidget(self.remove)
        columns.addLayout(left)
        right = QVBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit()
        self.address = QLineEdit()
        self.address.setPlaceholderText("https://api.openai.com/v1")
        key_row = QHBoxLayout()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API Key，仅保存到本机 Windows 用户")
        self.show_key = QPushButton("显示")
        self.show_key.setCheckable(True)
        self.show_key.toggled.connect(lambda visible: self.key.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password))
        key_row.addWidget(self.key)
        key_row.addWidget(self.show_key)
        self.models = QPlainTextEdit()
        self.models.setPlaceholderText("每行一个模型 ID；可读取 API 模型列表，也可手动填写。")
        self.models.setMaximumHeight(130)
        self.models.textChanged.connect(self.refresh_test_models)
        self.efforts = QLineEdit()
        self.efforts.setPlaceholderText("例如 none,low,medium,high；留空表示不指定")
        form.addRow("名称", self.name)
        form.addRow("API 根地址", self.address)
        form.addRow("API Key", key_row)
        form.addRow("模型列表", self.models)
        form.addRow("思考档位", self.efforts)
        right.addLayout(form)
        detail = QLabel("DeepSeek：none 关闭，low / high / max。其他服务需填写模型实际支持的档位。"
                        "不确定时可留空，不指定思考参数。")
        detail.setWordWrap(True)
        right.addWidget(detail)
        test_row = QHBoxLayout()
        self.fetch = QPushButton("读取模型")
        self.fetch.clicked.connect(lambda: self.diagnose("models"))
        self.test_model = QComboBox()
        self.test_model.setMinimumWidth(150)
        self.test_model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.test_model.setMinimumContentsLength(12)
        self.test = QPushButton("测试工具调用")
        self.test.clicked.connect(lambda: self.diagnose("probe"))
        test_row.addWidget(self.fetch)
        test_row.addWidget(self.test_model, 1)
        test_row.addWidget(self.test)
        right.addLayout(test_row)
        cost = QLabel("工具测试会发送一次 API 请求，按供应商计费；不会访问或修改游戏。")
        cost.setWordWrap(True)
        right.addWidget(cost)
        self.message = QLabel("测试工具调用会发送一次小型模型请求，按供应商计费；"
                              "不会访问或修改游戏。")
        self.message.setObjectName("message")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.message)
        right.addStretch()
        columns.addLayout(right, 1)
        outer.addLayout(columns, 1)
        privacy = QLabel("API Key 使用 Windows DPAPI 加密。配置不修改全局 Codex 设置，"
                         "也不随项目打包。"
                         "切换供应商会新建对话会话，游戏世界与设计卡保留。")
        privacy.setWordWrap(True)
        outer.addWidget(privacy)
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.clicked.connect(lambda: self.save(False))
        use = QPushButton("保存并使用")
        use.clicked.connect(lambda: self.save(True))
        footer.addWidget(cancel)
        footer.addWidget(save)
        footer.addWidget(use)
        outer.addLayout(footer)
        self.refresh_list(settings.active)

    def store_form(self) -> None:
        if self.current is None or self.loading:
            return
        self.current.name = self.name.text().strip()
        if self.current.kind != "inherited":
            self.current.base_url = self.address.text().strip()
            self.current.api_key = self.key.text().strip()
            self.current.efforts = [v.strip() for v in self.efforts.text().split(",") if v.strip()]
        self.current.models = self.models.toPlainText().splitlines()
        self.current.model = self.test_model.currentText().strip()

    def refresh_list(self, identity: str) -> None:
        self.provider_list.blockSignals(True)
        self.provider_list.clear()
        self.provider_list.addItems([p.name for p in self.providers])
        index = next(i for i, p in enumerate(self.providers) if p.id == identity)
        self.provider_list.setCurrentRow(index)
        self.provider_list.blockSignals(False)
        self.select_provider(index)

    def select_provider(self, index: int) -> None:
        if index < 0:
            return
        self.store_form()
        self.current = self.providers[index]
        self.loading = True
        inherited = self.current.kind == "inherited"
        self.name.setText(self.current.name)
        self.address.setText(self.current.base_url)
        self.key.setText(self.current.api_key)
        self.show_key.setChecked(False)
        self.models.setPlainText("\n".join(self.current.models))
        self.efforts.setText(",".join(self.current.efforts))
        for widget in [self.address, self.key, self.show_key, self.efforts]:
            widget.setEnabled(not inherited)
        self.remove.setEnabled(not inherited and not self.pending)
        self.fetch.setEnabled(not inherited and not self.pending)
        self.test.setEnabled(not inherited and not self.pending)
        self.refresh_test_models()
        self.test_model.setCurrentText(self.current.model)
        self.loading = False
        self.message.setText("沿用本机已有 Codex 登录与供应商配置。模型能力由对话宿主读取。"
                             if inherited else "可读取模型列表，再测试所选模型的工具调用。")

    def refresh_test_models(self) -> None:
        selected = self.test_model.currentText() if hasattr(self, "test_model") else ""
        if not hasattr(self, "test_model"):
            return
        self.test_model.clear()
        self.test_model.addItems([v.strip() for v in self.models.toPlainText().splitlines()
                                  if v.strip()])
        if selected:
            self.test_model.setCurrentText(selected)

    def add_provider(self) -> None:
        self.store_form()
        provider = preset(self.new_kind.currentData())
        self.providers.append(provider)
        self.refresh_list(provider.id)

    def remove_provider(self) -> None:
        if self.current and self.current.kind != "inherited":
            self.providers.remove(self.current)
            self.current = None
            self.refresh_list(self.providers[0].id)

    def diagnose(self, operation: str) -> None:
        self.store_form()
        if self.current is None or self.pending:
            return
        try:
            provider = copy.deepcopy(self.current)
            provider.validate()
            if provider.kind in {"openai", "deepseek"} and not provider.api_key:
                raise ValueError("请先填写 API Key。")
        except ValueError as error:
            self.message.setText(str(error))
            return
        self.pending = True
        self.fetch.setEnabled(False)
        self.test.setEnabled(False)
        self.remove.setEnabled(False)
        self.message.setText("正在读取模型…" if operation == "models" else "正在测试模型工具调用…")

        def work() -> None:
            event: dict[str, Any] = {"id": provider.id, "operation": operation}
            try:
                event["value"] = (fetch_models(provider) if operation == "models"
                                  else probe_model(provider, provider.model, provider.effort))
                event["modalities"] = provider.modalities
            except Exception as error:
                event["error"] = provider.redact(error)
            self.diagnostics.result.emit(event)

        threading.Thread(target=work, name="model-api-check", daemon=True).start()

    def diagnostic_finished(self, event: dict[str, Any]) -> None:
        self.pending = False
        inherited = self.current is None or self.current.kind == "inherited"
        self.fetch.setEnabled(not inherited)
        self.test.setEnabled(not inherited)
        self.remove.setEnabled(not inherited)
        if self.current is None or event["id"] != self.current.id:
            return
        if "error" in event:
            self.message.setText(event["error"])
        elif event["operation"] == "models":
            self.current.modalities = event.get("modalities", {})
            self.models.setPlainText("\n".join(event["value"]))
            self.message.setText(f"已读取 {len(event['value'])} 个模型。"
                                 "请选择支持工具调用的对话模型。")
        else:
            self.message.setText(event["value"])

    def save(self, use: bool) -> None:
        self.store_form()
        previous = self.settings.providers, self.settings.active
        try:
            for provider in self.providers:
                provider.validate()
            if use and self.current:
                if not self.current.model:
                    raise ValueError("请先读取模型列表或填写模型 ID。")
                if self.current.kind in {"openai", "deepseek"} and not self.current.api_key:
                    raise ValueError("请填写当前供应商的 API Key。")
            self.settings.providers = copy.deepcopy(self.providers)
            if use and self.current:
                self.settings.active = self.current.id
            elif not any(p.id == self.settings.active for p in self.providers):
                self.settings.active = "inherited"
            self.settings.save()
        except (OSError, ValueError) as error:
            self.settings.providers, self.settings.active = previous
            self.message.setText(str(error))
            return
        self.accept()
