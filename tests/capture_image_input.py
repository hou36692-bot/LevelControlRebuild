"""Render the shipped desktop layout with four local reference images."""

import os
import queue
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from lc import desktop
from lc.client import ROOT
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication


class Backend:
    def __init__(self, *args):
        self.queue = queue.Queue()
        self.speech = None

    def close(self):
        pass

    def control(self, command):
        pass


def main():
    directory = ROOT / "tests/reports/image-input"
    desktop.Backend = Backend
    app = QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    with tempfile.TemporaryDirectory() as settings:
        os.environ["LC_MODEL_SETTINGS"] = str(Path(settings) / "models.json")
        window = desktop.Companion()
        window.setStyleSheet(window.styleSheet().replace("Microsoft YaHei UI", "Microsoft YaHei"))
        window.tray = None
        window.ready = window.online = True
        references = [window.references.store.prepare(directory / name)
                      for name in ("facade.png", "layout.png", "facade.png", "layout.png")]
        window.references.images = tuple(references)
        window.references.mark_submitted()
        window.user_text.setText("按参考图搭建礼堂，保留入口和中央通道。")
        window.ai_text.setText("已完成。后续可以继续引用当前参考图修改。")
        window.input.setText("把两扇窗改成深蓝色")
        window.show()
        app.processEvents()
        window.grab().save(str(directory / "desktop.png"))
        print(window.size().width(), window.size().height())
        window.close()
        app.processEvents()


if __name__ == "__main__":
    main()
