"""Synthesize the project's original 16-second percussion/pad composition."""

import wave
from pathlib import Path

import numpy as np


def main() -> None:
    rate, duration = 48000, 16
    sound = np.zeros(rate * duration, dtype=float)
    random = np.random.default_rng(18844)
    for beat in range(32):
        start = int(beat * 0.5 * rate)
        t = np.arange(int(0.24 * rate)) / rate
        kick = 0.70 * np.sin(2 * np.pi * (58 * t + 2 * (1 - np.exp(-25 * t)))) * np.exp(-22 * t)
        click = random.normal(0, 0.12, len(t)) * np.exp(-100 * t)
        sound[start : start + len(t)] += kick + click
        note = [261.63, 329.63, 392, 523.25, 440, 349.23, 392, 293.66][beat % 8]
        pluck = 0.10 * np.sin(2 * np.pi * note * t) * np.exp(-10 * t)
        sound[start : start + len(t)] += pluck
    chords = [
        [130.81, 164.81, 196],
        [110, 130.81, 164.81],
        [87.31, 110, 130.81],
        [98, 123.47, 146.83],
    ]
    for bar in range(8):
        t = np.arange(2 * rate) / rate
        envelope = np.minimum(t / 0.15, 1) * np.minimum((2 - t) / 0.3, 1)
        pad = sum(np.sin(2 * np.pi * f * t) for f in chords[bar % 4]) * envelope * 0.025
        sound[bar * 2 * rate : (bar + 1) * 2 * rate] += pad
    pcm = (np.clip(sound, -0.96, 0.96) * 32767).astype("<i2")
    directory = Path(__file__).resolve().parents[1] / "ue/Plugins/LevelControlRuntime/Resources"
    directory.mkdir(parents=True, exist_ok=True)
    with wave.open(str(directory / "architect-pulse.wav"), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm.tobytes())
    from presenter.beats import save_analysis

    save_analysis()


if __name__ == "__main__":
    main()
