import json

from lc.settings_migration import migrate


def _settings(*targets, active=""):
    return {"version": 1, "active": active, "targets": list(targets), "recent": []}


def test_migrates_project_targets_workspaces_and_logs_once(tmp_path):
    source = tmp_path / "LevelControl3"
    target = tmp_path / "LevelControl"
    (source / "workspaces" / "abc").mkdir(parents=True)
    (source / "workspaces" / "abc" / "config.json").write_text("{}")
    (source / "logs").mkdir()
    (source / "logs" / "app.log").write_text("old")
    (source / "project-settings.json").write_text(json.dumps(_settings(
        {"id": "shared", "name": "3.0 shared", "port": 18870},
        {"id": "three", "name": "3.0 only", "port": 18871},
        active="three")))
    target.mkdir()
    (target / "project-settings.json").write_text(json.dumps(_settings(
        {"id": "shared", "name": "2.0 shared", "port": 18848},
        {"id": "two", "name": "2.0 only", "port": 18848},
        active="two")))
    (target / "model-settings.json").write_text("legacy")
    source_before = (source / "project-settings.json").read_text()

    report = migrate(target, source)
    merged = json.loads((target / "project-settings.json").read_text())

    assert report["status"] == "ok" and report["targets"] == 3
    assert [item["id"] for item in merged["targets"]] == ["shared", "two", "three"]
    assert next(item for item in merged["targets"] if item["id"] == "shared")["port"] == 18870
    assert merged["active"] == "three"
    assert (target / "workspaces" / "abc" / "config.json").is_file()
    assert (target / "logs" / "app.log").read_text() == "old"
    assert (target / "model-settings.json").read_text() == "legacy"
    assert (target / "migration.json").is_file()
    assert (source / "project-settings.json").read_text() == source_before

    second = migrate(target, source)
    assert second["status"] == "already_migrated"


def test_missing_source_is_recorded_without_changing_target(tmp_path):
    target = tmp_path / "LevelControl"
    source = tmp_path / "absent"
    report = migrate(target, source)
    assert report["status"] == "no_source"
    assert (target / "migration.json").is_file()

    source.mkdir()
    (source / "project-settings.json").write_text(json.dumps(_settings(
        {"id": "later", "name": "Later", "port": 18870})))
    retried = migrate(target, source)
    assert retried["status"] == "ok"
    assert (target / "project-settings.json").is_file()
