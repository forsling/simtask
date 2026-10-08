"""Rename compatibility keeps old callers on the user's existing data."""

import json
import os
import runpy
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from simtask.compat import setting
from simtask.store import Store, TaskError, default_database, live_database


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("SIMTASK_DB", raising=False)
    monkeypatch.delenv("TASK_MCP_DB", raising=False)
    return tmp_path


def test_existing_legacy_data_is_selected_without_creating_a_new_database(data_home):
    legacy = data_home / "task-mcp/tasks.sqlite3"
    legacy.parent.mkdir()
    legacy.touch()
    assert default_database() == legacy
    assert live_database() == legacy
    assert not (data_home / "simtask").exists()


def test_fresh_installation_uses_simtask(data_home):
    assert default_database() == data_home / "simtask/tasks.sqlite3"


def test_migrated_legacy_alias_resolves_to_the_same_database(data_home):
    current = data_home / "simtask/tasks.sqlite3"
    current.parent.mkdir()
    current.touch()
    (data_home / "task-mcp").symlink_to(current.parent, target_is_directory=True)
    assert default_database() == current
    assert (data_home / "task-mcp/tasks.sqlite3").resolve() == current


def test_distinct_old_and_new_live_databases_require_explicit_selection(data_home, monkeypatch):
    for directory in ["simtask", "task-mcp"]:
        database = data_home / directory / "tasks.sqlite3"
        database.parent.mkdir()
        database.touch()
    with pytest.raises(RuntimeError, match="distinct databases"):
        default_database()
    monkeypatch.setenv("SIMTASK_DB", str(data_home / "simtask/tasks.sqlite3"))
    assert default_database() == data_home / "simtask/tasks.sqlite3"


def test_database_settings_prefer_current_name_and_accept_legacy(data_home, monkeypatch):
    monkeypatch.setenv("TASK_MCP_DB", str(data_home / "legacy.sqlite3"))
    assert default_database() == data_home / "legacy.sqlite3"
    monkeypatch.setenv("SIMTASK_DB", str(data_home / "current.sqlite3"))
    assert default_database() == data_home / "current.sqlite3"


@pytest.mark.parametrize("name", ["ACTOR", "TRACE"])
def test_setting_precedence_includes_explicit_empty_values(monkeypatch, name):
    monkeypatch.delenv("SIMTASK_" + name, raising=False)
    monkeypatch.setenv("TASK_MCP_" + name, "legacy")
    assert setting(name) == "legacy"
    monkeypatch.setenv("SIMTASK_" + name, "")
    assert setting(name) == ""


def test_both_module_entrypoints_select_legacy_data_and_keep_export_format(data_home):
    database = data_home / "task-mcp/tasks.sqlite3"
    context = Store(database).init_project(str(data_home / "project"), "main", confirmed=True)
    for module in ["simtask", "task_mcp"]:
        result = subprocess.run(
            [sys.executable, "-m", module, "--export-workstream", context["workstream"]["id"]],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        assert "Format: task-mcp/v5" in result.stdout
    assert not (data_home / "simtask").exists()


def test_legacy_imports_find_canonical_resources():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import task_mcp.reference as r; import json; "
            "print(json.dumps(r.default_skills('init')))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert json.loads(result.stdout)["items"][0]["name"] == "init"


def test_both_installed_commands_are_available():
    directory = Path(sys.executable).parent
    for name in ["simtask", "task-mcp"]:
        result = subprocess.run([directory / name, "--help"], capture_output=True, timeout=30)
        assert result.returncode == 0


def test_project_relocation_preserves_tasks_workstreams_and_prior_audit(tmp_path):
    relocate = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/relocate-project.py")
    )["relocate"]
    store = Store(tmp_path / "tasks.sqlite3")
    before_path, after_path = tmp_path / "old", tmp_path / "simtask"
    setup = store.init_project(str(before_path), "main", confirmed=True)
    project = setup["project"]["id"]
    store.create_task(project, "Retained task", workstream_id=setup["workstream"]["id"])
    with sqlite3.connect(store.path) as db:
        before = {
            table: db.execute(f"SELECT * FROM {table}").fetchall()
            for table in ["tasks", "workstreams", "events"]
        }
    result = relocate(store, project, before_path, after_path, "simtask")
    assert result["project"]["canonical_path"] == str(after_path)
    assert result["project"]["name"] == "simtask"
    with sqlite3.connect(store.path) as db:
        for table in ["tasks", "workstreams"]:
            assert db.execute(f"SELECT * FROM {table}").fetchall() == before[table]
        assert (
            db.execute("SELECT * FROM events ORDER BY sequence").fetchall()[:-1] == before["events"]
        )
        assert (
            db.execute("SELECT action FROM events ORDER BY sequence DESC LIMIT 1").fetchone()[0]
            == "project.relocated"
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    other = store.init_project(str(tmp_path / "other"), "main", confirmed=True)
    with pytest.raises(TaskError, match="another_project"):
        relocate(store, project, after_path, tmp_path / "other", "bad")
    assert other["project"]["id"] != project
    registered = next(item for item in store.list_projects()["items"] if item["id"] == project)
    assert registered["name"] == "simtask"
    assert registered["canonical_path"] == str(after_path)
