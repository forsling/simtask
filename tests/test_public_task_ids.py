"""Public references stay meaningful while private identity never crosses the API."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from uuid import UUID

import pytest

from task_mcp import store as store_module
from task_mcp.server import create_server
from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    setup = store.init(
        str(tmp_path / "checkout"), branch="main", action="create_project", confirmed=True
    )
    return store, setup["project"]["id"], setup["workstream"]["id"]


def create(context, public_id="readable-task-ids", **overrides):
    store, project, ws = context
    return store.create_task(
        project,
        "Readable task IDs",
        "Goal",
        "Verified",
        workstream_id=ws,
        public_id=public_id,
        **overrides,
    )


def deliver(store, task, ws):
    result = store.record_result(
        task["id"],
        ws,
        task["revision"],
        "builder",
        "Delivered",
        "Proof",
        [{"kind": "artifact", "reference": "result.txt"}],
        "Checked result",
        task["specification_etag"],
    )
    store.record_review(result["id"], 1, "independent-checker", "pass", "Checked")
    return store.signoff_task(
        task["id"],
        result["task_revision"],
        "approve",
        "User approved",
        result["id"],
        2,
    )


def test_readable_identity_covers_entire_lifecycle_and_stays_private(context):
    store, project, ws = context
    task = create(context)
    with closing(sqlite3.connect(store.path)) as db:
        private_id = db.execute(
            "SELECT internal_uuid FROM task_identities WHERE public_id=?", (task["id"],)
        ).fetchone()[0]
    assert UUID(private_id).version == 4
    assert task["id"] == "readable-task-ids"
    with pytest.raises(TaskError, match="unknown_task"):
        store.get_tasks([private_id])
    edited = store.update_task(
        task["id"],
        1,
        {"title": "A new title", "body": "A revised goal"},
        task["specification_etag"],
    )
    assert edited["id"] == task["id"]
    full = store.get_tasks([task["id"]])["items"][0]
    completed = deliver(store, full, ws)
    assert completed["status"] == "done" and completed["id"] == task["id"]
    outputs = [
        task,
        edited,
        full,
        completed,
        store.list_tasks(project),
        store.workstream_status(ws),
        store.list_events(task_id=task["id"], include_details=True),
    ]
    for format in ("markdown", "legacy"):
        outputs.append(store.export_workstream(ws, format=format))
    serialized = json.dumps(outputs)
    assert private_id not in serialized and "internal_uuid" not in serialized
    assert "readable-task-ids" in serialized
    with pytest.raises(TaskError, match="public_id_conflict.*closed task IDs cannot be reused"):
        create(context)
    with closing(sqlite3.connect(store.path)) as db:
        assert (
            db.execute(
                "SELECT internal_uuid FROM task_identities WHERE public_id=?", (task["id"],)
            ).fetchone()[0]
            == private_id
        )


def test_explicit_ids_are_globally_unique_and_concurrent_conflict_is_atomic(context, tmp_path):
    store, project, ws = context
    other = store.init(
        str(tmp_path / "other"), branch="main", action="create_project", confirmed=True
    )
    barrier = Barrier(2)

    def race():
        barrier.wait()
        try:
            return create(context)
        except TaskError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: race(), range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert (
        sum(isinstance(result, str) and "public_id_conflict" in result for result in results) == 1
    )
    with pytest.raises(TaskError, match="public_id_conflict"):
        store.create_task(other["project"]["id"], "Other project", public_id="readable-task-ids")
    with pytest.raises(TaskError, match="public_id_conflict"):
        store.create_group(ws, "Other group", public_id="readable-task-ids")
    assert len(store.list_tasks(project)["items"]) == 1
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM task_identities").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT count(*) FROM scope_members WHERE workstream_id=?", (ws,)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "public_id",
    ["tsk_123", "Uppercase", "double--dash", "trailing-", "1-leading", "a/b", "x" * 97, "", 123],
)
def test_invalid_ids_roll_back(context, public_id):
    with pytest.raises(TaskError, match="invalid_public_id"):
        create(context, public_id)
    assert context[0].list_tasks(context[1])["items"] == []


@pytest.mark.parametrize("kind", ["task", "group"])
@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("public_id", ["a", "e1e1e1e1", "a" * 32])
def test_hex_public_ids_coexist_with_legacy_bookmarks(
    context, monkeypatch, kind, explicit, public_id
):
    store, project, ws = context
    legacy_id = "tsk_" + public_id + "0" * (32 - len(public_id))
    with monkeypatch.context() as patch:
        patch.setattr(
            Store,
            "_public_task_id",
            staticmethod(lambda db, title, public_id=None: legacy_id),
        )
        if kind == "group":
            store.create_group(ws, "Existing group")
        else:
            create(context)
    legacy = store.get_tasks([legacy_id])["items"][0]
    if kind == "group":
        bookmark = store.resolve_prefix("group", public_id)
        new = store.create_group(ws, public_id, public_id=public_id if explicit else None)
        assert store.resolve_prefix("group", public_id) == bookmark
        assert bookmark["items"][0]["id"] == legacy_id
        exact = store.resolve_prefix("group", public_id, match="public_id")
        assert exact["items"][0]["id"] == public_id
    else:
        new = store.create_task(
            project, public_id, workstream_id=ws, public_id=public_id if explicit else None
        )
    assert new["id"] == public_id
    assert store.get_tasks([public_id])["items"][0]["id"] == public_id
    assert store.get_tasks([legacy_id])["items"][0] == legacy


def test_derived_names_ideas_and_decomposition(context):
    store, project, ws = context
    assert create(context, None)["id"] == "readable-task-ids"
    assert create(context, None)["id"] == "readable-task-ids-2"
    idea = store.capture_idea(project, "Fix task references", "An idea")
    assert idea["id"] == "fix-task-references"
    assert store.capture_idea(project, "Fix task references")["id"] == "fix-task-references-2"
    assert store.capture_idea(project, "東京")["id"] == "task"
    group = create(context, "reference-redesign")
    result = store.decompose_task(
        group["id"],
        1,
        [
            {"title": "Public IDs", "public_id": "reference-storage"},
            {"title": "Viewer labels", "public_id": "reference-viewer"},
        ],
    )
    assert result["id"] == group["id"] and result["object_type"] == "group"
    members = store.list_tasks(group_id=group["id"], include=["ids"])["items"]
    assert {member["id"] for member in members} == {"reference-storage", "reference-viewer"}
    assert all(member["parent_group_id"] == group["id"] for member in members)
    before = create(context, "collision-parent")
    with pytest.raises(TaskError, match="public_id_conflict"):
        store.decompose_task(
            before["id"],
            1,
            [
                {"title": "New", "public_id": "new-member"},
                {"title": "Conflict", "public_id": "reference-viewer"},
            ],
        )
    assert store.get_tasks([before["id"]])["items"][0]["object_type"] == "task"
    with pytest.raises(TaskError, match="unknown_task"):
        store.get_tasks(["new-member"])
    assert (
        store.resolve_prefix("group", "reference-redesign", match="public_id")["items"][0]["id"]
        == group["id"]
    )
    assert (
        store.resolve_prefix("group", "reference-redesign-missing", match="public_id")["items"]
        == []
    )


def snapshot(db):
    tables = [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        table: db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in tables
    }


def legacy_database(context, monkeypatch):
    store, project, ws = context
    with monkeypatch.context() as patch:
        patch.setattr(
            Store,
            "_public_task_id",
            staticmethod(lambda db, title, public_id=None: store_module._id("tsk_")),
        )
        task = create(context)
        done = deliver(store, task, ws)
        blocker = create(context)
        child = create(context)
        store.add_prerequisite(child["id"], 1, blocker["id"])
        store.add_unresolved(blocker["id"], 1, "Design question")
        store.decompose_task(child["id"], 2, [{"title": "Member"}])
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("DROP TABLE task_identities")
        db.execute("PRAGMA user_version=10")
        db.commit()
        original = snapshot(db)
    return store.path, done, original


def test_schema10_migration_preserves_existing_ids_every_row_and_proof(context, monkeypatch):
    path, done, original = legacy_database(context, monkeypatch)
    upgraded = Store(path)
    assert upgraded.migration_backup_path is not None
    with closing(sqlite3.connect(path)) as db:
        migrated = snapshot(db)
        identities = migrated.pop("task_identities")
        assert migrated == original
        assert {public for _, public in identities} == {row[0] for row in original["tasks"]}
        assert all(UUID(private).version == 4 for private, _ in identities)
    with closing(sqlite3.connect(upgraded.migration_backup_path)) as backup:
        assert snapshot(backup) == original
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 10
    assert upgraded.get_tasks([done["id"]])["items"][0]["id"].startswith("tsk_")
    assert Store(path).migration_backup_path is None


def test_identity_migration_failure_rolls_back_and_keeps_verified_backup(context, monkeypatch):
    path, _, original = legacy_database(context, monkeypatch)
    # Backup naming needs one UUID before the identity backfill failure.
    real_uuid = __import__("uuid").uuid4
    calls = iter([real_uuid()])

    def failing_uuid():
        try:
            return next(calls)
        except StopIteration:
            raise RuntimeError("UUID unavailable") from None

    monkeypatch.setattr(store_module, "uuid4", failing_uuid)
    with pytest.raises(RuntimeError, match="UUID unavailable"):
        Store(path)
    with closing(sqlite3.connect(path)) as db:
        assert snapshot(db) == original
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
    assert list(path.parent.glob("*.pre-schema-11.*.sqlite3"))


def test_mcp_catalog_exposes_public_id_and_returns_only_public_reference(context):
    asyncio.run(check_mcp(context))


async def check_mcp(context):
    server = create_server(context[0], tracing=False)
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert "public_id" in tools["create_task"].input_schema["properties"]
    assert "create_group" not in tools
    group_response = await server.call_tool(
        "create_task",
        {
            "project": context[1],
            "workstream_id": context[2],
            "kind": "group",
            "title": "Readable group",
            "public_id": "mcp-readable-group",
        },
    )
    assert "mcp-readable-group" in str(group_response)
    assert "internal_uuid" not in str(group_response)
    assert context[0].get_tasks(["mcp-readable-group"])["items"][0]["object_type"] == "group"
    response = await server.call_tool(
        "create_task",
        {"project": context[1], "title": "Readable IDs", "public_id": "mcp-readable-reference"},
    )
    assert "mcp-readable-reference" in str(response)
    assert "internal_uuid" not in str(response)
