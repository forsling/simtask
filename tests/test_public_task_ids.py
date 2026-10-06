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
from task_mcp.store import Store, TaskError, short_task_slug


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
@pytest.mark.parametrize("public_id", ["a", "e1e1e1e1", "a" * 30, "a" * 32])
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
    # A derived ID is the title's short slug: a 32-character title is cut to 30.
    expected = public_id if explicit else short_task_slug(public_id)
    if kind == "group":
        bookmark = store.resolve_prefix("group", public_id)
        new = store.create_group(ws, public_id, public_id=public_id if explicit else None)
        assert store.resolve_prefix("group", public_id) == bookmark
        assert bookmark["items"][0]["id"] == legacy_id
        exact = store.resolve_prefix("group", expected, match="public_id")
        assert exact["items"][0]["id"] == expected
    else:
        new = store.create_task(
            project, public_id, workstream_id=ws, public_id=public_id if explicit else None
        )
    assert new["id"] == expected
    assert store.get_tasks([expected])["items"][0]["id"] == expected
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


@pytest.mark.parametrize(
    "title, slug",
    [
        (
            "Count each workstream's Needs input on the server instead of re-reading "
            "every card list",
            "count-workstreams-needs-input",
        ),
        (
            'Cap task IDs at 40 characters, let ideas set short IDs, restore "go through my ideas"',
            "cap-task-ids-40-characters",
        ),
        ("Readable task IDs", "readable-task-ids"),
        ("Show the current task\u2019s title in the browser tab", "show-current-tasks-title"),
        ("Café crème brûlée", "cafe-creme-brulee"),
        ("2FA for the viewer", "task-2fa-viewer"),
        ("The", "the"),
        ("東京", "task"),
        ("x" * 50 + " more words", "x" * 30),
        ("one two three four five six seven", "one-two-three-four-five"),
    ],
)
def test_automatic_ids_are_short_word_boundary_slugs(title, slug):
    """Filler words go; at most five words, cut on a word boundary at 30 characters."""
    assert short_task_slug(title) == slug
    assert len(slug) <= store_module.AUTO_ID_TARGET


def test_automatic_ids_with_suffix_never_exceed_the_limit(context, monkeypatch):
    store, project, _ = context
    assert create(context, None)["id"] == "readable-task-ids"
    assert create(context, None)["id"] == "readable-task-ids-2"
    # Even a base at the full limit keeps every suffixed ID within 40 characters.
    monkeypatch.setattr(store_module, "short_task_slug", lambda title: "b" * 40)
    ids = [store.create_task(project, "Long base")["id"] for _ in range(3)]
    assert ids == ["b" * 40, "b" * 38 + "-2", "b" * 38 + "-3"]
    assert all(len(task_id) <= store_module.PUBLIC_ID_LIMIT for task_id in ids)


@pytest.mark.parametrize("kind", ["task", "group", "member", "idea"])
def test_explicit_ids_over_40_characters_are_refused_and_save_nothing(context, kind):
    store, project, ws = context
    parent = create(context, "parent-task") if kind == "member" else None
    with closing(sqlite3.connect(store.path)) as db:
        before = snapshot(db)
    too_long = "a" + "-b" * 20  # 41 characters
    attempts = {
        "task": lambda: store.create_task(project, "Too long", public_id=too_long),
        "group": lambda: store.create_group(ws, "Too long", public_id=too_long),
        "member": lambda: store.decompose_task(
            parent["id"], 1, [{"title": "Too long", "public_id": too_long}]
        ),
        "idea": lambda: store.capture_idea(project, "Too long", public_id=too_long),
    }
    with pytest.raises(TaskError, match="invalid_public_id: .*at most 40 characters.* has 41"):
        attempts[kind]()
    with closing(sqlite3.connect(store.path)) as db:
        after = snapshot(db)
        after.pop("events"), before.pop("events")
        assert after == before
    assert store.create_task(project, "Exactly 40", public_id="c" * 40)["id"] == "c" * 40


def test_existing_longer_ids_stay_valid_and_resolvable(context, monkeypatch):
    store, project, ws = context
    long_task, long_group = "t" + "-long" * 15, "g" + "-long" * 15  # 76 characters
    for title, public_id in (("Old task", long_task), ("Old group", long_group)):
        with monkeypatch.context() as patch:
            patch.setattr(
                Store,
                "_public_task_id",
                staticmethod(lambda db, title, public_id=None, value=public_id: value),
            )
            if title == "Old group":
                store.create_group(ws, title)
            else:
                store.create_task(project, title, workstream_id=ws)
    assert store.get_tasks([long_task])["items"][0]["title"] == "Old task"
    assert store.get_tasks([long_group])["items"][0]["object_type"] == "group"
    exact = store.resolve_prefix("group", long_group, match="public_id")
    assert exact["items"][0]["id"] == long_group
    assert store.update_task(long_task, 1, {"title": "Renamed"})["id"] == long_task


def test_suggested_id_matches_what_creation_assigns_without_an_audit(context):
    store, project, _ = context
    title = "Count each workstream's Needs input on the server"

    def events():
        with closing(sqlite3.connect(store.path)) as db:
            return db.execute("SELECT count(*) FROM events").fetchone()[0]

    before = events()
    assert store.suggest_task_id(title) == {
        "public_id": "count-workstreams-needs-input",
        "limit": 40,
    }
    assert events() == before
    assert store.capture_idea(project, title)["id"] == "count-workstreams-needs-input"
    suggested = store.suggest_task_id(title)["public_id"]
    assert suggested == "count-workstreams-needs-input-2"
    assert store.create_task(project, title)["id"] == suggested
    assert events() == before + 2
    with pytest.raises(TaskError, match="invalid_title"):
        store.suggest_task_id(None)


def test_ideas_take_an_optional_custom_id(context):
    store, project, _ = context
    idea = store.capture_idea(project, "Dark mode for the viewer", public_id="dark-mode")
    assert idea["id"] == "dark-mode"
    with pytest.raises(TaskError, match="public_id_conflict"):
        store.capture_idea(project, "Dark mode again", public_id="dark-mode")
    with pytest.raises(TaskError, match="invalid_public_id: use up to 40"):
        store.capture_idea(project, "Bad", public_id="Dark Mode")
    assert [row["id"] for row in store.list_tasks(project)["items"]] == ["dark-mode"]


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
