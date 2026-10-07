"""Titled notes: separate records that reference tasks, groups, workstreams and projects."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import textwrap
from contextlib import closing
from io import BytesIO
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from task_mcp.reference import default_skills
from task_mcp.server import create_server
from task_mcp.store import (
    DATABASE_SCHEMA_REVISION,
    NOTE_TEXT_LIMIT,
    NOTE_TITLE_LIMIT,
    Store,
    TaskError,
)
from task_mcp.viewer import dispatch

ROOT = Path(__file__).resolve().parents[1]
# The last schema-11 code with set_note and init notes: main as the live server runs it.
PREVIOUS = {"main": "039c5b10fc76ad8fe46a668dfdec1a2ca5acdd19"}


@pytest.fixture
def world(tmp_path):
    """Two projects with a task, a group and a workstream each."""
    store = Store(tmp_path / "tasks.sqlite3", actor="simon")
    ids = {}
    for name in ("one", "two"):
        path = tmp_path / name
        path.mkdir()
        setup = store.init(str(path), "main", action="create_project", confirmed=True)
        project, ws = setup["project"]["id"], setup["workstream"]["id"]
        task = store.create_task(
            project, f"Task {name}", workstream_id=ws, public_id=f"task-{name}"
        )
        group = store.create_task(
            project, f"Group {name}", kind="group", workstream_id=ws, public_id=f"grp-{name}"
        )
        ids[name] = {
            "project": project,
            "workstream": ws,
            "task": task["id"],
            "group": group["id"],
            "path": str(path),
        }
    return store, ids


def refs(entity, *kinds):
    return [{"kind": kind, "id": entity[kind]} for kind in kinds]


def count_rows(store):
    with closing(sqlite3.connect(store.path)) as db:
        return tuple(
            db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("note_records", "note_references")
        )


def note_events(store, note_id):
    return store.list_events(note_id=note_id, include_details=True, limit=100)["items"]


def test_create_saves_one_record_referencing_every_kind_across_projects(world):
    store, ids = world
    references = refs(ids["one"], "task", "group", "workstream", "project") + refs(
        ids["two"], "task", "workstream"
    )
    ack = store.create_note("Release checklist", "Tag after review.", references)
    assert ack == ack | {
        "title": "Release checklist",
        "revision": 1,
        "archived": False,
        "reference_count": 6,
        "changed": True,
    }
    assert ack["id"].startswith("note_") and ack["created_at"] == ack["updated_at"]
    assert "text" not in ack
    assert count_rows(store) == (1, 6)
    (note,) = store.get_notes([ack["id"]])["items"]
    assert note == {
        "id": ack["id"],
        "title": "Release checklist",
        "text": "Tag after review.",
        "references": references,
        "archived": False,
        "revision": 1,
        "created_at": ack["created_at"],
        "updated_at": ack["updated_at"],
        "created_by": "simon",
        "updated_by": "simon",
    }
    # Every referenced entity finds the one note.
    for reference in references:
        assert [n["id"] for n in store.list_notes(reference)["items"]] == [ack["id"]]


def test_repeated_references_are_deduplicated_and_text_never_adds_references(world):
    store, ids = world
    one = ids["one"]
    text = f"See {ids['two']['task']} and {one['group']} and {ids['two']['project']}."
    ack = store.create_note(
        "Dedup", text, refs(one, "task", "task", "project") + refs(one, "task", "project")
    )
    note = store.get_notes([ack["id"]])["items"][0]
    assert note["references"] == refs(one, "task", "project")
    assert ack["reference_count"] == 2 and count_rows(store) == (1, 2)
    for entity in (
        {"kind": "task", "id": ids["two"]["task"]},
        {"kind": "group", "id": one["group"]},
    ):
        assert store.list_notes(entity)["items"] == []


@pytest.mark.parametrize(
    "bad, error",
    [
        (lambda ids: [{"kind": "task", "id": "missing-task"}], "unknown_reference"),
        (lambda ids: [{"kind": "group", "id": ids["one"]["task"]}], "reference_kind_mismatch"),
        (lambda ids: [{"kind": "task", "id": ids["one"]["group"]}], "reference_kind_mismatch"),
        (lambda ids: [{"kind": "project", "id": ids["one"]["workstream"]}], "is a workstream"),
        (lambda ids: [{"kind": "workstream", "id": ids["one"]["project"]}], "is a project"),
        (lambda ids: [{"kind": "attempt", "id": ids["one"]["task"]}], "invalid_reference"),
        (lambda ids: [{"kind": "task", "id": ids["one"]["task"], "x": 1}], "invalid_reference"),
        (lambda ids: [{"kind": "project", "id": ids["one"]["path"]}], "unknown_reference"),
        (lambda ids: [ids["one"]["task"]], "invalid_reference"),
    ],
)
def test_an_invalid_reference_rejects_the_whole_write(world, bad, error):
    store, ids = world
    good = refs(ids["one"], "task", "project")
    with pytest.raises(TaskError, match=error):
        store.create_note("Title", "Text", good + bad(ids))
    assert count_rows(store) == (0, 0)
    ack = store.create_note("Kept", "Original", good)
    with pytest.raises(TaskError, match=error):
        store.update_note(ack["id"], 1, title="Changed", text="Changed", references=good + bad(ids))
    note = store.get_notes([ack["id"]])["items"][0]
    assert (note["title"], note["text"], note["revision"]) == ("Kept", "Original", 1)
    assert note["references"] == good and count_rows(store) == (1, 2)


def test_references_are_required(world):
    store, ids = world
    for empty in ([], None, "task-one"):
        with pytest.raises(TaskError, match="references_required"):
            store.create_note("Title", "Text", empty)
    assert count_rows(store) == (0, 0)
    ack = store.create_note("Kept", "Text", refs(ids["one"], "task"))
    with pytest.raises(TaskError, match="references_required"):
        store.update_note(ack["id"], 1, references=[])


def test_titles_and_text_are_required_and_bounded(world):
    store, ids = world
    target = refs(ids["one"], "task")
    longest = store.create_note("t" * NOTE_TITLE_LIMIT, "é" * NOTE_TEXT_LIMIT, target)
    assert longest["revision"] == 1
    assert store.create_note("  Padded  ", "x", target)["title"] == "Padded"
    for title, text, error in (
        ("", "x", "invalid_note_title"),
        ("   ", "x", "invalid_note_title"),
        (None, "x", "invalid_note_title"),
        ("t" * 121, "x", "note_title_too_long: a title holds at most 120 characters; this one"),
        ("T", "", "invalid_note_text"),
        ("T", " \n", "invalid_note_text"),
        ("T", "x" * 4001, "note_too_long: a note holds at most 4,000 characters; this text has"),
    ):
        with pytest.raises(TaskError, match=error):
            store.create_note(title, text, target)
    assert count_rows(store) == (2, 2)
    with closing(sqlite3.connect(store.path)) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE note_records SET text=?", ("x" * 4001,))


def test_update_keeps_or_replaces_references_and_checks_the_revision(world):
    store, ids = world
    one, two = ids["one"], ids["two"]
    created = store.create_note("Plan", "v1", refs(one, "task", "workstream"))
    note_id = created["id"]
    edited = store.update_note(note_id, 1, text="v2")
    assert edited["revision"] == 2 and edited["changed"] is True
    note = store.get_notes([note_id])["items"][0]
    assert note["references"] == refs(one, "task", "workstream") and note["text"] == "v2"
    assert note["created_at"] == created["created_at"]
    assert note["updated_at"] > created["updated_at"]
    replaced = store.update_note(note_id, 2, title="Plan B", references=refs(two, "group", "group"))
    assert replaced["reference_count"] == 1
    note = store.get_notes([note_id])["items"][0]
    assert note["title"] == "Plan B" and note["references"] == refs(two, "group")
    assert store.list_notes({"kind": "task", "id": one["task"]})["items"] == []
    assert count_rows(store) == (1, 1)
    for stale in (1, 2, 4, None, "3", True):
        with pytest.raises(TaskError, match="revision_conflict"):
            store.update_note(note_id, stale, text="blind overwrite")
    with pytest.raises(TaskError, match="no_note_changes"):
        store.update_note(note_id, 3)
    with pytest.raises(TaskError, match="unknown_note"):
        store.update_note("note_missing", 1, text="x")
    with pytest.raises(TaskError, match="invalid_archived"):
        store.update_note(note_id, 3, archived="yes")
    # Values equal to the current ones change nothing and keep the revision.
    same = store.update_note(note_id, 3, title="Plan B", references=refs(two, "group"))
    assert same["changed"] is False and same["revision"] == 3
    assert store.get_notes([note_id])["items"][0]["updated_at"] == note["updated_at"]


def test_edits_and_archiving_are_audited_with_before_and_after(world, tmp_path):
    store, ids = world
    one = ids["one"]
    note_id = store.create_note("Audit", "first", refs(one, "task"))["id"]
    other = Store(store.path, actor="reviewer")
    other.update_note(note_id, 1, text="second", references=refs(one, "task", "project"))
    with pytest.raises(TaskError):
        other.update_note(note_id, 1, text="stale")
    other.update_note(note_id, 2, archived=True)
    other.update_note(note_id, 3, archived=False)
    store.create_note("Unrelated", "x", refs(one, "task"))
    events = note_events(store, note_id)
    assert [(e["action"], e["outcome"], e["actor"]) for e in events] == [
        ("note.created", "ok", "simon"),
        ("note.updated", "ok", "reviewer"),
        ("note.updated", "error", "reviewer"),
        ("note.updated", "ok", "reviewer"),
        ("note.updated", "ok", "reviewer"),
    ]
    created, edited, failed, archived, restored = events
    assert created["before"] is None and created["after"]["text"] == "first"
    assert edited["before"]["text"] == "first" and edited["after"]["text"] == "second"
    assert edited["before"]["references"] == refs(one, "task")
    assert edited["after"]["references"] == refs(one, "task", "project")
    assert edited["after"]["updated_by"] == "reviewer"
    assert edited["after"]["created_at"] == created["after"]["created_at"]
    assert "revision_conflict" in failed["error"] and failed["after"] is None
    assert (archived["before"]["archived"], archived["after"]["archived"]) == (False, True)
    assert (restored["before"]["archived"], restored["after"]["archived"]) == (True, False)
    # Notes whose references share one project are in that project's history too.
    project_actions = [
        e["action"] for e in store.list_events(project=one["project"], limit=100)["items"]
    ]
    assert project_actions.count("note.created") == 2
    with pytest.raises(TaskError, match="unknown_note"):
        store.list_events(note_id="note_missing")


def test_archived_notes_are_hidden_until_requested_and_restored_on_unarchive(world):
    store, ids = world
    one = ids["one"]
    task = {"kind": "task", "id": one["task"]}
    kept = store.create_note("Kept", "x", [task])["id"]
    hidden = store.create_note("Hidden", "x", [task])["id"]
    store.update_note(hidden, 1, archived=True)
    listed = store.list_notes(task)
    assert [n["id"] for n in listed["items"]] == [kept]
    assert listed["total"] == 1 and listed["archived_hidden"] == 1
    everything = store.list_notes(task, include_archived=True)
    assert [(n["id"], n.get("archived", False)) for n in everything["items"]] == [
        (hidden, True),
        (kept, False),
    ]
    assert everything["total"] == 2 and "archived_hidden" not in everything
    spec = store.read_tasks([one["task"]], specification=True)["items"][0]
    assert [n["id"] for n in spec["notes"]] == [kept] and spec["note_total"] == 1
    # get_notes still reads an archived note in full.
    assert store.get_notes([hidden])["items"][0]["archived"] is True
    store.update_note(hidden, 2, archived=False)
    assert store.list_notes(task)["total"] == 2
    with pytest.raises(TaskError, match="invalid_include_archived"):
        store.list_notes(task, include_archived="yes")


def test_list_notes_pages_titles_newest_updated_first(world):
    store, ids = world
    workstream = {"kind": "workstream", "id": ids["two"]["workstream"]}
    created = [store.create_note(f"Note {i}", "x", [workstream])["id"] for i in range(5)]
    store.update_note(created[1], 1, text="touched")
    first = store.list_notes(workstream, limit=2)
    assert [n["title"] for n in first["items"]] == ["Note 1", "Note 4"]
    assert set(first["items"][0]) == {"id", "title", "updated_at"}
    assert first["total"] == 5 and first["next_offset"] == 2
    rest = store.list_notes(workstream, limit=2, offset=4)
    assert [n["title"] for n in rest["items"]] == ["Note 0"] and rest["next_offset"] is None
    assert first["reference"] == workstream
    for reference, error in (
        ({"kind": "workstream", "id": "wst_missing"}, "unknown_reference"),
        ({"kind": "task", "id": ids["two"]["workstream"]}, "reference_kind_mismatch"),
        ("not a reference", "invalid_reference"),
    ):
        with pytest.raises(TaskError, match=error):
            store.list_notes(reference)
    with pytest.raises(TaskError, match="invalid_limit"):
        store.list_notes(workstream, limit=0)


def test_get_notes_reads_complete_notes_in_order(world):
    store, ids = world
    a = store.create_note("A", "alpha", refs(ids["one"], "project"))["id"]
    b = store.create_note("B", "beta", refs(ids["two"], "project"))["id"]
    assert [n["text"] for n in store.get_notes([b, a])["items"]] == ["beta", "alpha"]
    with pytest.raises(TaskError, match="unknown_note"):
        store.get_notes([a, "note_missing"])
    for bad in ([], [a, a], [a] * 21, a):
        with pytest.raises(TaskError, match="invalid_ids"):
            store.get_notes(bad)


def test_specification_reads_list_active_note_titles_capped_at_twenty(world):
    store, ids = world
    one = ids["one"]
    task = {"kind": "task", "id": one["task"]}
    created = [store.create_note(f"Note {i:02}", "x", [task])["id"] for i in range(22)]
    store.update_note(created[0], 1, text="newest")
    store.update_note(created[1], 1, archived=True)
    store.create_note("Group note", "x", refs(one, "group"))
    spec = store.read_tasks([one["task"]], specification=True)["items"][0]
    assert spec["note_total"] == 21 and len(spec["notes"]) == 20
    assert all(set(n) == {"id", "title", "updated_at"} for n in spec["notes"])
    assert spec["notes"][0]["title"] == "Note 00"
    assert [n["title"] for n in spec["notes"][1:3]] == ["Note 21", "Note 20"]
    assert created[1] not in {n["id"] for n in spec["notes"]}
    stamps = [n["updated_at"] for n in spec["notes"]]
    assert stamps == sorted(stamps, reverse=True)
    # The same window comes with get_next_action, and a group read lists the group's notes.
    action = store.get_next_action(one["workstream"])
    assert action["task"]["id"] == one["task"]
    assert action["task"]["notes"] == spec["notes"] and action["task"]["note_total"] == 21
    group = store.read_tasks([one["group"]], specification=True)["items"][0]
    assert [n["title"] for n in group["notes"]] == ["Group note"] and group["note_total"] == 1
    # Cards without the specification carry no notes.
    card = store.read_tasks([one["task"]])["items"][0]
    assert "notes" not in card and "note_total" not in card
    other = store.read_tasks([ids["two"]["task"]], specification=True)["items"][0]
    assert other["notes"] == [] and other["note_total"] == 0


def test_init_returns_no_notes_titles_or_counts(world):
    store, ids = world
    one = ids["one"]
    store.create_note(
        "Secret title", "Secret text", refs(one, "task", "group", "workstream", "project")
    )
    ready = store.init(one["path"], "main")
    other = store.init(one["path"], "feature")
    for result in (ready, other):
        assert "Secret" not in json.dumps(result), result["state"]
        assert not note_keys(result), result["state"]


def note_keys(value):
    """Every key anywhere in a payload that mentions notes."""
    if isinstance(value, dict):
        return {k for k in value if "note" in k.lower()} | {
            key for child in value.values() for key in note_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in note_keys(child)}
    return set()


def test_set_note_and_the_viewer_notes_read_are_gone(world):
    store, ids = world
    assert not hasattr(store, "set_note") and not hasattr(store, "read_notes")
    with pytest.raises(TaskError, match="unknown_action"):
        dispatch(store, "notes", {"project": ids["one"]["project"]})


def test_mcp_tools_publish_the_note_contract(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    server = create_server(store, tracing=False)

    async def call(name, **arguments):
        result = await server.call_tool(name, arguments)
        assert not result.is_error, result.content
        return result.structured_content

    async def scenario():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert "set_note" not in tools
        assert {"create_note", "update_note", "get_notes", "list_notes"} <= tools.keys()
        for name in ("create_note", "update_note", "get_notes", "list_notes", "init"):
            assert len(tools[name].description) <= 300, name
        assert "notes" not in tools["init"].description
        assert tools["create_note"].annotations.destructive_hint is False
        assert tools["update_note"].annotations.destructive_hint is True
        assert set(tools["create_note"].input_schema["required"]) == {
            "title",
            "text",
            "references",
        }
        assert set(tools["update_note"].input_schema["required"]) == {
            "note_id",
            "expected_revision",
        }
        assert "note_id" in tools["list_events"].input_schema["properties"]
        assert "read the notes" not in server.instructions
        assert "notes" not in server.instructions.split("\n")[1]
        setup = await call(
            "init", path=str(tmp_path), branch="main", action="create_project", confirmed=True
        )
        ws = setup["workstream"]["id"]
        task = await call("create_task", project=setup["project"]["id"], title="T", public_id="t")
        ack = await call(
            "create_note",
            title="Deploy",
            text="Staging runs build 41.",
            references=[{"kind": "workstream", "id": ws}, {"kind": "task", "id": task["id"]}],
        )
        assert ack["reference_count"] == 2
        listed = await call("list_notes", reference={"kind": "task", "id": task["id"]})
        assert [n["title"] for n in listed["items"]] == ["Deploy"]
        updated = await call(
            "update_note", note_id=ack["id"], expected_revision=1, text="Rolled back to 40."
        )
        assert updated["revision"] == 2
        note = (await call("get_notes", ids=[ack["id"]]))["items"][0]
        assert note["text"] == "Rolled back to 40." and len(note["references"]) == 2
        spec = (await call("get_tasks", ids=[task["id"]], specification=True))["items"][0]
        assert spec["notes"] == [{k: note[k] for k in ("id", "title", "updated_at")}]
        history = await call("list_events", note_id=ack["id"], include_details=True)
        assert [e["action"] for e in history["items"]] == ["note.created", "note.updated"]
        resumed = await call("init", path=str(tmp_path), branch="main")
        assert "notes" not in resumed and "Deploy" not in json.dumps(resumed)
        with pytest.raises(ToolError, match="reference_kind_mismatch"):
            await server.call_tool(
                "create_note",
                {"title": "x", "text": "x", "references": [{"kind": "project", "id": ws}]},
            )
        with pytest.raises(ToolError, match="list_notes"):
            await server.call_tool("list_notes", {"reference": {"kind": "attempt", "id": ws}})

    asyncio.run(scenario())


def test_the_init_skill_no_longer_reads_notes():
    content = default_skills("init")["items"][0]["content"]
    assert "note" not in content.lower()


# The frozen schema-11 notes table, as the previous code created it.
SCHEMA11_NOTES = """CREATE TABLE notes (
    owner_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('project','workstream')),
    project_id TEXT NOT NULL REFERENCES projects(id),
    workstream_id TEXT REFERENCES workstreams(id),
    text TEXT NOT NULL CHECK(length(text) <= 2000),
    revision INTEGER NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
    CHECK(owner_id = coalesce(workstream_id, project_id)),
    CHECK((kind = 'workstream') = (workstream_id IS NOT NULL)))"""


def schema11_database(world):
    """Turn the world into a schema-11 database with project and workstream notes."""
    store, ids = world
    one, two = ids["one"], ids["two"]
    rows = [
        (one["project"], "project", one["project"], None, "Never push.", 3, "2026-01-02T03:04:05Z"),
        (one["workstream"], "workstream", one["project"], one["workstream"], "é" * 2000, 1,
         "2026-02-03T04:05:06Z"),
        # A cleared note keeps its row with empty text; it is not migrated.
        (two["project"], "project", two["project"], None, "", 2, "2026-03-04T05:06:07Z"),
    ]  # fmt: skip
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("DROP TABLE note_references")
        db.execute("DROP TABLE note_records")
        db.execute(SCHEMA11_NOTES)
        db.executemany("INSERT INTO notes VALUES (?,?,?,?,?,?,?,'legacy-agent')", rows)
        db.execute("PRAGMA user_version=11")
        db.commit()
    return store.path, ids


def snapshot(database):
    with closing(sqlite3.connect(database)) as db:
        return {
            table: db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for (table,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }


def test_schema11_notes_migrate_after_a_verified_backup(world):
    database, ids = schema11_database(world)
    one = ids["one"]
    before = snapshot(database)
    migrated = Store(database, actor="simon")
    backup = migrated.migration_backup_path
    assert backup.name.startswith("tasks.sqlite3.pre-schema-12.")
    assert backup.stat().st_mode & 0o777 == 0o600
    assert snapshot(backup) == before
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_REVISION == 12
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchone()
    after = snapshot(database)
    # Existing rows, the legacy notes table included, are unchanged; only notes and their
    # migration events are added.
    for table, rows in before.items():
        if table in {"events", "sqlite_sequence"}:
            assert after["events"][: len(before["events"])] == before["events"]
        else:
            assert after[table] == rows, table
    assert len(after["note_records"]) == 2 and len(after["note_references"]) == 2
    project_note = migrated.list_notes({"kind": "project", "id": one["project"]})["items"]
    ws_note = migrated.list_notes({"kind": "workstream", "id": one["workstream"]})["items"]
    assert [n["title"] for n in project_note] == ["Project note"]
    assert [n["title"] for n in ws_note] == ["Workstream note"]
    assert migrated.list_notes({"kind": "project", "id": ids["two"]["project"]})["total"] == 0
    project, workstream = migrated.get_notes([project_note[0]["id"], ws_note[0]["id"]])["items"]
    assert project | {"id": None} == {
        "id": None,
        "title": "Project note",
        "text": "Never push.",
        "references": [{"kind": "project", "id": one["project"]}],
        "archived": False,
        "revision": 1,
        "created_at": "2026-01-02T03:04:05Z",
        "updated_at": "2026-01-02T03:04:05Z",
        "created_by": "legacy-agent",
        "updated_by": "legacy-agent",
    }
    assert workstream["text"] == "é" * 2000
    assert workstream["updated_at"] == "2026-02-03T04:05:06Z"
    assert workstream["references"] == [{"kind": "workstream", "id": one["workstream"]}]
    (event,) = migrated.list_events(note_id=project["id"], include_details=True)["items"]
    assert event["action"] == "note.migrated" and event["project_id"] == one["project"]
    assert event["before"]["text"] == "Never push." and event["after"]["id"] == project["id"]
    # Migrated notes are ordinary notes; init still returns none of them.
    assert migrated.update_note(project["id"], 1, text="Rules moved to AGENTS.md")["revision"] == 2
    assert not note_keys(migrated.init(one["path"], "main"))
    assert Store(database).migration_backup_path is None
    assert len(snapshot(database)["note_records"]) == 2


def test_a_failed_note_migration_rolls_back_and_keeps_the_backup(world, monkeypatch):
    database, _ = schema11_database(world)
    before = snapshot(database)

    def broken(db):
        raise RuntimeError("note migration failed")

    monkeypatch.setattr(Store, "_migrate_notes", staticmethod(broken))
    with pytest.raises(RuntimeError, match="note migration failed"):
        Store(database)
    assert snapshot(database) == before
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
    (backup,) = database.parent.glob("*.pre-schema-12.*.sqlite3")
    assert snapshot(backup) == before


def test_a_new_database_has_no_legacy_notes_table(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    assert store.migration_backup_path is None
    with closing(sqlite3.connect(store.path)) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"note_records", "note_references"} <= names and "notes" not in names


PREVIOUS_SERVER = textwrap.dedent(
    """
    import asyncio, json, sys
    from pathlib import Path
    from task_mcp.server import create_server
    from task_mcp.store import Store

    database, path = Path(sys.argv[1]), sys.argv[2]
    if sys.argv[3] == "seed":
        store = Store(database, actor="old-agent")
        setup = store.init(path, "main", action="create_project", confirmed=True)
        project, ws = setup["project"]["id"], setup["workstream"]["id"]
        store.create_task(project, "Seeded", workstream_id=ws)
        store.set_note("project", project, 0, "Project rules")
        store.set_note("workstream", ws, 0, "Live state")
        print(json.dumps({"project": project, "ws": ws}))
        sys.exit()
    # Already running when the newer server migrates: start, then wait for the signal.
    store = Store(database, actor="old-agent")
    server = create_server(store, tracing=False)
    print("started", flush=True)
    sys.stdin.readline()
    ready = store.init(path, "main")
    project, ws = ready["project"]["id"], ready["workstream"]["id"]
    created = store.create_task(project, "Written by previous code", workstream_id=ws)
    resumed = asyncio.run(server.call_tool("init", {"path": path, "branch": "main"}))
    saved = store.set_note("workstream", ws, 1, "Written after the migration")
    try:
        Store(database)
    except RuntimeError as error:
        restart_error = str(error)
    else:
        raise AssertionError("old server accepted schema12")
    print(json.dumps({
        "state": ready["state"],
        "notes": {kind: note["text"] for kind, note in ready.get("notes", {}).items()},
        "mcp_error": resumed.is_error,
        "saved_revision": saved["revision"],
        "restart_error": restart_error,
    }))
    """
)


@pytest.mark.parametrize("label", sorted(PREVIOUS))
def test_running_schema11_code_survives_the_migration_but_cannot_restart(tmp_path, label):
    revision = PREVIOUS[label]
    if (
        subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-e", revision + "^{commit}"],
            capture_output=True,
        ).returncode
        != 0
    ):
        pytest.skip(f"previous revision {revision} is not in this checkout's history")
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", revision, "src/task_mcp"],
        capture_output=True,
        check=True,
    ).stdout
    previous = tmp_path / "previous"
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(previous, filter="data")
    script = tmp_path / "previous_server.py"
    script.write_text(PREVIOUS_SERVER)
    env = {
        **os.environ,
        "PYTHONPATH": str(previous / "src"),
        "TASK_MCP_TRACE": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    database, checkout = tmp_path / "tasks.sqlite3", tmp_path / "repo"
    checkout.mkdir()
    args = [sys.executable, str(script), str(database), str(checkout)]
    seeded = subprocess.run(
        [*args, "seed"], env=env, capture_output=True, text=True, timeout=60, check=True
    )
    ids = json.loads(seeded.stdout)
    running = subprocess.Popen(
        [*args, "run"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert running.stdout.readline().strip() == "started"
        current = Store(database, actor="simon")
        assert current.migration_backup_path.name.startswith("tasks.sqlite3.pre-schema-12.")
        with closing(sqlite3.connect(current.migration_backup_path)) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 11
            assert db.execute("SELECT count(*) FROM notes WHERE text<>''").fetchone()[0] == 2
        output, _ = running.communicate("go\n", timeout=60)
    finally:
        running.kill()
    assert running.returncode == 0, output
    report = json.loads(output.strip().splitlines()[-1])
    # The running server keeps its init and set_note on the frozen legacy table.
    assert report == {
        "state": "ready",
        "notes": {"project": "Project rules", "workstream": "Live state"},
        "mcp_error": False,
        "saved_revision": 2,
        "restart_error": "task database schema is newer than this server supports",
    }
    # The newer server has the notes as they were at migration; later legacy writes are
    # not carried over.
    (ws_note,) = current.list_notes({"kind": "workstream", "id": ids["ws"]})["items"]
    assert current.get_notes([ws_note["id"]])["items"][0]["text"] == "Live state"
    assert current.list_notes({"kind": "project", "id": ids["project"]})["total"] == 1
    titles = {card["title"] for card in current.init(str(checkout), "main")["queue"]}
    assert titles == {"Seeded", "Written by previous code"}
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 12
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchone()
