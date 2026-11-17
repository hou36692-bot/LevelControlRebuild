"""User-local model providers, Windows-protected keys, and API diagnostics."""

from __future__ import annotations

import base64
import copy
import ctypes
import json
import os
import re
import tempfile
import tomllib
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .client import ROOT

EFFORTS = {
    "none": "关闭", "minimal": "最小", "low": "低", "medium": "中",
    "high": "高", "xhigh": "更高", "max": "最大", "ultra": "极高",
}


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect_key(value: str, decrypt: bool = False) -> str:
    if not value:
        return ""
    if os.name != "nt":
        raise ValueError("API Key 加密保存需要 Windows。")
    raw = base64.b64decode(value, validate=True) if decrypt else value.encode("utf-8")
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, output = Blob(len(raw), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    call = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    call.restype = ctypes.c_int
    if not call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("API Key 加解密失败；请在当前 Windows 用户下重新填写密钥。")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        data = ctypes.string_at(output.data, output.size)
        return data.decode("utf-8") if decrypt else base64.b64encode(data).decode("ascii")
    finally:
        kernel.LocalFree(output.data)


def validate_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if any(character.isspace() for character in value):
        raise ValueError("API 根地址不能包含空格。")
    try:
        parsed.port
    except ValueError:
        raise ValueError("API 端口无效。") from None
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not (parsed.scheme == "https" or parsed.scheme == "http" and local)):
        raise ValueError("API 根地址需使用 HTTPS；本机 localhost / 127.0.0.1 可使用 HTTP。")
    if parsed.path.endswith(("/responses", "/models", "/chat/completions")):
        raise ValueError("请填写 API 根地址，不要包含 /responses、/models 或 /chat/completions。")
    return value


def model_ids(values: list[str]) -> list[str]:
    result = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if any(not re.fullmatch(r"[\w][\w./:-]{0,159}", value) for value in result):
        raise ValueError("模型 ID 不能包含空格或控制字符。")
    return result


@dataclass
class Provider:
    id: str
    name: str
    kind: str = "custom"
    base_url: str = ""
    models: list[str] = field(default_factory=list)
    efforts: list[str] = field(default_factory=list)
    model: str = ""
    effort: str = ""
    api_key: str = field(default="", repr=False)
    modalities: dict[str, list[str]] = field(default_factory=dict)

    def validate(self) -> None:
        if self.kind not in {"inherited", "openai", "deepseek", "custom"} or not self.id:
            raise ValueError("供应商配置无效。")
        self.name = self.name.strip()
        if not self.name or len(self.name) > 60:
            raise ValueError("供应商名称需为 1–60 个字符。")
        if self.kind != "inherited":
            self.base_url = validate_url(self.base_url)
        self.models = model_ids(self.models)
        self.efforts = list(dict.fromkeys(self.efforts))
        if any(value not in EFFORTS for value in self.efforts):
            raise ValueError("思考档位需使用 none/minimal/low/medium/high/xhigh/max/ultra。")
        if self.model and self.model not in self.models:
            self.models = model_ids(self.models + [self.model])

    def identity(self) -> tuple[str, ...]:
        # Never include this value in events or diagnostics.
        return self.id, self.kind, self.base_url, self.api_key

    def redact(self, value: Any) -> str:
        text = str(value)
        return text.replace(self.api_key, "[API Key]") if self.api_key else text


@dataclass
class Selection:
    provider: Provider = field(repr=False)
    model: str = ""
    effort: str = ""


def preset(kind: str) -> Provider:
    if kind == "deepseek":
        return Provider(uuid.uuid4().hex, "DeepSeek", kind, "https://api.deepseek.com",
                        ["deepseek-flash", "deepseek-v4-pro"],
                        ["none", "low", "high", "max"], "deepseek-flash", "high")
    if kind == "openai":
        return Provider(uuid.uuid4().hex, "OpenAI", kind, "https://api.openai.com/v1",
                        efforts=["none", "minimal", "low", "medium", "high", "xhigh"])
    return Provider(uuid.uuid4().hex, "自定义 Responses", "custom", "https://")


class ModelSettings:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(os.environ.get(
            "LC_MODEL_SETTINGS", str(Path(os.environ.get("LOCALAPPDATA", Path.home()))
                                      / "LevelControl/model-settings.json")))
        project = tomllib.loads((ROOT / "project/.codex/config.toml").read_text("utf-8"))
        default = Provider("inherited", "现有 Codex 配置", "inherited",
                           models=[project["model"]], model=project["model"],
                           effort=project.get("model_reasoning_effort", "low"))
        self.providers = [default]
        self.active = default.id
        self.error = ""
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text("utf-8"))
                if data.get("version") != 1:
                    raise ValueError("未知的模型设置版本。")
                providers = []
                for entry in data["providers"]:
                    entry = dict(entry)
                    protected = entry.pop("protected_key", "")
                    entry.pop("api_key", None)
                    provider = Provider(**entry, api_key=protect_key(protected, decrypt=True))
                    provider.validate()
                    providers.append(provider)
                if (not providers or len({p.id for p in providers}) != len(providers)
                        or not any(p.id == "inherited" and p.kind == "inherited"
                                   for p in providers)):
                    raise ValueError("供应商列表无效。")
                self.providers = providers
                self.active = data["active"]
                self.provider(self.active)
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            self.providers, self.active = [default], default.id
            self.error = "模型设置读取失败，原文件已保留。请打开 API 设置修复后保存。"

    def provider(self, identity: str | None = None) -> Provider:
        return next(p for p in self.providers if p.id == (identity or self.active))

    def selection(self) -> Selection:
        provider = copy.deepcopy(self.provider())
        return Selection(provider, provider.model, provider.effort)

    def save(self) -> None:
        self.provider()
        entries = []
        for provider in self.providers:
            provider.validate()
            entry = asdict(provider)
            entry.pop("api_key")
            entry["protected_key"] = protect_key(provider.api_key)
            entries.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix="model-settings-", dir=self.path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "active": self.active, "providers": entries},
                          stream, ensure_ascii=False, indent=2)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        self.error = ""


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> Request | None:
        old, new = urlsplit(request.full_url), urlsplit(newurl)
        if (old.scheme, old.hostname, old.port) != (new.scheme, new.hostname, new.port):
            raise ValueError("拒绝跨站重定向，避免向其他地址发送 API Key。")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def api_request(provider: Provider, endpoint: str, body: dict[str, Any] | None = None) -> Any:
    base = validate_url(provider.base_url)
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = "Bearer " + provider.api_key
    request = Request(base + endpoint,
                      None if body is None else json.dumps(body).encode("utf-8"), headers)
    try:
        with build_opener(SafeRedirect()).open(request, timeout=45) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("API 响应超过 4 MiB。")
            return json.loads(raw)
    except HTTPError as error:
        detail = error.read(4096).decode("utf-8", errors="replace")
        raise ValueError(provider.redact(f"HTTP {error.code}: {detail}")[:500]) from None
    except (URLError, TimeoutError) as error:
        raise ValueError(provider.redact(f"API 连接失败: {error}")) from None


def fetch_models(provider: Provider) -> list[str]:
    result = api_request(provider, "/models")
    for entry in result["data"]:
        modalities = entry.get("inputModalities", entry.get("input_modalities"))
        if isinstance(modalities, list) and all(isinstance(value, str) for value in modalities):
            provider.modalities[entry["id"]] = modalities
    values = model_ids([entry["id"] for entry in result["data"]])
    if not values:
        raise ValueError("供应商返回了空模型列表；可手动填写模型 ID。")
    return values


def probe_model(provider: Provider, model: str, effort: str = "") -> str:
    model_ids([model])
    if not model:
        raise ValueError("请先选择测试模型。")
    body: dict[str, Any] = {
        "model": model, "input": "Call levelcontrol_probe once with value ok.",
        "store": False, "max_output_tokens": 1024,
        "tools": [{"type": "function", "name": "levelcontrol_probe",
                   "description": "A harmless connectivity test. Does not access the game.",
                   "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                                  "required": ["value"], "additionalProperties": False}}],
        "tool_choice": {"type": "function", "name": "levelcontrol_probe"},
    }
    if effort:
        body["reasoning"] = {"effort": effort}
    result = api_request(provider, "/responses", body)
    if result.get("error"):
        raise ValueError(provider.redact(result["error"]))
    calls = [item for item in result.get("output", []) if item.get("type") == "function_call"]
    if not any(item.get("name") == "levelcontrol_probe"
               and json.loads(item.get("arguments", "{}")) == {"value": "ok"} for item in calls):
        raise ValueError("API 已响应，但没有返回预期工具调用；请检查模型的 Responses 工具兼容性。")
    return "连接成功，模型工具调用可用。测试未修改游戏。"
