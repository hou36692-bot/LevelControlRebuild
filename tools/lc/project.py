"""UE project targets, validation, launch plans and process ownership."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from .client import ROOT
from .settings_io import base_directory, default_path, load_versioned, save_atomic

KINDS = {"bundled", "uproject", "exe", "attach"}
PROTOCOLS = {2, 3}
DEFAULT_PORT = 18870
RESERVED_PORTS = {18844, 18848}
PLUGIN_NAME = "LevelControlRuntime"


class ProjectError(RuntimeError):
    pass


class ProtocolMismatch(ProjectError):
    pass


@dataclass
class ProjectTarget:
    id: str
    kind: str
    name: str
    path: str = ""
    port: int = DEFAULT_PORT
    protocol: int = 3
    engine: str = ""
    store: str = ""
    design_file: str = ""
    width: int = 1280
    height: int = 720

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "name": self.name, "path": self.path,
            "port": self.port, "protocol": self.protocol, "engine": self.engine,
            "store": self.store, "design_file": self.design_file,
            "width": self.width, "height": self.height,
        }


@dataclass
class BuildInfo:
    root: Path
    companion: Path
    game_exe: Path
    build_info: Path
    port: int = DEFAULT_PORT
    protocol: int = 3
    width: int = 1920
    height: int = 1080
    error: str = ""


@dataclass
class TargetStatus:
    target: ProjectTarget
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    store: Path | None = None
    design_file: Path | None = None
    engine: Path | None = None
    plugin: Path | None = None
    executable: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class LaunchSpec:
    command: list[str]
    cwd: Path
    store: Path
    log: Path
    design_file: Path
    port: int
    protocol: int


@dataclass
class LaunchResult:
    status: str
    health: dict[str, Any] | None = None
    process: subprocess.Popen[str] | None = None
    message: str = ""


def detect_bundle() -> BuildInfo:
    configured = os.environ.get("LC_BUNDLE_ROOT")
    root = Path(configured) if configured else (
        ROOT.parent if ROOT.name == "Companion" else ROOT.parent)
    companion = root / "Companion" if (root / "Companion").exists() else ROOT
    info = root / "build-info.json"
    data: dict[str, Any] = {}
    error = ""
    if info.exists():
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            error = "build-info.json 读取失败，使用目标默认端口/协议。"
    port = int(data.get("port", os.environ.get("LC_PORT", DEFAULT_PORT)))
    protocol = int(data.get("protocol", os.environ.get("LC_PROTOCOL", 3)))
    resolution = data.get("resolution", [1920, 1080])
    width, height = (resolution if isinstance(resolution, list) and len(resolution) == 2
                     else [1920, 1080])
    return BuildInfo(root, companion, root / "Game/Windows/ProjectLC.exe", info,
                     port, protocol, int(width), int(height), error)


def target_id(kind: str, path: str, port: int) -> str:
    if kind == "bundled":
        return "bundled"
    if kind == "attach":
        return f"attach:{port}"
    digest = hashlib.sha1(str(Path(path).resolve()).casefold().encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}"


class ProjectSettings:
    def __init__(self, path: Path | None = None, bundle: BuildInfo | None = None) -> None:
        self.path = path or default_path("project-settings.json", "LC_PROJECT_SETTINGS")
        self.bundle = bundle or detect_bundle()
        self.active = ""
        self.targets: list[ProjectTarget] = []
        self.recent: list[str] = []
        self.error = ""
        data = load_versioned(self.path)
        if data is None:
            if self.path.exists():
                self.error = "项目设置读取失败，原文件已保留；已使用默认项目。"
            self._seed()
            return
        try:
            self.active = str(data["active"])
            self.targets = [self._target(item) for item in data.get("targets", [])]
            self.recent = [str(item) for item in data.get("recent", [])][:8]
        except (KeyError, TypeError, ValueError):
            self.error = "项目设置内容无效；已使用默认项目。"
            self.targets, self.active, self.recent = [], "", []
            self._seed()
            return
        if not self.targets or self.active not in {item.id for item in self.targets}:
            self._seed()
        try:
            self._validate_all()
        except ProjectError as error:
            self.error = f"项目设置无效：{error}；已使用默认项目。"
            self.targets, self.active, self.recent = [], "", []
            self._seed()
        self.recent = [item for item in self.recent
                       if item in {target.id for target in self.targets}]

    def _target(self, item: dict[str, Any]) -> ProjectTarget:
        kind = str(item["kind"])
        path = str(item.get("path", ""))
        port = int(item.get("port", DEFAULT_PORT))
        target = ProjectTarget(
            id=str(item.get("id") or target_id(kind, path, port)), kind=kind,
            name=str(item["name"]), path=path, port=port,
            protocol=int(item.get("protocol", 3)), engine=str(item.get("engine", "")),
            store=str(item.get("store", "")), design_file=str(item.get("design_file", "")),
            width=int(item.get("width", 1280)), height=int(item.get("height", 720)),
        )
        validate_fields(target)
        return target

    def _seed(self) -> None:
        if self.bundle.game_exe.exists():
            self.targets = [ProjectTarget(
                "bundled", "bundled", "ProjectLC（随客户端）",
                port=self.bundle.port, protocol=self.bundle.protocol,
                width=self.bundle.width, height=self.bundle.height)]
        else:
            project = ROOT / "project/ProjectLC.uproject"
            if project.exists():
                port = int(os.environ.get("LC_PORT", DEFAULT_PORT))
                self.targets = [ProjectTarget(
                    target_id("uproject", str(project), port),
                    "uproject", "ProjectLC（开发）", str(project),
                    port=port, protocol=int(os.environ.get("LC_PROTOCOL", 3)))]
        self.active = self.targets[0].id if self.targets else ""

    def active_target(self) -> ProjectTarget | None:
        return next((item for item in self.targets if item.id == self.active), None)

    def by_id(self, identity: str) -> ProjectTarget | None:
        return next((item for item in self.targets if item.id == identity), None)

    def upsert(self, target: ProjectTarget) -> None:
        validate_fields(target)
        self.targets = [item for item in self.targets if item.id != target.id]
        self.targets.append(target)
        self.recent = [target.id] + [item for item in self.recent if item != target.id][:7]

    def remove(self, identity: str) -> None:
        self.targets = [item for item in self.targets if item.id != identity]
        self.recent = [item for item in self.recent if item != identity]
        if self.active == identity:
            self.active = self.targets[0].id if self.targets else ""

    def save(self) -> None:
        self._validate_all()
        save_atomic(self.path, {
            "version": 1, "active": self.active, "recent": self.recent,
            "targets": [item.as_dict() for item in self.targets],
        })
        self.error = ""

    def _validate_all(self) -> None:
        seen_ids, seen_ports = set(), set()
        for target in self.targets:
            validate_fields(target)
            if target.id in seen_ids:
                raise ProjectError(f"项目 ID 重复：{target.id}")
            seen_ids.add(target.id)
            if target.kind != "attach":
                if target.port in seen_ports:
                    raise ProjectError(f"端口 {target.port} 已被其他启动目标使用。")
                seen_ports.add(target.port)


def validate_fields(target: ProjectTarget) -> None:
    if target.kind not in KINDS:
        raise ProjectError("项目类型无效。")
    target.name = target.name.strip()
    if not 1 <= len(target.name) <= 60:
        raise ProjectError("项目名称需为 1–60 个字符。")
    if isinstance(target.port, bool) or not 1024 <= target.port <= 65535:
        raise ProjectError("端口需在 1024–65535。")
    if target.protocol not in PROTOCOLS:
        raise ProjectError("协议版本必须是 2 或 3。")
    if not 640 <= target.width <= 3840 or not 480 <= target.height <= 2160:
        raise ProjectError("游戏分辨率超出范围。")
    if target.kind == "uproject" and Path(target.path).suffix.lower() != ".uproject":
        raise ProjectError("请选择 .uproject 文件。")
    if target.kind == "exe" and Path(target.path).suffix.lower() != ".exe":
        raise ProjectError("请选择打包游戏 .exe。")
    if target.kind == "attach":
        target.path = target.engine = target.store = target.design_file = ""


def probe(port: int, expected_protocol: int | None = None, timeout: float = 0.4
          ) -> dict[str, Any] | None:
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            raw = response.read(64 * 1024 + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if len(raw) > 64 * 1024:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("ok") is not True:
        return None
    actual = data.get("protocol")
    if expected_protocol is not None and actual != expected_protocol:
        raise ProtocolMismatch(
            f"端口 {port} 上的游戏协议为 {actual}，期望 {expected_protocol}；"
            "请使用匹配的客户端/游戏版本。")
    return data


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port(preferred: int = 18849, used: Iterable[int] = ()) -> int:
    taken = set(used)
    for port in range(max(1024, preferred), 19000):
        if port in taken or not port_free(port):
            continue
        return port
    raise ProjectError("没有找到可用端口。")


def store_for(target: ProjectTarget, bundle: BuildInfo) -> Path | None:
    if target.kind == "attach":
        return None
    if target.store:
        return Path(target.store)
    if target.kind == "bundled":
        return bundle.companion / "project/Saved/LevelControl/entities.sqlite3"
    folder = "".join(character if character.isalnum() or character in "-._" else "-"
                     for character in target.id)
    destination = base_directory() / "projects" / folder / "entities.sqlite3"
    legacy = base_directory().parent / "LevelControl3/projects" / folder / "entities.sqlite3"
    # Reuse existing worlds in place; copying a live SQLite store is not a migration.
    return legacy if not destination.exists() and legacy.exists() else destination


def design_for(target: ProjectTarget, bundle: BuildInfo) -> Path | None:
    if target.design_file:
        return Path(target.design_file)
    if target.kind in {"bundled", "attach"}:
        return bundle.companion / f"project/Saved/LevelControl/design-{target.port}.json"
    store = store_for(target, bundle)
    return store.parent / "design.json" if store else None


def _plugin_candidates(project: Path, data: dict[str, Any], bundle: BuildInfo) -> list[Path]:
    result = [project / "Plugins" / PLUGIN_NAME / f"{PLUGIN_NAME}.uplugin"]
    for entry in data.get("AdditionalPluginDirectories", []):
        value = str(entry).replace("$(ProjectDir)", str(project))
        value = value.replace("$(EngineDir)", str(find_editor(project, "") or ""))
        result.append((project / value).resolve() / PLUGIN_NAME / f"{PLUGIN_NAME}.uplugin")
    result.append(bundle.companion / "ue/Plugins" / PLUGIN_NAME / f"{PLUGIN_NAME}.uplugin")
    result.append(ROOT / "ue/Plugins" / PLUGIN_NAME / f"{PLUGIN_NAME}.uplugin")
    return result


def _read_uproject(project: Path) -> dict[str, Any]:
    if not project.is_file() or project.suffix.lower() != ".uproject":
        raise ProjectError("请选择有效的 .uproject 文件。")
    if project.stat().st_size > 1024 * 1024:
        raise ProjectError(".uproject 文件过大。")
    try:
        data = json.loads(project.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProjectError(f".uproject 读取失败：{error}") from error
    if not isinstance(data, dict):
        raise ProjectError(".uproject 内容无效。")
    return data


def inspect_uproject(
    project: Path, bundle: BuildInfo
) -> tuple[dict[str, Any], Path | None, list[str], list[str]]:
    data = _read_uproject(project)
    errors: list[str] = []
    warnings: list[str] = []
    disabled = False
    enabled = False
    for item in data.get("Plugins", []):
        if isinstance(item, dict) and item.get("Name") == PLUGIN_NAME:
            enabled = bool(item.get("Enabled"))
            disabled = not enabled
    plugin = next(
        (path for path in _plugin_candidates(project, data, bundle) if path.is_file()), None)
    if disabled:
        errors.append("LevelControlRuntime 在 .uproject 中被禁用；请启用后重试。")
    elif enabled and plugin is None:
        errors.append("项目声明启用了 LevelControlRuntime，但磁盘上没有插件文件。")
    elif plugin is None:
        errors.append("项目未安装 LevelControlRuntime 插件；可复制插件后重新编译。")
    if plugin is not None:
        try:
            descriptor = json.loads(plugin.read_text(encoding="utf-8"))
            modules = descriptor.get("Modules", [])
            has_module = any(item.get("Name") == PLUGIN_NAME
                             for item in modules if isinstance(item, dict))
            if not has_module:
                errors.append("LevelControlRuntime.uplugin 缺少 Runtime 模块。")
            deps = descriptor.get("Plugins", [])
            has_sqlite = any(item.get("Name") == "SQLiteCore"
                             for item in deps if isinstance(item, dict))
            if not has_sqlite:
                warnings.append("插件描述未声明 SQLiteCore 依赖。")
        except (OSError, ValueError):
            errors.append("LevelControlRuntime.uplugin 读取失败。")
    association = str(data.get("EngineAssociation", ""))
    if association and not association.startswith("5.8"):
        warnings.append(f"项目引擎 {association} 与本机 UE 5.8 插件版本可能不一致。")
    return data, plugin, errors, warnings


def find_editor(project: Path | None = None, override: str = "") -> Path | None:
    configured = override or os.environ.get("LC_UNREAL_EDITOR", "")
    if configured:
        path = Path(configured)
        if path.is_dir():
            path = path / "Engine/Binaries/Win64/UnrealEditor.exe"
        return path if path.is_file() else None
    association = ""
    if project and project.is_file():
        try:
            association = str(json.loads(project.read_text(encoding="utf-8"))
                              .get("EngineAssociation", ""))
        except (OSError, ValueError):
            association = ""
    candidates: list[Path] = []
    try:
        import winreg

        if association:
            key = r"Software\Epic Games\Unreal Engine\Builds"
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                    root, _ = winreg.QueryValueEx(handle, association)
                    candidates.append(Path(root) / "Engine/Binaries/Win64/UnrealEditor.exe")
            except OSError:
                pass
            for hive in (winreg.HKEY_LOCAL_MACHINE,):
                for prefix in (r"SOFTWARE\EpicGames\Unreal Engine",
                               r"SOFTWARE\WOW6432Node\EpicGames\Unreal Engine"):
                    try:
                        with winreg.OpenKey(hive, prefix + "\\" + association) as handle:
                            root, _ = winreg.QueryValueEx(handle, "InstalledDirectory")
                            candidates.append(Path(root) / "Engine/Binaries/Win64/UnrealEditor.exe")
                    except OSError:
                        pass
    except ImportError:
        pass
    launcher = Path("C:/ProgramData/Epic/UnrealEngineLauncher/LauncherInstalled.dat")
    if association and launcher.exists():
        try:
            data = json.loads(launcher.read_text(encoding="utf-8-sig"))
            for item in data.get("InstallationList", []):
                if item.get("AppName") == f"UE_{association}":
                    candidates.append(Path(item["InstallLocation"])
                                      / "Engine/Binaries/Win64/UnrealEditor.exe")
        except (OSError, ValueError, KeyError, TypeError):
            pass
    roots = [Path(f"C:/Program Files/Epic Games/UE_{association}")]
    if os.environ.get("UE_ROOT"):
        roots.insert(0, Path(os.environ["UE_ROOT"]))
    for root in roots:
        candidates.append(root / "Engine/Binaries/Win64/UnrealEditor.exe")
    return next((path for path in candidates if path.is_file()), None)


def validate_target(target: ProjectTarget, bundle: BuildInfo) -> TargetStatus:
    status = TargetStatus(target)
    status.store = store_for(target, bundle)
    status.design_file = design_for(target, bundle)
    try:
        validate_fields(target)
    except ProjectError as error:
        status.errors.append(str(error))
        return status
    if target.kind == "bundled":
        status.executable = bundle.game_exe
        if not bundle.game_exe.is_file():
            status.errors.append("发行包内置游戏不存在。")
        if bundle.error:
            status.warnings.append(bundle.error)
    elif target.kind == "uproject":
        project = Path(target.path)
        try:
            _, status.plugin, errors, warnings = inspect_uproject(project, bundle)
            status.errors.extend(errors)
            status.warnings.extend(warnings)
        except ProjectError as error:
            status.errors.append(str(error))
        status.engine = find_editor(project, target.engine)
        if status.engine is None:
            status.errors.append("未找到匹配的 UnrealEditor；请选择编辑器路径。")
    elif target.kind == "exe":
        executable = Path(target.path)
        status.executable = executable
        if not executable.is_file():
            status.errors.append("游戏 EXE 不存在。")
        if executable.name.lower() in {"levelcontrol.exe", "unrealeditor.exe"}:
            status.errors.append("不能把客户端或编辑器选作游戏目标。")
    if target.kind != "attach" and status.store is None:
        status.errors.append("无法确定世界数据目录。")
    return status


def _log_tail(path: Path, lines: int = 40) -> str:
    try:
        raw = path.read_bytes()[-64 * 1024:]
    except OSError:
        return ""
    text = raw.decode("utf-16-le" if b"\x00" in raw[:64] else "utf-8", "replace")
    return "\n".join(text.splitlines()[-lines:])


def sanitized_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in ["PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUTF8",
                 "PYTHONIOENCODING", "QT_QPA_PLATFORM"]:
        environment.pop(name, None)
    for name in list(environment):
        if name.startswith(("LC_", "TCODEX_", "CODEX_")):
            environment.pop(name, None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def actual_executable(executable: Path) -> tuple[Path, Path]:
    """Resolve UE's tiny launcher exe to the Shipping exe it would start.

    Owning the real process lets ``停止游戏进程`` work; the selected path in the
    project remains the user-facing launcher.
    """
    shipping = (executable.parent / executable.stem / "Binaries/Win64"
                / f"{executable.stem}-Win64-Shipping.exe")
    if shipping.is_file():
        return shipping, shipping.parent
    return executable, executable.parent


def launch_spec(target: ProjectTarget, bundle: BuildInfo) -> LaunchSpec:
    status = validate_target(target, bundle)
    if not status.ok:
        raise ProjectError("；".join(status.errors))
    assert status.store is not None and status.design_file is not None
    store, design = status.store, status.design_file
    log = store.parent / "game.log"
    store.parent.mkdir(parents=True, exist_ok=True)
    common = ["-game", "-windowed", f"-ResX={target.width}", f"-ResY={target.height}",
              f"-LCPort={target.port}", f"-LCStore={store}", f"-abslog={log}"]
    if target.kind == "bundled":
        executable, cwd = actual_executable(bundle.game_exe)
        command = [str(executable), *common]
    elif target.kind == "uproject":
        assert status.engine is not None
        command = [str(status.engine), str(Path(target.path)), *common,
                   "-nosplash", "-DisablePython"]
        cwd = Path(target.path).parent
    elif target.kind == "exe":
        assert status.executable is not None
        executable, cwd = actual_executable(status.executable)
        command = [str(executable), *common]
    else:
        raise ProjectError("仅连接目标不能启动游戏。")
    return LaunchSpec(command, cwd, store, log, design, target.port, target.protocol)


def ensure_running(target: ProjectTarget, bundle: BuildInfo, *, timeout: float = 120.0,
                   cancel: threading.Event | None = None,
                   progress: Callable[[str], None] | None = None,
                   on_process: Callable[[subprocess.Popen[str]], None] | None = None
                   ) -> LaunchResult:
    report = progress or (lambda _text: None)
    if target.kind == "attach":
        health = probe(target.port, target.protocol)
        if health is None:
            raise ProjectError(f"附加模式不会启动游戏；端口 {target.port} 没有可连接的游戏。")
        return LaunchResult("attached", health, None, "已连接")
    health = probe(target.port, target.protocol)
    if health is not None:
        return LaunchResult("reused", health, None, "复用已运行的游戏")
    spec = launch_spec(target, bundle)
    report(f"正在启动 {target.name}…")
    process = subprocess.Popen(spec.command, cwd=spec.cwd, env=sanitized_environment(),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    if on_process is not None:
        on_process(process)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            return LaunchResult("cancelled", None, process, "已取消等待")
        try:
            health = probe(target.port, target.protocol, timeout=0.4)
        except ProtocolMismatch:
            raise
        if health is not None:
            return LaunchResult("started", health, process, "已启动")
        if process.poll() is not None:
            tail = _log_tail(spec.log)
            raise ProjectError(f"游戏进程已退出（代码 {process.returncode}）。"
                               f"日志：{spec.log}\n{tail}")
        if cancel is not None:
            cancel.wait(0.2)
        else:
            time.sleep(0.2)
    tail = _log_tail(spec.log)
    raise ProjectError(f"启动超时（{int(timeout)} 秒）。日志：{spec.log}\n{tail}")


class ProcessRegistry:
    def __init__(self) -> None:
        self._owned: dict[str, subprocess.Popen[str]] = {}

    def track(self, target: ProjectTarget, process: subprocess.Popen[str]) -> None:
        self._owned[target.id] = process

    def owns(self, target: ProjectTarget) -> bool:
        process = self._owned.get(target.id)
        return process is not None and process.poll() is None

    def stop_owned(self, target: ProjectTarget) -> bool:
        process = self._owned.pop(target.id, None)
        if process is None or process.poll() is not None:
            return False
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        return True

    def close(self) -> None:
        self._owned.clear()


def install_plugin(project: Path, source: Path) -> Path:
    if project.suffix.lower() != ".uproject" or not project.is_file():
        raise ProjectError("请选择有效的 .uproject 文件。")
    destination = project.parent / "Plugins" / PLUGIN_NAME
    if destination.exists():
        raise ProjectError(f"项目已存在插件：{destination}（未覆盖）。")
    if not (source / f"{PLUGIN_NAME}.uplugin").is_file():
        raise ProjectError("客户端没有可用的插件源。")
    ignore = shutil.ignore_patterns("Binaries", "Intermediate", "Saved", "*.pdb", "*.obj")
    temporary = destination.with_name(destination.name + ".tmp-" + uuid.uuid4().hex)
    shutil.copytree(source, temporary, ignore=ignore)
    temporary.replace(destination)
    return destination


def enable_plugin(project: Path) -> str:
    """Add LevelControlRuntime to the .uproject, keeping a .bak backup."""
    data = _read_uproject(project)
    plugins = data.setdefault("Plugins", [])
    for item in plugins:
        if isinstance(item, dict) and item.get("Name") == PLUGIN_NAME:
            item["Enabled"] = True
            break
    else:
        plugins.append({"Name": PLUGIN_NAME, "Enabled": True})
    backup = project.with_suffix(project.suffix + ".bak")
    if backup.exists():
        backup = project.with_suffix(project.suffix + "." + uuid.uuid4().hex[:8] + ".bak")
    shutil.copy2(project, backup)
    project.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"已启用插件；备份：{backup}"
