"""Install the official offline Chinese speech model as data, never executable code."""

from __future__ import annotations

import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "vosk-model-small-cn-0.22"
URL = f"https://alphacephei.com/vosk/models/{NAME}.zip"


def main() -> None:
    directory = ROOT / ".local-assets/speech"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"{NAME}.zip"
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        with urllib.request.urlopen(URL, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = directory / "manifest.json"
    if manifest.exists():
        expected = json.loads(manifest.read_text("utf-8"))["sha256"]
        if digest != expected:
            raise RuntimeError("Speech model archive no longer matches the saved SHA-256")
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            destination = (directory / member.filename).resolve()
            if not destination.is_relative_to(directory.resolve()):
                raise ValueError("Unsafe model archive path")
        source.extractall(directory)
    if not (directory / NAME / "am/final.mdl").exists():
        raise RuntimeError("Model extraction incomplete")
    manifest.write_text(
        json.dumps(
            {
                "name": NAME,
                "source": URL,
                "sha256": digest,
                "license": "Apache-2.0",
                "purpose": "offline streaming Chinese ASR",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(manifest.read_text("utf-8"))


if __name__ == "__main__":
    main()
