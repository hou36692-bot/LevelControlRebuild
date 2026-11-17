"""Local streaming speech recognition; microphone data is never uploaded or saved."""

from __future__ import annotations

import json
import re
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyaudiowpatch as pyaudio
from vosk import KaldiRecognizer, Model, SetLogLevel

from .client import ROOT

MODEL = ROOT / ".local-assets/speech/vosk-model-small-cn-0.22"


def chinese_text(text: str) -> str:
    return re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text).strip()


class Decoder:
    def __init__(self, model: Model, callback: Callable[[str], None] | None = None) -> None:
        self.recognizer = KaldiRecognizer(model, 16000)
        self.callback = callback or (lambda text: None)
        self.segments: list[str] = []
        self.last_partial = ""

    def feed(self, pcm: bytes) -> None:
        if self.recognizer.AcceptWaveform(pcm):
            text = json.loads(self.recognizer.Result()).get("text", "")
            if text:
                self.segments.append(text)
            partial = " ".join(self.segments)
        else:
            partial = " ".join(
                self.segments + [json.loads(self.recognizer.PartialResult()).get("partial", "")]
            )
        partial = chinese_text(partial)
        if partial != self.last_partial:
            self.callback(partial)
            self.last_partial = partial

    def finish(self) -> str:
        final = json.loads(self.recognizer.FinalResult()).get("text", "")
        return chinese_text(" ".join(self.segments + [final]))


class Speech:
    def __init__(self, model_path: Path = MODEL) -> None:
        if not model_path.exists():
            raise FileNotFoundError("Run scripts/bootstrap_voice.py to install the Chinese model")
        SetLogLevel(-1)
        self.model = Model(str(model_path))
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.released_at: float | None = None

    def transcribe_file(
        self, path: Path, realtime: bool = False, partial: Callable[[str], None] | None = None
    ) -> dict[str, Any]:
        decoder = Decoder(self.model, partial)
        with wave.open(str(path), "rb") as source:
            if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (
                16000,
                1,
                2,
            ):
                raise ValueError("Speech fixtures must be mono 16 kHz PCM16")
            duration = source.getnframes() / 16000
            started = time.perf_counter()
            frames = 0
            while pcm := source.readframes(800):
                decoder.feed(pcm)
                frames += len(pcm) / 2
                if realtime:
                    time.sleep(max(0, started + frames / 16000 - time.perf_counter()))
        ended = time.perf_counter()
        text = decoder.finish()
        ready = time.perf_counter()
        return {
            "text": text,
            "audio_s": duration,
            "processing_s": time.perf_counter() - started,
            "finalization_s": time.perf_counter() - ended,
            "speech_ended_at": started + duration if realtime else ended,
            "text_ready_at": ready,
            "release_to_text_s": ready - (started + duration if realtime else ended),
        }

    def start(
        self,
        partial: Callable[[str], None],
        complete: Callable[[dict[str, Any]], None],
        device: int | None = None,
    ) -> None:
        if self.thread and self.thread.is_alive():
            raise RuntimeError("Already recording")
        self.stopping.clear()
        self.released_at = None

        def capture() -> None:
            decoder = Decoder(self.model, partial)
            started = time.perf_counter()
            frames = 0
            try:
                with pyaudio.PyAudio() as audio:
                    stream = audio.open(
                        format=pyaudio.paInt16,
                        channels=1,
                        rate=16000,
                        input=True,
                        input_device_index=device,
                        frames_per_buffer=800,
                    )
                    try:
                        while not self.stopping.is_set() and time.perf_counter() - started < 30:
                            pcm = stream.read(800, exception_on_overflow=False)
                            frames += len(pcm) // 2
                            decoder.feed(pcm)
                    finally:
                        stream.stop_stream()
                        stream.close()
                released = time.perf_counter()
                text = decoder.finish()
                complete(
                    {
                        "text": text,
                        "audio_s": frames / 16000,
                        "finalization_s": time.perf_counter() - released,
                        "release_to_text_s": time.perf_counter() - (self.released_at or released),
                    }
                )
            except Exception as error:
                complete({"text": "", "error": str(error)})

        self.thread = threading.Thread(target=capture, name="local-speech", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.released_at is None:
            self.released_at = time.perf_counter()
        self.stopping.set()
