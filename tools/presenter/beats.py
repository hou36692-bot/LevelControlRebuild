"""Analyze PCM transients and quantize construction to the detected beat grid."""

from __future__ import annotations

import functools
import hashlib
import json
import math
import wave
from pathlib import Path
from typing import Any

RESOURCE = Path(__file__).resolve().parents[2] / "ue/Plugins/LevelControlRuntime/Resources"


def analyze(path: Path) -> dict[str, Any]:
    import numpy as np

    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise ValueError("Beat analysis requires mono PCM16")
        rate = source.getframerate()
        audio = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(float)
    hop = rate // 200
    if len(audio) < rate // 2 or np.max(np.abs(audio)) < 2:
        raise ValueError("No reliable rhythmic transients found")
    energy = np.sqrt(np.mean(audio[: len(audio) // hop * hop].reshape(-1, hop) ** 2, axis=1))
    novelty = np.maximum(np.diff(energy, prepend=0), 0)
    if novelty.max() < 1:
        raise ValueError("No reliable rhythmic transients found")
    candidates = np.flatnonzero(novelty >= novelty.max() * 0.25)
    beats = []
    for index in candidates:
        timestamp = float(index * hop / rate)
        if not beats or timestamp - beats[-1] >= 0.2:
            beats.append(timestamp)
    if len(beats) < 4:
        raise ValueError("No reliable rhythmic transients found")
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "beats_s": beats,
        "duration_s": len(audio) / rate,
        "sample_rate": rate,
        "median_period_s": float(np.median(np.diff(beats))),
        "method": "5 ms RMS onset novelty",
    }


@functools.lru_cache(maxsize=1)
def built_in() -> dict[str, Any]:
    result = json.loads((RESOURCE / "architect-pulse.beats.json").read_text("utf-8"))
    if hashlib.sha256((RESOURCE / "architect-pulse.wav").read_bytes()).hexdigest() != result.get(
        "sha256"
    ):
        raise ValueError("Music changed; run scripts/create_music.py to regenerate its analysis")
    return result


def timed(entities: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    track = built_in()
    cycle = track["duration_s"]
    beats = [
        beat + repeat * cycle
        for repeat in range(math.ceil((duration + 2) / cycle))
        for beat in track["beats_s"]
    ]
    flight = min(0.8, duration / 2)
    result = []
    for index, entity in enumerate(entities):
        target = flight + index * (duration - flight) / max(len(entities) - 1, 1)
        beat = next(value for value in beats if value + 1e-8 >= target)
        result.append({"id": entity["id"], "start_s": beat - flight, "duration_s": flight})
    return result


def save_analysis() -> None:
    (RESOURCE / "architect-pulse.beats.json").write_text(
        json.dumps(analyze(RESOURCE / "architect-pulse.wav"), indent=2), "utf-8"
    )
