"""Build the small Win64 application host using the installed MSVC and bundled Python."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_client(output: Path) -> Path:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
    vswhere /= "Microsoft Visual Studio/Installer/vswhere.exe"
    result = subprocess.run([str(vswhere), "-latest", "-products", "*", "-prerelease",
                             "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                             "-property", "installationPath"], capture_output=True,
                            text=True, check=True)
    installation = result.stdout.strip()
    if not installation:
        raise RuntimeError("MSVC x64 build tools are required to build the client host")
    vcvars = Path(installation) / "VC/Auxiliary/Build/vcvars64.bat"
    result = subprocess.run(f'call "{vcvars}" >nul && set', shell=True,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError("MSVC environment initialization failed")
    environment = dict(line.split("=", 1) for line in result.stdout.splitlines()
                       if "=" in line and not line.startswith("="))
    lookup = {key.lower(): value for key, value in environment.items()}
    compiler = Path(lookup["vctoolsinstalldir"]) / "bin/Hostx64/x64/cl.exe"
    resource = Path(lookup["windowssdkdir"]) / "bin"
    resource /= lookup["windowssdkversion"].strip("\\/") + "/x64/rc.exe"
    build = ROOT / "Intermediate/Client"
    build.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    # The icon is a deterministic rendering of the repository's original SVG.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(str(ROOT / "tools/lc/assets/levelcontrol.svg")).render(painter)
    painter.end()
    if not image.save(str(build / "LevelControl.ico"), "ICO"):
        raise RuntimeError("Could not render the client icon")
    app.processEvents()
    subprocess.run([str(resource), "/nologo", "/fo", str(build / "LevelControl.res"),
                    str(ROOT / "scripts/client/LevelControl.rc")],
                   env=environment, cwd=build, check=True)
    executable = output / "LevelControl.exe"
    subprocess.run([str(compiler), "/nologo", "/O2", "/EHsc", "/std:c++17", "/MT", "/utf-8",
                    "/DUNICODE", "/D_UNICODE", f"/Fe:{executable}",
                    f"/Fo:{build / 'LevelControlClient.obj'}",
                    str(ROOT / "scripts/client/LevelControlClient.cpp"),
                    str(build / "LevelControl.res"), "/link", "/SUBSYSTEM:WINDOWS",
                    "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker' uiAccess='false'",
                    "user32.lib", "shell32.lib"], env=environment, cwd=build, check=True)
    return executable


if __name__ == "__main__":
    print(build_client(ROOT / "dist/LevelControl"))
