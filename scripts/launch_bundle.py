"""Start the packaged game and desktop companion, preserving existing local state."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


@contextmanager
def launch_lock(port: int = 18844):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, f"Local\\LevelControl-launch-{port}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 95000) in {0, 0x80}
        if not acquired:
            raise RuntimeError("另一个客户端仍在启动，请稍后重试。")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def main() -> None:
    bundle = Path(__file__).resolve().parent
    manifest_path = bundle / "build-info.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {}
    if manifest.get("native_harness"):
        if manifest.get("semantic_core"):
            os.environ.setdefault("LC_USER_DATA_ROOT", str(Path(os.environ.get("LOCALAPPDATA", str(bundle))) / "LevelControl"))
            os.environ.setdefault("LC_PROTOCOL", "4")
            os.environ.setdefault("LC_PORT", str(manifest.get("port", 18871)))
        runtime = bundle / "Runtime/Python"
        tools = bundle / "Companion/tools"
        os.environ.update(PYTHONHOME=str(runtime), PYTHONPATH=str(tools),
                          LC_BUNDLE_ROOT=str(bundle))
        sys.path.insert(0, str(tools))
        user_root = os.environ.get("LC_USER_DATA_ROOT")
        if user_root:
            log_path = Path(user_root) / "logs"
        else:
            log_path = Path(os.environ.get("LOCALAPPDATA", str(bundle))) / "LevelControl/logs"
        log_path.mkdir(parents=True, exist_ok=True)
        with (log_path / "app.log").open("a", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                from lc.desktop import main as start_desktop

                start_desktop()
        return
    port = int(manifest.get("port", 18844))
    protocol = int(manifest.get("protocol", 2))
    width, height = manifest.get("resolution", [1280, 720])
    if not 1024 <= port <= 65535:
        raise ValueError("Invalid bundle port")
    companion = bundle / "Companion"
    runtime = bundle / "Runtime/Python"
    saved = companion / "project/Saved"
    (saved / "Logs").mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler({}))

    def healthy() -> bool:
        try:
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                health = json.load(response)
            if health.get("protocol") != protocol:
                raise RuntimeError(f"端口 {port} 上的游戏版本不匹配，请关闭该端口的旧游戏后重试。")
            if manifest.get("fast_build") and not health.get("fast_build"):
                raise RuntimeError(f"端口 {port} 上的游戏不支持快速构建，请使用本包对应的游戏。")
            if manifest.get("short_presentation") and not health.get("short_presentation"):
                raise RuntimeError(f"端口 {port} 上的游戏不支持短演出，请使用本包对应的游戏。")
            return health.get("ok") is True
        except (URLError, TimeoutError):
            return False

    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    with launch_lock(port):
        if not healthy():
            executable = bundle / "Game/Windows/ProjectLC.exe"
            if not executable.exists():
                raise FileNotFoundError("找不到打包游戏，请先执行 scripts/package.ps1。")
            game = subprocess.Popen([str(executable), "-game", "-windowed",
                                     f"-ResX={width}", f"-ResY={height}", f"-LCPort={port}",
                                     f"-LCStore={saved / 'LevelControl/entities.sqlite3'}",
                                     f"-abslog={saved / 'Logs/game.log'}"], startupinfo=startup)
            deadline = time.monotonic() + 90
            while not healthy():
                if time.monotonic() >= deadline or game.poll() not in (None, 0):
                    raise RuntimeError("游戏未能启动，请查看 "
                                       "Companion/project/Saved/Logs/game.log。")
                time.sleep(0.2)
    os.environ.update(PYTHONHOME=str(runtime), PYTHONPATH=str(companion / "tools"),
                      LC_PORT=str(port))
    sys.path.insert(0, str(companion / "tools"))
    with (saved / "Logs/companion.log").open("a", encoding="utf-8") as log:
        with redirect_stdout(log), redirect_stderr(log):
            from lc.desktop import main as start_companion

            start_companion()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        ctypes.windll.user32.MessageBoxW(None, str(error), "LevelControl 启动失败", 0x10)
        sys.exit(1)
