"""Verify microphone I/O and paired text/audio model turns with real streaming recognition."""

from __future__ import annotations

import json
import statistics
import threading
import time

from lc.client import ROOT, Client
from lc.resident import ResidentAgent
from lc.speech import Speech
from test_w23_live import completed, terminal


def main() -> None:
    client = Client(port=18844)
    assert client.read("objects * --count")["count"] == 0
    speech = Speech()
    microphone_result = []
    done = threading.Event()

    def microphone_done(result):
        microphone_result.append(result)
        done.set()

    speech.start(lambda text: None, microphone_done)
    time.sleep(1.2)
    speech.stop()
    assert done.wait(5), "Microphone did not stop"
    microphone = microphone_result[0]
    assert "error" not in microphone and microphone["audio_s"] > 0.2, microphone
    report = {
        "microphone": {k: v for k, v in microphone.items() if k != "text"},
        "audio_source": "Windows Huihui TTS fixtures processed by real streaming ASR",
        "pairs": [],
        "complete": False,
    }
    path = ROOT / "tests/reports/w4-voice.json"
    undo_times = []
    coarse_times = []

    def event(item):
        if item["type"] != "tool" or item.get("phase") != "completed":
            return
        command = item["arguments"].get("command", "").strip()
        if command.startswith("undo ") and item.get("receipt", {}).get("status") == "applied":
            undo_times.append(time.perf_counter())
        if (
            item["name"] == "lgg_draft"
            and "coarse" in command
            and item.get("receipt", {}).get("status") == "applied"
        ):
            coarse_times.append(time.perf_counter())

    with ResidentAgent(port=18844, on_event=event) as agent:
        fixture = ROOT / "tests/reports/voice/column.wav"
        text = speech.transcribe_file(fixture)["text"]
        report["column_transcript"] = text
        assert "柱子" in text and "撤销" in text, text
        for index in range(4):
            pair = {}
            for mode in ["text", "voice"] if index % 2 == 0 else ["voice", "text"]:
                audio_result = (
                    speech.transcribe_file(fixture, realtime=True) if mode == "voice" else None
                )
                prompt = audio_result["text"] if audio_result else text
                start = audio_result["speech_ended_at"] if audio_result else time.perf_counter()
                previous = len(undo_times)
                turn = agent.turn(prompt)
                assert turn["status"] == "completed" and len(undo_times) == previous + 1, turn
                assert client.read("consistency")["memory_count"] == 0
                pair[mode] = {
                    "end_to_end_s": undo_times[-1] - start,
                    "turn": turn,
                    "speech": audio_result,
                }
            pair["added_end_to_end_s"] = (
                pair["voice"]["end_to_end_s"] - pair["text"]["end_to_end_s"]
            )
            report["pairs"].append(pair)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
            print(
                f"Pair {index + 1}: text={pair['text']['end_to_end_s']:.3f}, "
                f"voice={pair['voice']['end_to_end_s']:.3f}",
                flush=True,
            )
        audio_result = speech.transcribe_file(
            ROOT / "tests/reports/voice/courtyard.wav", realtime=True
        )
        start = audio_result["speech_ended_at"]
        turn = agent.turn(audio_result["text"])
        runs = [
            c["receipt"]
            for c in turn["tools"]
            if c["tool"] == "lgg_run" and c["receipt"] and c["receipt"]["status"] == "scheduled"
        ]
        assert runs and coarse_times, turn
        receipt = terminal(client, runs[-1]["req_id"])
        assert receipt["status"] == "applied", receipt
        committed_at = time.perf_counter()
        state = completed(client)
        report["spoken_courtyard"] = {
            "speech": audio_result,
            "turn": turn,
            "receipt": receipt,
            "modules_s": coarse_times[-1] - start,
            "commit_s": committed_at - start,
            "presentation": state,
        }
        assert client.edit("undo --tx-id " + receipt["tx_id"])["status"] == "applied"
        client.edit("presentation cancel")
    report["median_added_end_to_end_s"] = statistics.median(
        p["added_end_to_end_s"] for p in report["pairs"]
    )
    report["max_added_end_to_end_s"] = max(p["added_end_to_end_s"] for p in report["pairs"])
    report["max_speech_tail_s"] = max(
        p["voice"]["speech"]["release_to_text_s"] for p in report["pairs"]
    )
    report["complete"] = True
    report["passed"] = (
        report["median_added_end_to_end_s"] <= 1.5
        and report["max_speech_tail_s"] <= 1.5
        and report["spoken_courtyard"]["modules_s"] <= 20
        and report["spoken_courtyard"]["commit_s"] <= 120
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"pairs", "spoken_courtyard"}},
            ensure_ascii=False,
            indent=2,
        )
    )
    assert report["passed"], "Voice latency acceptance failed"


if __name__ == "__main__":
    main()
