"""Existing data is never rewritten to invent provenance or approval authority."""

import json
import sqlite3
import stat
from contextlib import closing

import pytest

from task_mcp.store import DATABASE_SCHEMA_REVISION, SCHEMA, Store

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
        group_id=group["id"],
        group_expected_revision=group["revision"],
    )
    accepted = store.add_unresolved(accepted["id"], 1, "Question")
    store.add_prerequisite(accepted["id"], accepted["revision"], pending["id"])
    store.add_prerequisite(
        pending["id"], 1, accepted["id"], handling="observer", milestone="signoff"
    )
    done = store.create_task(
        project,
        "Done",
        workstream_id=ws,
    )
    attempt = store.record_result(
        done["id"],
        ws,
        1,
        "worker",
        "Delivered",
        "Exact proof",
        artifacts=[{"kind": "artifact", "reference": "tests/test_migration.py"}],
        verification="Exact proof",
        specification_etag=store.get_tasks([done["id"]])["items"][0]["specification_etag"],
    )
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Checked")
    store.signoff_task(
        done["id"], 2, "approve", "Legacy human verdict", attempt["id"], expected_attempt_revision=2
    )
    # Keep a connection open: the migration must back up committed WAL content,
    # rather than assuming a copy of just the main file is sufficient.
    db = sqlite3.connect(database)
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("ALTER TABLE attempts DROP COLUMN concerns_json")
    db.execute("DROP TABLE workstream_task_order")
    db.execute("ALTER TABLE workstreams DROP COLUMN order_revision")
    # Reconstruct the actual pre-queue authority and live scope tables.
    db.execute("DROP TABLE legacy_membership_migration")
    db.execute(
        "UPDATE tasks SET accepted_spec_revision=spec_revision,acceptance_note='Legacy "
        "accepted',acceptance_basis='specific' WHERE id=?",
        (accepted["id"],),
    )
    db.execute(
        "UPDATE tasks SET accepted_spec_revision=spec_revision,acceptance_note='Legacy "
        "approved',acceptance_basis='specific' WHERE id=?",
        (done["id"],),
    )
    if version < 3:
        db.execute("ALTER TABLE projects DROP COLUMN order_revision")
    for table in ("prerequisites", "gate_proposals") if version < 5 else ():
        db.execute(f"ALTER TABLE {table} DROP COLUMN milestone")
    for name in ("summary", "summary_spec_revision") if version < 4 else ():
        db.execute(f"ALTER TABLE tasks DROP COLUMN {name}")
    for name in PROVENANCE_COLUMNS if version < 2 else ():
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


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4, 5])
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
        assert store.database_schema_revision() == DATABASE_SCHEMA_REVISION
        with closing(sqlite3.connect(backup)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == version
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        details = store.get_tasks(list(identities))["items"]
        assert all("accepted" not in item and "approval_decision" not in item for item in details)
        assert details[0]["workstream_ids"] == []
        assert details[1]["workstream_ids"] == details[2]["workstream_ids"]
        assert details[2]["status"] == "done" and details[2]["selected_attempt_id"]
        assert details[2]["attempts"][0]["evidence"] == "Exact proof"
        if version < 2:
            assert all(item["source"] == "unknown" for item in details)
            assert all(item["user_request"] == "" for item in details)
        else:
            assert details[0]["source"] == "user" and details[0]["user_request"] == "Design first"
        assert store.list_tasks(details[0]["project_id"])["ordering"] == "project_baseline"
        assert all(item["summary"] is None and item["summary_stale"] is None for item in details)
        with closing(sqlite3.connect(database)) as db:
            archive = db.execute(
                "SELECT task_json FROM legacy_membership_migration WHERE task_id=?",
                (identities[1],),
            ).fetchone()[0]
            assert '"acceptance_note": "Legacy accepted"' in archive
        assert details[1]["prerequisites"][0]["milestone"] == "review"
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("SELECT DISTINCT milestone FROM prerequisites").fetchall() == [
                ("review",)
            ]
            assert db.execute("SELECT DISTINCT milestone FROM gate_proposals").fetchall() == [
                ("review" if version < 5 else "signoff",)
            ]
        assert Store(database).migration_backup_path is None
        assert len(list(tmp_path.glob(f"*.pre-schema-{DATABASE_SCHEMA_REVISION}.*.sqlite3"))) == 1
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


@pytest.mark.parametrize("legacy_disposition", ["dropped", "deferred"])
def test_legacy_disposition_restore_preserves_authority_archive_and_proof(
    tmp_path, legacy_disposition
):
    database = tmp_path / "legacy-disposition.sqlite3"
    writer, identities = legacy_database(database, 5)
    writer.execute("UPDATE tasks SET status=? WHERE id=?", (legacy_disposition, identities[1]))
    writer.commit()
    before = snapshot(database)
    store = Store(database)
    assert snapshot(store.migration_backup_path) == before
    migrated = store.get_tasks([identities[1]])["items"][0]
    restored = store.set_disposition(
        migrated["id"],
        migrated["revision"],
        "open",
        "Resume",
        authorization="Actual revival instruction" if legacy_disposition == "dropped" else None,
    )
    assert restored["workstream_ids"] == migrated["workstream_ids"]
    with closing(sqlite3.connect(database)) as db:
        assert db.execute(
            "SELECT accepted_spec_revision,acceptance_note FROM tasks WHERE id=?", (migrated["id"],)
        ).fetchone() == (1, "Legacy accepted")
    writer.close()


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4, 5])
def test_migration_failure_rolls_back_ddl_revision_and_data_retains_backup(
    tmp_path, monkeypatch, version
):
    database = tmp_path / "legacy.sqlite3"
    writer, _ = legacy_database(database, version)
    try:
        before = snapshot(database)
        original = Store._upgrade_schema

        def fail_after_upgrade(db):
            original(db)
            assert "source" in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            assert "order_revision" in {row[1] for row in db.execute("PRAGMA table_info(projects)")}
            raise RuntimeError("Injected failure after migration DDL")

        monkeypatch.setattr(Store, "_upgrade_schema", staticmethod(fail_after_upgrade))
        with pytest.raises(RuntimeError, match="Injected failure"):
            Store(database)
        assert snapshot(database) == before
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == version
        backups = list(tmp_path.glob(f"*.pre-schema-{DATABASE_SCHEMA_REVISION}.*.sqlite3"))
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
    assert (
        store.database_schema_revision() == DATABASE_SCHEMA_REVISION
        and store.migration_backup_path is None
    )
    assert snapshot(database)[1]["tasks"] == []
    assert list(tmp_path.glob(f"*.pre-schema-{DATABASE_SCHEMA_REVISION}.*.sqlite3")) == []


@pytest.mark.parametrize("fail", [False, True])
def test_schema7_concern_metadata_preserves_proof_without_inventing_provenance(
    tmp_path, monkeypatch, fail
):
    database = tmp_path / "schema7.sqlite3"
    store = Store(database)
    setup = store.init(str(tmp_path / "repo"), "main", action="create_project", confirmed=True)
    project, ws = setup["project"]["id"], setup["workstream"]["id"]
    task = store.create_task(project, "Completed original proof", workstream_id=ws)
    attempt = store.record_result(
        task["id"],
        ws,
        1,
        "worker",
        "Delivered",
        "Exact β proof",
        [{"kind": "artifact", "reference": "original.txt"}],
        "Original verification",
        task["specification_etag"],
    )
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Checked")
    store.signoff_task(task["id"], 2, "approve", "Synthetic verdict", attempt["id"], 2)
    legacy = store.create_task(project, "Legacy reserved lookalike", workstream_id=ws)
    legacy_attempt = store.record_result(
        legacy["id"],
        ws,
        1,
        "worker",
        "Historical",
        "Placeholder",
        [{"kind": "artifact", "reference": "old.txt"}],
        "Historical verification",
        legacy["specification_etag"],
    )
    raw = (
        ' \n{"format":"attempt-concerns-v1","original_evidence":"inner β",'
        '"concerns":[{"kind":"value","text":"Historical data",'
        '"source":"reviewer","author":"old"}]}\n\t'
    )
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("ALTER TABLE attempts DROP COLUMN concerns_json")
        writer.execute("DROP TABLE legacy_membership_migration")
        writer.execute("PRAGMA user_version=7")
        writer.execute("UPDATE attempts SET evidence=? WHERE id=?", (raw, legacy_attempt["id"]))
        writer.commit()
        before = snapshot(database)
        upgrade = Store._upgrade_schema
        if fail:

            def injected(db):
                upgrade(db)
                assert "concerns_json" in {r[1] for r in db.execute("PRAGMA table_info(attempts)")}
                raise RuntimeError("Injected concern migration failure")

            monkeypatch.setattr(Store, "_upgrade_schema", staticmethod(injected))
            with pytest.raises(RuntimeError, match="Injected concern"):
                Store(database)
            assert snapshot(database) == before
        else:
            migrated = Store(database)
            assert snapshot(database, before[0])[1] == before[1]
            assert migrated.get_attempt(legacy_attempt["id"])["evidence"] == raw
            assert migrated.get_attempt(legacy_attempt["id"])["concerns"] == []
            proof = migrated.get_attempt(attempt["id"])
            assert proof["evidence"] == "Exact β proof" and proof["state"] == "passed"
            assert proof["artifacts"] == [{"kind": "artifact", "reference": "original.txt"}]
            assert proof["verification"] == "Original verification"
            assert migrated.workstream_status(ws)["concern_tasks"]["total"] == 0
            assert Store(database).migration_backup_path is None
        backups = list(tmp_path.glob("*.pre-schema-10.*.sqlite3"))
        assert len(backups) == 1 and snapshot(backups[0]) == before
        assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
        restored = tmp_path / "restored-schema7.sqlite3"
        with (
            closing(sqlite3.connect(backups[0])) as source,
            closing(sqlite3.connect(restored)) as dest,
        ):
            source.backup(dest)
        assert snapshot(restored) == before
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == (
                7 if fail else DATABASE_SCHEMA_REVISION
            )
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_legacy_pending_keeps_membership_and_uses_normal_resolution(tmp_path):
    database = tmp_path / "pending.sqlite3"
    writer, identities = legacy_database(database, 4)
    pending, accepted, done = identities
    ws = writer.execute("SELECT id FROM workstreams").fetchone()[0]
    writer.execute("INSERT INTO scope_members VALUES (?,?)", (ws, pending))
    writer.commit()
    before = snapshot(database)
    store = Store(database)
    assert snapshot(store.migration_backup_path) == before
    details = {t["id"]: t for t in store.get_tasks(list(identities))["items"]}
    assert details[pending]["workstream_ids"] == [ws]
    assert details[pending]["adopted"]
    questions = details[pending]["unresolved_items"]
    assert len(questions) == 1 and "legacy pending intent" in questions[0]["text"]
    assert (
        "unresolved_items"
        in store.read_tasks([pending], specification=True, workstream_id=ws)["items"][0][
            "gate_diagnostics"
        ]
    )
    assert not details[done]["unresolved_items"] and details[done]["selected_attempt_id"]
    with closing(sqlite3.connect(database)) as db:
        assert (
            json.loads(
                db.execute(
                    "SELECT task_json FROM legacy_membership_migration WHERE task_id=?", (pending,)
                ).fetchone()[0]
            )["unresolved_json"]
            == "[]"
        )
    resolved = store.resolve_unresolved(
        pending, details[pending]["revision"], questions[0]["id"], "Actual decision to proceed"
    )
    assert resolved["workstream_ids"] == [ws] and not resolved["unresolved_items"]
    assert store.get_next_action(ws)["task"]["id"] == pending
    # All old audit and proof bytes survive, including when the question is resolved.
    with closing(sqlite3.connect(database)) as db:
        for table in ("attempts", "events"):
            names = before[0][table]
            current = db.execute(f"SELECT {','.join(names)} FROM {table} ORDER BY rowid").fetchall()
            assert current[: len(before[1][table])] == before[1][table]
    writer.close()


@pytest.mark.parametrize("fail", [False, True])
def test_schema6_rejection_index_upgrade_preserves_rows_and_rolls_back(tmp_path, monkeypatch, fail):
    database = tmp_path / "schema6.sqlite3"
    store = Store(database)
    setup = store.init(str(tmp_path), branch="main", action="create_project", confirmed=True)
    store.create_task(
        setup["project"]["id"], "Existing queue", workstream_id=setup["workstream"]["id"]
    )
    with closing(sqlite3.connect(database)) as db:
        db.execute("DROP TABLE legacy_membership_migration")
        db.execute("DROP INDEX rejection_history")
        db.execute("PRAGMA user_version=6")
        db.commit()
    before = snapshot(database)
    if fail:
        upgrade = Store._upgrade_schema

        def failed_upgrade(db):
            upgrade(db)
            assert db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='rejection_history'"
            ).fetchone()
            raise RuntimeError("Injected index migration failure")

        monkeypatch.setattr(Store, "_upgrade_schema", staticmethod(failed_upgrade))
        with pytest.raises(RuntimeError, match="Injected index migration"):
            Store(database)
    else:
        upgraded = Store(database)
        assert upgraded.database_schema_revision() == DATABASE_SCHEMA_REVISION
    assert snapshot(database, before[0])[1] == before[1]
    backups = list(tmp_path.glob(f"*.pre-schema-{DATABASE_SCHEMA_REVISION}.*.sqlite3"))
    assert len(backups) == 1 and snapshot(backups[0]) == before
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == (
            6 if fail else DATABASE_SCHEMA_REVISION
        )
        assert (
            bool(
                db.execute("SELECT 1 FROM sqlite_master WHERE name='rejection_history'").fetchone()
            )
            != fail
        )


@pytest.mark.parametrize("version", [6, 8])
def test_rejected_candidate_restores_all_archive_scopes_and_candidate_additions(tmp_path, version):
    database = tmp_path / f"rejected-schema{version}.sqlite3"
    writer, identities = legacy_database(database, 4)
    pending, accepted, done = identities
    writer.row_factory = sqlite3.Row
    ws = dict(writer.execute("SELECT * FROM workstreams").fetchone())
    other = {**ws, "id": "wst_parallel", "name": "parallel", "branch": "parallel"}
    writer.execute(
        "INSERT INTO workstreams VALUES "
        "(:id,:project_id,:name,:branch,:checkout_path,:revision,:created_at)",
        other,
    )
    group = writer.execute("SELECT parent_group_id FROM tasks WHERE id=?", (accepted,)).fetchone()[
        0
    ]
    writer.execute("INSERT INTO scope_groups VALUES (?,?)", (other["id"], group))
    writer.execute("INSERT INTO scope_members VALUES (?,?)", (ws["id"], pending))
    writer.execute("INSERT INTO scope_members VALUES (?,?)", (other["id"], pending))
    writer.execute("INSERT INTO scope_members VALUES (?,?)", (other["id"], done))
    writer.execute("INSERT INTO scope_exclusions VALUES (?,?)", (ws["id"], accepted))
    original = {
        table: writer.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        for table in ("scope_members", "scope_groups", "scope_exclusions")
    }
    writer.execute(
        "CREATE TABLE queue_members (task_id TEXT PRIMARY KEY REFERENCES tasks(id),"
        "workstream_id TEXT NOT NULL REFERENCES workstreams(id))"
    )
    writer.execute(
        "CREATE TABLE legacy_queue_migration (task_id TEXT PRIMARY KEY REFERENCES tasks(id),"
        "source_schema INTEGER NOT NULL,authority_json TEXT NOT NULL,scopes_json TEXT NOT NULL,"
        "owning_workstream_id TEXT,reason TEXT NOT NULL)"
    )
    tasks = [dict(row) for row in writer.execute("SELECT * FROM tasks")]
    for task in tasks:
        facts = {
            "members": [dict(r) for r in original["scope_members"] if r["task_id"] == task["id"]],
            "groups": [
                dict(r)
                for r in original["scope_groups"]
                if r["group_id"] in (task["id"], task["parent_group_id"])
            ],
            "exclusions": [
                dict(r)
                for r in original["scope_exclusions"]
                if r["task_id"] in (task["id"], task["parent_group_id"])
            ],
            "eligible_workstream_ids": Store._membership_ids(writer, task),
        }
        authority = {
            key: task[key]
            for key in ("accepted_spec_revision", "acceptance_note", "acceptance_basis")
        }
        writer.execute(
            "INSERT INTO legacy_queue_migration VALUES (?,?,?,?,?,?)",
            (
                task["id"],
                4,
                json.dumps(authority),
                json.dumps(facts),
                other["id"] if task["id"] == done else None,
                "legacy_unapproved_to_inbox" if task["id"] == pending else "single_scope",
            ),
        )
    writer.execute("INSERT INTO queue_members VALUES (?,?)", (done, other["id"]))
    # Former adoption survives candidate specification edits; frozen legacy
    # acceptance columns no longer govern the current edited spec.
    writer.execute("UPDATE tasks SET spec_revision=2,revision=revision+1 WHERE id=?", (accepted,))
    # Exercise recovery from archives as well as retained tables.
    writer.execute("DELETE FROM scope_groups WHERE workstream_id=?", (other["id"],))
    writer.execute("DELETE FROM scope_members WHERE workstream_id=?", (other["id"],))
    writer.execute("DELETE FROM scope_exclusions")
    added = Store._insert_task(writer, ws["project_id"], "Candidate addition", "Scope", "Checks")
    writer.execute("INSERT INTO queue_members VALUES (?,?)", (added, ws["id"]))
    if version == 8:
        writer.execute("ALTER TABLE attempts ADD COLUMN concerns_json TEXT NOT NULL DEFAULT '[]'")
        writer.execute(
            "UPDATE attempts SET concerns_json=?",
            (
                '[{"kind":"design","text":"Exact concern",'
                '"source":"implementer","author":"worker"}]',
            ),
        )
    writer.execute(f"PRAGMA user_version={version}")
    writer.commit()
    before = snapshot(database)
    migrated = Store(database)
    assert snapshot(migrated.migration_backup_path) == before
    details = {t["id"]: t for t in migrated.get_tasks([*identities, added])["items"]}
    assert set(details[pending]["workstream_ids"]) == {ws["id"], other["id"]}
    assert details[accepted]["workstream_ids"] == [other["id"]]
    assert set(details[done]["workstream_ids"]) == {ws["id"], other["id"]}
    assert details[added]["workstream_ids"] == [ws["id"]] and not details[added]["unresolved_items"]
    assert details[pending]["unresolved_items"] and not details[done]["unresolved_items"]
    assert len(details[accepted]["unresolved_items"]) == 1
    assert not details[accepted]["unresolved_items"][0]["id"].startswith(
        "unr_membership_migration_"
    )
    with closing(sqlite3.connect(database)) as db:
        for table, rows in original.items():
            assert {tuple(row) for row in rows} <= set(
                db.execute(f"SELECT * FROM {table}").fetchall()
            )
        for table in ("attempts", "events", "legacy_queue_migration"):
            current = db.execute(
                f"SELECT {','.join(before[0][table])} FROM {table} ORDER BY rowid"
            ).fetchall()
            assert current[: len(before[1][table])] == before[1][table]
            if table != "events":
                assert len(current) == len(before[1][table])
        names = before[0]["tasks"]
        completed = db.execute(
            f"SELECT {','.join(names)} FROM tasks WHERE id=?", (done,)
        ).fetchone()
        assert completed == next(row for row in before[1]["tasks"] if row[0] == done)
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='queue_members'").fetchone()
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    restored = tmp_path / "rollback.sqlite3"
    with (
        closing(sqlite3.connect(migrated.migration_backup_path)) as source,
        closing(sqlite3.connect(restored)) as target,
    ):
        source.backup(target)
    assert snapshot(restored) == before
    writer.close()


def test_candidate_exclusion_conflict_aborts_with_backup_and_no_scope_guess(tmp_path):
    database = tmp_path / "conflict.sqlite3"
    writer, identities = legacy_database(database, 4)
    task = identities[1]
    ws = writer.execute("SELECT id FROM workstreams").fetchone()[0]
    writer.execute("INSERT INTO scope_exclusions VALUES (?,?)", (ws, task))
    writer.execute("CREATE TABLE queue_members (task_id TEXT PRIMARY KEY,workstream_id TEXT)")
    writer.execute("INSERT INTO queue_members VALUES (?,?)", (task, ws))
    writer.execute("PRAGMA user_version=6")
    writer.commit()
    before = snapshot(database)
    with pytest.raises(RuntimeError, match="membership_migration_conflict"):
        Store(database)
    assert snapshot(database) == before
    backups = list(tmp_path.glob("*.pre-schema-10.*.sqlite3"))
    assert len(backups) == 1 and snapshot(backups[0]) == before
    writer.close()


@pytest.mark.parametrize("fail", [False, True])
def test_schema9_order_migration_seeds_effective_lists_and_preserves_history(
    tmp_path, monkeypatch, fail
):
    database = tmp_path / "schema9-order.sqlite3"
    store = Store(database)
    ctx = store.init(str(tmp_path / "repo"), branch="a", action="create_project", confirmed=True)
    project, a = ctx["project"]["id"], ctx["workstream"]["id"]
    b = store.init_workstream(project, str(tmp_path / "repo"), branch="b", confirmed=True)[
        "workstream"
    ]["id"]
    group = store.create_group(a, "Live group")
    tasks = [
        store.create_task(project, title, workstream_id=a) for title in ("First", "Second", "Third")
    ]
    store.add_group_member(group["id"], group["revision"], tasks[2]["id"], 1)
    store.set_scope(b, 1, f"none +{group['id']} +{tasks[0]['id']}")
    attempt = store.record_result(
        tasks[0]["id"],
        a,
        1,
        "builder",
        "Proof",
        "Original proof bytes",
        artifacts=[{"kind": "artifact", "reference": __file__}],
        verification="Actual fixture",
        specification_etag=store.get_tasks([tasks[0]["id"]])["items"][0]["specification_etag"],
        concerns=[{"kind": "design", "text": "Synthetic recorded design concern"}],
    )
    store.record_review(attempt["id"], 1, "independent reviewer", "pass", "Original review")
    store.signoff_task(
        tasks[0]["id"], attempt["task_revision"], "approve", "Synthetic verdict", attempt["id"], 2
    )
    with closing(sqlite3.connect(database)) as db:
        # An authentic schema 9 has only canonical baseline positions.
        db.execute("DROP TABLE workstream_task_order")
        db.execute("ALTER TABLE workstreams DROP COLUMN order_revision")
        db.execute("UPDATE tasks SET order_key=100-order_key WHERE object_type='task'")
        db.execute("PRAGMA user_version=9")
        db.commit()
    columns, before = snapshot(database)
    upgrade = Store._upgrade_schema
    if fail:

        def injected(db):
            upgrade(db)
            assert db.execute("SELECT count(*) FROM workstream_task_order").fetchone()[0] == 5
            raise RuntimeError("after local order migration")

        monkeypatch.setattr(Store, "_upgrade_schema", staticmethod(injected))
        with pytest.raises(RuntimeError, match="after local order migration"):
            Store(database)
        assert snapshot(database) == (columns, before)
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 9
    else:
        upgraded = Store(database)
        assert snapshot(database, columns)[1] == before
        for ws, expected in ((a, list(reversed(tasks))), (b, [tasks[2], tasks[0]])):
            board = upgraded.list_tasks(project, ws)
            assert board["workstream_order_revision"] == 0
            assert [task["id"] for task in board["items"]] == [task["id"] for task in expected]
        assert (
            upgraded.get_tasks([tasks[0]["id"]])["items"][0]["selected_attempt_id"] == attempt["id"]
        )
        assert (
            upgraded.get_attempt(attempt["id"])["concerns"][0]["text"]
            == "Synthetic recorded design concern"
        )
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
    backup = next(tmp_path.glob("schema9-order.sqlite3.pre-schema-10.*.sqlite3"))
    assert snapshot(backup) == (columns, before)
    restored = tmp_path / "restored-schema9.sqlite3"
    with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(restored)) as target:
        source.backup(target)
    assert snapshot(restored) == (columns, before)
