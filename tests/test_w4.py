import json
import wave

import numpy as np
import pytest
from lc.command import parse
from lc.speech import chinese_text
from presenter.beats import analyze, built_in, timed


def test_detected_music_grid_and_quantized_landings():
    beats = built_in()
    assert len(beats["beats_s"]) == 32
    assert beats["median_period_s"] == pytest.approx(0.5)
    items = [{"id": f"test-{index}"} for index in range(188)]
    result = timed(items, 6)
    assert [row["id"] for row in result] == [row["id"] for row in items]
    assert all(
        (row["start_s"] + row["duration_s"]) * 2
        == pytest.approx(round((row["start_s"] + row["duration_s"]) * 2))
        for row in result
    )


def test_silence_is_not_a_beat_track(tmp_path):
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(np.zeros(48000, dtype="<i2").tobytes())
    with pytest.raises(ValueError, match="No reliable"):
        analyze(path)


@pytest.mark.parametrize(
    "command,path",
    [
        ("act column spin --degrees 90 --duration 2", "/v1/actions"),
        ("act column oscillate --amplitude 0m,0m,0.2m --frequency 1 --duration 2", "/v1/actions"),
        ("action stop", "/v1/actions/control"),
        ("player teleport --feet 1m,2m,0m", "/v1/player/control"),
        ("player configure --speed 5m --gravity-scale 1", "/v1/player/control"),
        ("presentation hold --lease-id voice-test", "/v1/presentation"),
    ],
)
def test_w4_write_routes(command, path):
    assert parse("edit", command).path == path


@pytest.mark.parametrize(
    "command,path",
    [
        ("jump-check --from 0m,0m,0m --speed 5m", "/v1/checks"),
        ("clearance --from 0m,0m,0m --to 1m,0m,0m", "/v1/checks"),
        ("walk-graph tag:walkable --from-id a --to-id b", "walk-graph"),
        ("objects group:ground --count", "/v1/entities"),
    ],
)
def test_w4_read_routes(command, path):
    assert parse("read", command).path == path


def test_speech_spacing_preserves_words_and_numbers():
    assert chinese_text("在 我 面前 放 一 根 五 米 高 的 柱子") == "在我面前放一根五米高的柱子"
    assert chinese_text("柱子 5 米") == "柱子 5 米"
    assert json.loads(json.dumps({"text": chinese_text("喷泉 小 一点")}))["text"] == "喷泉小一点"
