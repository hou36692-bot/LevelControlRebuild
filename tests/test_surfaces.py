import pytest
from lc.command import parse


def test_surface_selector_and_parameters() -> None:
    result = parse("edit", "surface apply group:court --preset stone-slate "
                   "--tint 0.1,0.2,0.3 --roughness 0.8 --normal-strength 0.2")
    assert result.path == "/v1/edits"
    assert result.data["operations"] == [{"op": "surface", "selector": "group:court",
                                         "preset": "stone-slate", "values": {
                                             "tint": [0.1, 0.2, 0.3], "roughness": 0.8,
                                             "normal_strength": 0.2}}]


def test_surface_preview_and_commit() -> None:
    preview = parse("edit", "surface preview tag:stone --preset stone-limestone")
    assert preview.path == "/v1/design" and preview.data["action"] == "preview"
    commit = parse("edit", "design commit --expected-version 20")
    assert commit.data == {"action": "commit", "expected_version": 20}
    assert parse("read", "surfaces").path == "/v1/surfaces"


@pytest.mark.parametrize("command", ["surface apply bad:group --preset stone-slate",
                                      "surface apply * --texture test.png",
                                      "design commit --expected-version x"])
def test_bad_surface_syntax(command: str) -> None:
    with pytest.raises(ValueError):
        parse("edit", command)


def test_negative_vectors_are_values() -> None:
    command = parse("edit", "apply create decal --template decal/path "
                    "--rotation -90,0,0 --at -2m,0,2cm")
    transform = command.data["operations"][0]["entity"]["transform"]
    assert transform["rotation_deg"] == [-90, 0, 0]
    assert transform["position_cm"] == [-200, 0, 2]
