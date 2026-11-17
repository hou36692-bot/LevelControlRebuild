from v2_language_cases import cross_cases, lighting_cases, near, scope_valid


def test_language_matrix_has_twenty_cases_per_domain() -> None:
    assert len(cross_cases()) == len(lighting_cases()) == 20
    assert all(case.prompt and callable(case.check) for case in cross_cases()+lighting_cases())


def test_scope_rejects_changes_to_unselected_entities() -> None:
    case = cross_cases()[5]
    before = {"environment": {"committed": {}, "presets": {}}, "entities": {
        "wall": {"group": "reference-wall", "value": 1},
        "floor": {"group": "reference-floor", "value": 1},
    }}
    after = {"environment": before["environment"], "entities": {
        "wall": {"group": "reference-wall", "value": 2},
        "floor": {"group": "reference-floor", "value": 1},
    }}
    assert scope_valid(case, before, after)
    after["entities"]["floor"]["value"] = 2
    assert not scope_valid(case, before, after)


def test_empty_array_cannot_pass_movement_case() -> None:
    assert not cross_cases()[13].check({}, {"entities": {}}, {})
    assert near([0.1, 0.2, 0.3], [0.1, 0.2, 0.3])
    assert not near(None, [0.1, 0.2, 0.3])
