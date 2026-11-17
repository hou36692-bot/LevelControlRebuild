"""Background project activation used by the desktop Companion."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from .project import (
    BuildInfo,
    ProcessRegistry,
    ProjectError,
    ProjectTarget,
    ProtocolMismatch,
    detect_bundle,
    ensure_running,
    probe,
)


class ProjectController(QObject):
    status = Signal(str, int)
    connected = Signal(str, dict, int)
    failed = Signal(str, str, int)

    def __init__(self, bundle: BuildInfo | None = None,
                 registry: ProcessRegistry | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bundle = bundle or detect_bundle()
        self.registry = registry or ProcessRegistry()
        self._generation = 0
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    def activate(self, target: ProjectTarget, *, launch: bool,
                 timeout: float | None = None) -> int:
        with self._lock:
            self._cancel.set()
            self._cancel = threading.Event()
            self._generation += 1
            generation = self._generation
            cancel = self._cancel
        seconds = timeout if timeout is not None else (
            180.0 if target.kind == "uproject" else 120.0)
        threading.Thread(
            target=self._work, name=f"project-{target.id}", daemon=True,
            args=(target, launch, seconds, generation, cancel),
        ).start()
        return generation

    def _work(self, target: ProjectTarget, launch: bool, timeout: float,
              generation: int, cancel: threading.Event) -> None:
        try:
            if launch:
                result = ensure_running(
                    target, self.bundle, timeout=timeout, cancel=cancel,
                    progress=lambda text: self._emit_status(text, generation),
                    on_process=lambda process: self.registry.track(target, process),
                )
                if cancel.is_set() or result.status == "cancelled":
                    self._emit_status("已取消启动", generation)
                    return
                if result.health is not None:
                    self.connected.emit(target.id, result.health, generation)
                elif result.status == "attached":
                    self.connected.emit(target.id, {}, generation)
            else:
                health = probe(target.port, target.protocol)
                if health is None:
                    self.failed.emit(target.id, "未运行 · 点击启动", generation)
                else:
                    self.connected.emit(target.id, health, generation)
        except ProtocolMismatch as error:
            self.failed.emit(target.id, str(error), generation)
        except ProjectError as error:
            self.failed.emit(target.id, str(error), generation)
        except Exception as error:  # defensive: worker must never kill the UI
            self.failed.emit(target.id, f"项目启动失败：{error}", generation)

    def _emit_status(self, text: str, generation: int) -> None:
        self.status.emit(text, generation)

    def cancel(self) -> None:
        with self._lock:
            self._cancel.set()

    def owns(self, target: ProjectTarget) -> bool:
        return self.registry.owns(target)

    def stop_owned(self, target: ProjectTarget) -> bool:
        return self.registry.stop_owned(target)

    def latest_generation(self) -> int:
        with self._lock:
            return self._generation

    def close(self) -> None:
        self.cancel()
        self.registry.close()
