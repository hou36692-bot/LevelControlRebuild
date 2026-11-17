"""Measure actual WASAPI loopback transients against game landing events."""

from __future__ import annotations

import argparse
import json
import threading
import time
import wave

import numpy as np
import pyaudiowpatch as audio
from lc.client import ROOT, Client
from test_w23_live import completed, make_design, terminal


def main(port: int = 18846) -> None:
    client = Client(port=port)
    assert client.read("objects * --count")["count"] == 0
    service = make_design(client, "audio-check")
    samples = []
    first_stamp = []
    lock = threading.Lock()
    offsets = []
    for _ in range(8):
        start = time.perf_counter()
        remote = client.read("clock")["clock_s"]
        end = time.perf_counter()
        offsets.append((end - start, remote - (start + end) / 2))
    rtt, offset = min(offsets)
    cursor = client.read("events --since 0")["next"]
    with audio.PyAudio() as system:
        device = system.get_default_wasapi_loopback()
        rate = int(device["defaultSampleRate"])
        channels = device["maxInputChannels"]

        def callback(data, count, timing, status):
            with lock:
                if not first_stamp:
                    first_stamp.append(
                        time.perf_counter()
                        + timing["input_buffer_adc_time"]
                        - timing["current_time"]
                    )
                samples.append(data)
            return None, audio.paContinue

        stream = system.open(
            format=audio.paFloat32,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=device["index"],
            frames_per_buffer=512,
            stream_callback=callback,
        )
        try:
            time.sleep(0.15)
            request = service.invoke("lgg_run", "play --duration 6 --music architect-pulse")
            assert request["status"] == "scheduled", request
            receipt = terminal(client, request["req_id"])
            assert receipt["status"] == "applied", receipt
            state = completed(client)
            time.sleep(0.1)
        finally:
            stream.stop_stream()
            stream.close()
    with lock:
        recorded = np.frombuffer(b"".join(samples), dtype="<f4").reshape(-1, channels).mean(axis=1)
    assert len(recorded) > rate and max(abs(recorded)) > 0.001, "No audible loopback signal"
    hop = rate // 200
    rms = np.sqrt(np.mean(recorded[: len(recorded) // hop * hop].reshape(-1, hop) ** 2, axis=1))
    novelty = np.maximum(np.diff(rms, prepend=0), 0)
    onsets = []
    for frame in np.flatnonzero(novelty >= max(novelty) * 0.25):
        stamp = float(first_stamp[0] + frame * hop / rate)
        if not onsets or stamp - onsets[-1] > 0.2:
            onsets.append(stamp)
    events = client.read(f"events --since {cursor}")["events"]
    landings = [e for e in events if e["type"] == "flight_settled"]
    errors = [min(abs(e["at_s"] - offset - onset) for onset in onsets) * 1000 for e in landings]
    assert landings and errors
    report = {
        "passed": bool(max(errors) <= 80),
        "audio_device": device["name"],
        "loopback_rate": rate,
        "clock_sync_rtt_s": rtt,
        "clock_offset_s": offset,
        "landings": len(landings),
        "onsets": len(onsets),
        "max_hardware_onset_error_ms": max(errors),
        "median_hardware_onset_error_ms": float(np.median(errors)),
        "presentation": state,
        "onset_timestamps": onsets,
        "landing_events": landings,
    }
    with wave.open(str(ROOT / "tests/reports/w4-loopback.wav"), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes((np.clip(recorded, -1, 1) * 32767).astype("<i2").tobytes())
    (ROOT / "tests/reports/w4-audio-sync.json").write_text(json.dumps(report, indent=2), "utf-8")
    assert client.edit("undo --tx-id " + receipt["tx_id"])["status"] == "applied"
    client.edit("presentation cancel")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"landing_events", "onset_timestamps"}},
            indent=2,
        )
    )
    assert report["passed"], report["max_hardware_onset_error_ms"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18846)
    main(parser.parse_args().port)
