"""Exercise real vision and scene edits in a private Shipping world."""

import json
import os
import shutil
import subprocess
import time
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lc.client import ROOT, Client
from lc.images import ImageStore, safe_image_log
from lc.resident import ResidentAgent
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPolygon
from PySide6.QtWidgets import QApplication


def references(directory):
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    paths = []
    for layout in (False, True):
        image = QImage(960, 720, QImage.Format.Format_RGB32)
        image.fill(QColor("#edf0ee"))
        painter = QPainter(image)
        painter.setPen(QColor("#202c35"))
        painter.setFont(QFont("Microsoft YaHei", 22))
        painter.drawText(40, 55, "平面布局参考" if layout else "建筑正面参考")
        if not layout:
            painter.fillRect(QRect(190, 290, 580, 310), QColor("#faf6e9"))
            painter.setBrush(QColor("#b83e32"))
            painter.drawPolygon(QPolygon([QPoint(150, 300), QPoint(480, 120), QPoint(810, 300)]))
            painter.fillRect(QRect(245, 355, 110, 105), QColor("#5499c6"))
            painter.fillRect(QRect(605, 355, 110, 105), QColor("#5499c6"))
            painter.fillRect(QRect(425, 410, 110, 190), QColor("#263841"))
            painter.drawText(220, 665, "红色双坡屋顶 / 米白墙 / 两扇蓝窗 / 中央入口")
        else:
            painter.fillRect(QRect(180, 140, 600, 440), QColor("#faf6e9"))
            painter.drawRect(QRect(180, 140, 600, 440))
            for y in (210, 320, 430):
                for x in (245, 570):
                    painter.fillRect(QRect(x, y, 130, 45), QColor("#a67a51"))
            painter.fillRect(QRect(440, 555, 80, 60), QColor("#edf0ee"))
            painter.drawText(240, 665, "左右各三排长椅 / 中央通道 / 下方入口")
        painter.end()
        path = directory / ("layout.png" if layout else "facade.png")
        assert image.save(str(path))
        paths.append(path)
    return paths


def main():
    QApplication.instance() or QApplication([])
    port = 18863
    client = Client(port, timeout=1)
    try:
        client._request("/health")
    except ConnectionError:
        pass
    else:
        raise RuntimeError("Isolated port is already occupied")
    output = ROOT / "tests/reports/image-input"
    output.mkdir(parents=True, exist_ok=True)
    facade, layout = references(output)
    store = ImageStore()
    first, second = store.prepare(facade), store.prepare(layout)
    database = ROOT / f"project/Saved/Tests/images-{uuid.uuid4().hex}.sqlite3"
    executable = ROOT / ("dist/LevelControlFast/Game/Windows/ProjectLC/Binaries/Win64/"
                         "ProjectLC-Win64-Shipping.exe")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen([str(executable), "-game", "-windowed", "-ResX=1280",
                                "-ResY=720", f"-LCPort={port}", f"-LCStore={database}",
                                f"-abslog={output / 'game.log'}"], startupinfo=startup)
    report = {"passed": False, "port": port, "database": str(database), "runs": []}
    try:
        deadline = time.perf_counter() + 60
        while time.perf_counter() < deadline:
            try:
                if client._request("/health").get("ok"):
                    break
            except ConnectionError:
                time.sleep(0.25)
        client.timeout = 30
        assert client.read("objects * --count")["count"] == 0
        with ResidentAgent(port=port, model="gpt-6-astra", effort="low") as agent:
            cases = [
                ("", (first,), False),
                ("按参考图在我前方建一座小礼堂，保留红色双坡屋顶、米白墙、左右蓝窗和中央可通行入口。"
                 "只做主要结构，最多50个构件。", (first,), True),
                ("沿用刚才参考图，把刚建礼堂的两扇窗改成更深的蓝色，其余不变。", (first,), True),
                ("仅比较这两张参考图，说明正面特征和室内布局，不修改场景。",
                 (first, second), False),
                ("不再使用参考图。把之前那座礼堂的墙改成浅灰色，保留屋顶、窗和其他结构。",
                 (), True),
            ]
            for index, (prompt, images, writes) in enumerate(cases):
                before = client.read("objects * --count")
                result = agent.turn(prompt, images=images)
                after = client.read("objects * --count")
                row = {"case": index, "prompt": prompt, "before": before, "after": after,
                       "turn": safe_image_log(result)}
                report["runs"].append(row)
                (output / "agent.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                   "utf-8")
                print(json.dumps({"case": index, "status": result["status"],
                                  "seconds": result["elapsed_s"], "count": after["count"],
                                  "final": result["final"]}, ensure_ascii=False), flush=True)
                assert result["status"] == "completed", result
                if writes:
                    assert after["version"] > before["version"], result
                else:
                    assert after == before, result
                if index == 1:
                    capture = client.capture("image-input-built.png")
                    row["capture"] = capture
                if index == 0:
                    assert "红" in result["final"] and "蓝" in result["final"]
                if index == 3:
                    assert "长椅" in result["final"] and "通道" in result["final"]
            report["consistency"] = client.read("consistency")
            assert report["consistency"]["ok"]
            report["passed"] = True
            (output / "agent.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                               "utf-8")
            shutil.copy2(agent.event_path, output / "resident.jsonl")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(15)


if __name__ == "__main__":
    main()
