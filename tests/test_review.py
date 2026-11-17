import pytest
from lc.command import parse


def test_sightline_units() -> None:
    command = parse("read", "sightline --from 0,0,1.5m --to 10m,0,1.5m")
    assert command.path == "/v1/checks"
    assert command.data["from_cm"] == [0, 0, 150]
    assert command.data["kind"] == "sightline"


def test_four_view_capture_and_compare() -> None:
    command = parse("capture", "court.png --center 12m,0,2m --radius 18m --height 8m")
    assert command.data["center_cm"] == [1200, 0, 200]
    assert command.data["radius_cm"] == 1800
    compare = parse("capture", "compare --a first --b second --name court")
    assert compare.path == "/v1/review"
    assert compare.data == {"action": "compare", "a": "first", "b": "second", "name": "court"}


@pytest.mark.parametrize("frames", [0, 29, 3601])
def test_invalid_measurement(frames: int) -> None:
    with pytest.raises(ValueError):
        parse("edit", f"review performance --frames {frames}")
