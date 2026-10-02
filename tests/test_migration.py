"""Existing data is never rewritten to invent provenance or approval authority."""

import sqlite3
import stat
from contextlib import closing

import pytest

from task_mcp.store import SCHEMA, Store

PROVENANCE_COLUMNS = ("source", "user_request", "acceptance_basis")


def snapshot(database, columns=None):
    with closing(sqlite3.connect(database)) as db:
        if columns is None:
            columns = {
                row[0]: [column[1] for column in db.execute(f'PRAGMA table_info("{row[0]}")')]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            }
        rows = {
            table: db.execute(f'SELECT {",".join(names)} FROM "{table}" ORDER BY rowid').fetchall()
            for table, names in columns.items()
        }
        return columns, rows


def legacy_database(database, version):
    store = Store(database)
    setup = store.init(
        str(database.parent / "repo"), branch="main", action="create_project", confirmed=True
    )
    project, ws = setup["project"]["id"], setup["workstream"]["id"]
    group = store.create_group(ws, "Group")
    pending = store.create_task(project, "Pending", source="user", user_request="Design first")
    accepted = store.create_task(
        project,
        "Accepted",
        approval={"basis": "specific", "note": "Legacy accepted"},
        group_id=group["id"],
        group_expected_revision=group["revision"],
    )
    accepted = store.add_unresolved(accepted["id"], 1, "Question")
    store.add_prerequisite(accepted["id"], accepted["revision"], pending["id"])
    done = store.create_task(
        project,
        "Done",
        approval={"basis": "specific", "note": "Legacy approved"},
        workstream_id=ws,
        scope="workstream",
    )
    attempt = store.record_result(done["id"], ws, 1, "worker", "Delivered", "Exact proof")
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Checked")
    store.signoff_task(done["id"], 2, "approve", "Legacy human verdict", attempt["id"])
    # Keep a connection open: the migration must back up committed WAL content,
    # rather than assuming a copy of just the main file is sufficient.
    db = sqlite3.connect(database)
    db.execute("PRAGMA foreign_keys=OFF")
    for name in PROVENANCE_COLUMNS:
        db.execute(f"ALTER TABLE tasks DROP COLUMN {name}")
    if version == 0:
        old_columns = [row[1] for row in db.execute("PRAGMA table_info(tasks)")]
        definition = SCHEMA[2].split(",\n        source TEXT", 1)[0] + ")"
        definition = definition.replace(
            "CREATE TABLE IF NOT EXISTS tasks (", "CREATE TABLE tasks_legacy ("
        )
        definition = definition.replace(
            "project_id TEXT REFERENCES", "project_id TEXT NOT NULL REFERENCES"
        )
        db.execute(definition)
        # Legacy groups still had a project origin; schema 1 made this nullable.
        db.execute("UPDATE tasks SET project_id=? WHERE project_id IS NULL", (project,))
        db.execute(f"INSERT INTO tasks_legacy SELECT {','.join(old_columns)} FROM tasks")
        db.execute("DROP TABLE tasks")
        db.execute("ALTER TABLE tasks_legacy RENAME TO tasks")
        db.execute(SCHEMA[3])
    db.execute(f"PRAGMA user_version={version}")
    db.execute(
        "INSERT INTO events (timestamp,actor,action,outcome,request_json) "
        "VALUES ('legacy','test','wal-only','ok','{}')"
    )
    db.commit()
    return db, (pending["id"], accepted["id"], done["id"])


@pytest.mark.parametrize("version", [0, 1])
def test_migration_fresh_backup_preserves_all_rows_and_old_classification(tmp_path, version):
    database = tmp_path / "legacy.sqlite3"
    writer, identities = legacy_database(database, version)
    try:
        columns, before = snapshot(database)
        assert database.with_name(database.name + "-wal").stat().st_size > 0
        store = Store(database)
        backup = store.migration_backup_path
        assert backup is not None and backup.is_file()
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
        assert snapshot(backup) == (columns, before)
        assert snapshot(database, columns)[1] == before
        assert store.database_schema_revision() == 2
        with closing(sqlite3.connect(backup)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == version
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        details = store.get_tasks(list(identities))["items"]
        assert [item["accepted"] for item in details] == [False, True, True]
        assert details[2]["status"] == "done" and details[2]["selected_attempt_id"]
        assert details[2]["attempts"][0]["evidence"] == "Exact proof"
        assert all(item["source"] == item["acceptance_basis"] == "unknown" for item in details)
        assert all(item["user_request"] == "" for item in details)
        assert details[1]["acceptance_note"] == "Legacy accepted"
        assert Store(database).migration_backup_path is None
        assert len(list(tmp_path.glob("*.pre-schema-2.*.sqlite3"))) == 1
        # Restore into a disposable target using SQLite online backup, not a
        # file copy over the still-open source/WAL. This proves rollback content.
        restored = tmp_path / "restored.sqlite3"
        with (
            closing(sqlite3.connect(backup)) as source,
            closing(sqlite3.connect(restored)) as target,
        ):
            source.backup(target)
        assert snapshot(restored) == (columns, before)
    finally:
        writer.close()


def test_migration_failure_rolls_back_ddl_revision_and_data_retains_backup(tmp_path, monkeypatch):
    database = tmp_path / "legacy.sqlite3"
    writer, _ = legacy_database(database, 1)
    try:
        before = snapshot(database)
        original = Store._upgrade_schema

        def fail_after_upgrade(db):
            original(db)
            assert "source" in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            raise RuntimeError("Injected failure after migration DDL")

        monkeypatch.setattr(Store, "_upgrade_schema", staticmethod(fail_after_upgrade))
        with pytest.raises(RuntimeError, match="Injected failure"):
            Store(database)
        assert snapshot(database) == before
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        backups = list(tmp_path.glob("*.pre-schema-2.*.sqlite3"))
        assert len(backups) == 1 and snapshot(backups[0]) == before
    finally:
        writer.close()


def test_backup_failure_aborts_before_any_schema_upgrade(tmp_path, monkeypatch):
    database = tmp_path / "legacy.sqlite3"
    writer, _ = legacy_database(database, 1)
    try:
        before = snapshot(database)

        def failed_backup(self, version):
            raise RuntimeError("Injected backup verification failure")

        monkeypatch.setattr(Store, "_backup_for_migration", failed_backup)
        with pytest.raises(RuntimeError, match="backup verification failure"):
            Store(database)
        assert snapshot(database) == before
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        writer.close()


def test_fresh_empty_database_needs_no_backup(tmp_path):
    database = tmp_path / "fresh.sqlite3"
    store = Store(database)
    assert store.database_schema_revision() == 2 and store.migration_backup_path is None
    assert snapshot(database)[1]["tasks"] == []
    assert list(tmp_path.glob("*.pre-schema-2.*.sqlite3")) == []
