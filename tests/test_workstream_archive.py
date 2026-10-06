"""Archived workstreams: one audited, revision-checked tool; hidden from discovery by default."""

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

from task_mcp.server import create_server
from task_mcp.store import DATABASE_SCHEMA_REVISION, Store, TaskError
from task_mcp.viewer import dispatch

ROOT = Path(__file__).resolve().parents[1]
# Code that predates archiving: main as the live server runs it, and this branch with notes.
PREVIOUS = {
    "main": "078f2be0c8a1ea5418ef2f501dd9ec89232b0b6b",
    "workflow-overhead": "6fb27f8fe49143766eec4e7881c63e8a67d9e971",
}
# Rows archiving must never touch.
KEPT = (
    "tasks",
    "workstreams",
    "scope_members",
    "scope_groups",
    "scope_exclusions",
    "workstream_task_order",
    "attempts",
    "prerequisites",
)


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="simon")
    repo = str(tmp_path / "repo")
    made = store.init(repo, "main", action="create_project", confirmed=True)
    project, main = made["project"]["id"], made["workstream"]["id"]
    old = store.init(repo, "old", action="new_workstream", confirmed=True)["workstream"]["id"]
    shared = store.create_task(project, "Shared", workstream_id=old)
    only = store.create_task(project, "Only old", workstream_id=old)
    store.create_task(project, "Only main", workstream_id=main)
    store.add_to_workstream(shared["id"], main, shared["revision"])
    store.reorder_tasks(
        old, [only["id"], shared["id"]], store.workstream_status(old)["workstream_order_revision"]
    )
    task = store.get_tasks([only["id"]])["items"][0]
    store.record_result(
        task["id"],
        old,
        task["revision"],
        "worker",
        "Done",
        "Proof",
        [{"kind": "artifact", "reference": "tests/test_workstream_archive.py"}],
        "Checked",
        task["specification_etag"],
    )
    return store, repo, project, main, old, shared["id"], only["id"]


def snapshot(database):
    with closing(sqlite3.connect(database)) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall() for table in KEPT
        }


def tools(store):
    server = create_server(store, tracing=False)

    async def call(name, **arguments):
        result = await server.call_tool(name, arguments)
        assert not result.is_error, result.content
        return result.structured_content

    async def fail(name, **arguments):
        with pytest.raises(ToolError) as error:
            await server.call_tool(name, arguments)
        return str(error.value)

    return server, call, fail


def test_archive_and_unarchive_keep_tasks_memberships_order_and_proof(setup):
    store, repo, project, main, old, shared, only = setup
    server, call, fail = tools(store)
    before = snapshot(store.path)
    main_board = store.list_tasks(project, main)

    async def exercise():
        catalog = {tool.name: tool for tool in await server.list_tools()}
        assert list(catalog["archive_workstream"].input_schema["properties"]) == [
            "workstream_id",
            "expected_revision",
            "reason",
            "archived",
        ]
        for name in ("init", "list_workstreams", "workstream_status", "get_next_action"):
            assert "include_archived" in catalog[name].input_schema["properties"], name
        ack = await call(
            "archive_workstream", workstream_id=old, expected_revision=0, reason=" Snapshot only "
        )
        assert ack["changed"] and ack["workstream_id"] == old and ack["project_id"] == project
        assert ack["archive"] == ack["archive"] | {
            "archived": True,
            "reason": "Snapshot only",
            "revision": 1,
            "updated_by": "simon",
        }
        # Only the archive state changed: no task, membership, order, attempt or binding.
        assert snapshot(store.path) == before
        assert store.list_tasks(project, main) == main_board
        assert store.get_tasks([shared])["items"][0]["workstream_ids"] == sorted([main, old])
        # Stale revisions, missing or long reasons and unknown workstreams change nothing.
        assert "revision_conflict" in await fail(
            "archive_workstream", workstream_id=old, expected_revision=0, reason="again"
        )
        assert "archive_reason_required" in await fail(
            "archive_workstream", workstream_id=old, expected_revision=1, reason=" "
        )
        assert "archive_reason_too_long" in await fail(
            "archive_workstream",
            workstream_id=old,
            expected_revision=1,
            reason="x" * 201,
            archived=False,
        )
        assert "unknown_workstream" in await fail(
            "archive_workstream", workstream_id="wst_nope", expected_revision=0, reason="x"
        )
        # Archiving an archived workstream again is a no-op that keeps the first reason.
        again = await call(
            "archive_workstream", workstream_id=old, expected_revision=1, reason="other"
        )
        assert not again["changed"] and again["archive"]["reason"] == "Snapshot only"
        restored = await call(
            "archive_workstream",
            workstream_id=old,
            expected_revision=1,
            reason="Back in use",
            archived=False,
        )
        assert restored["changed"] and restored["archive"] == restored["archive"] | {
            "archived": False,
            "reason": "Back in use",
            "revision": 2,
        }
        assert snapshot(store.path) == before
        listed = await call("list_workstreams", project=project)
        assert [w["id"] for w in listed["items"]] == [main, old]
        assert listed["items"][1]["archive"]["archived"] is False
        assert "archive" not in listed["items"][0] and listed["archived_hidden"] == 0
        events = await call("list_events", project=project, include_details=True, limit=100)
        actions = {"workstream.archived", "workstream.unarchived"}
        audited = [e for e in events["items"] if e["action"] in actions]
        assert [(e["action"], e["outcome"]) for e in audited] == [
            ("workstream.archived", "ok"),
            ("workstream.archived", "error"),
            ("workstream.archived", "error"),
            ("workstream.unarchived", "error"),
            ("workstream.archived", "ok"),
            ("workstream.unarchived", "ok"),
        ]
        first = audited[0]
        assert first["actor"] == "simon" and first["before"]["archive"] is None
        assert first["after"]["archive"]["reason"] == "Snapshot only"
        assert audited[-1]["before"]["archive"]["archived"] is True
        assert audited[-1]["after"]["archive"]["archived"] is False

    asyncio.run(exercise())
    with pytest.raises(TaskError, match="invalid_archived"):
        store.archive_workstream(old, 2, "x", archived="no")


def test_archived_workstreams_are_hidden_from_discovery_until_requested(setup, tmp_path):
    store, repo, project, main, old, shared, only = setup
    store.archive_workstream(old, 0, "Inactive snapshot")

    listed = store.list_workstreams(project)
    assert [w["id"] for w in listed["items"]] == [main] and listed["archived_hidden"] == 1
    assert store.list_workstreams()["archived_hidden"] == 1
    every = store.list_workstreams(project, include_archived=True)
    assert [w["id"] for w in every["items"]] == [main, old] and "archived_hidden" not in every
    assert every["items"][1]["archive"]["reason"] == "Inactive snapshot"

    # Init discovery by checkout path leaves archived workstreams out of its candidates.
    fresh = store.init(repo, "topic")
    assert fresh["state"] == "new_branch" and fresh["archived_hidden"] == 1
    assert [w["id"] for w in fresh["candidates"]] == [main] and fresh["candidate_total"] == 1
    shown = store.init(repo, "topic", include_archived=True)
    assert [w["id"] for w in shown["candidates"]] == [main, old] and shown["candidate_total"] == 2
    assert "archived_hidden" not in shown
    loose = str(tmp_path / "loose")
    unregistered = store.init(loose, "topic", project=project)
    assert [w["id"] for w in unregistered["workstream_candidates"]] == [main]
    assert unregistered["archived_hidden"] == 1
    unregistered = store.init(loose, "topic", project=project, include_archived=True)
    assert [w["id"] for w in unregistered["workstream_candidates"]] == [main, old]

    # Its exact checkout reports the archive instead of silently resuming it.
    stopped = store.init(repo, "old")
    assert stopped["state"] == "archived" and "queue" not in stopped
    assert stopped["workstream"]["id"] == old and "Inactive snapshot" in stopped["message"]
    assert stopped["choices"] == ["include_archived", "unarchive_workstream"]
    assert store.init(repo, "old", workstream_id=old)["state"] == "archived"
    resumed = store.init(repo, "old", include_archived=True)
    assert resumed["state"] == "ready" and resumed["workstream"]["archive"]["archived"]
    assert resumed["queue_total"] == 2 and "Archived workstream" in resumed["message"]
    assert store.init(repo, "main")["message"] == "Workstream ready"

    # Status reports an archived workstream's counts but lists nothing unless requested.
    status = store.workstream_status(old, include_scope=True)
    assert status["listing_skipped"] == "archived" and "items" not in status
    assert "scope" not in status and "concern_tasks" not in status
    assert status["status"]["scoped_count"] == 2 and "include_archived=true" in status["message"]
    full = store.workstream_status(old, include_archived=True)
    assert [card["id"] for card in full["items"]] == [only, shared]
    assert full["workstream"]["archive"]["archived"]

    # Next-action selection skips it; the shared task still selects in the other workstream.
    skipped = store.get_next_action(old)
    assert skipped["action"] is None and skipped["diagnostics"]["workstream_archived"]
    assert skipped["archive"]["reason"] == "Inactive snapshot"
    assert store.get_next_action(old, include_archived=True)["action"] == "review"
    assert store.get_next_action(main)["action"] == "implement"
    assert shared in [card["id"] for card in store.workstream_status(main)["items"]]

    # Moving an archived workstream also needs the explicit argument.
    other = str(tmp_path / "other")
    revision = store.list_workstreams(project, include_archived=True)["items"][1]["revision"]
    with pytest.raises(TaskError, match="workstream_archived"):
        store.init(
            other,
            "old",
            action="rebind_workstream",
            workstream_id=old,
            expected_revision=revision,
            confirmed=True,
        )
    moved = store.init(
        other,
        "old",
        action="rebind_workstream",
        workstream_id=old,
        expected_revision=revision,
        confirmed=True,
        include_archived=True,
    )
    assert moved["state"] == "ready" and moved["workstream"]["archive"]["archived"]

    for flag in ("yes", 1, None):
        with pytest.raises(TaskError, match="invalid_include_archived"):
            store.list_workstreams(project, include_archived=flag)


def test_viewer_reads_archived_workstreams_only_on_request(setup):
    store, repo, project, main, old, *_ = setup
    store.archive_workstream(old, 0, "Inactive snapshot")
    assert [w["id"] for w in dispatch(store, "workstreams", {"project": project})["items"]] == [
        main
    ]
    shown = dispatch(store, "workstreams", {"project": project, "include_archived": True})
    assert [w["id"] for w in shown["items"]] == [main, old]
    with pytest.raises(TaskError, match="unknown_action"):
        dispatch(store, "archive", {"workstream_id": old})


def _without_archive_table(database):
    with closing(sqlite3.connect(database)) as db:
        db.execute("DROP TABLE workstream_archive")
        db.commit()


def test_existing_database_gains_the_table_after_a_verified_backup(setup):
    store, repo, project, main, old, *_ = setup
    _without_archive_table(store.path)
    before = snapshot(store.path)
    migrated = Store(store.path)
    backup = migrated.migration_backup_path
    assert backup is not None and backup.name.startswith("tasks.sqlite3.pre-workstream_archive.")
    assert backup.stat().st_mode & 0o777 == 0o600
    assert snapshot(backup) == before
    with closing(sqlite3.connect(backup)) as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='workstream_archive'"
        ).fetchone()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_REVISION == 11
    assert snapshot(store.path) == before
    assert Store(store.path).migration_backup_path is None
    assert migrated.archive_workstream(old, 0, "after migration")["archive"]["revision"] == 1


PREVIOUS_SERVER = textwrap.dedent(
    """
    import asyncio, json, sys
    from pathlib import Path
    from task_mcp.server import create_server
    from task_mcp.store import Store

    database, path = Path(sys.argv[1]), sys.argv[2]
    if sys.argv[3] == "seed":
        store = Store(database)
        setup = store.init(path, "main", action="create_project", confirmed=True)
        old = store.init(path, "old", action="new_workstream", confirmed=True)
        store.create_task(setup["project"]["id"], "Seeded", workstream_id=old["workstream"]["id"])
        print(json.dumps({"project": setup["project"]["id"], "old": old["workstream"]["id"]}))
        sys.exit()
    # Already running when the newer server migrates: start, then wait for the signal.
    store = Store(database)
    server = create_server(store, tracing=False)
    print("started", flush=True)
    sys.stdin.readline()
    ready = store.init(path, "old")
    project, ws = ready["project"]["id"], ready["workstream"]["id"]
    created = store.create_task(project, "Written by previous code", workstream_id=ws)
    store.update_task(created["id"], created["revision"], {"title": "Edited by previous code"})
    listed = store.list_tasks(project, ws)
    status = store.workstream_status(ws)
    action = store.get_next_action(ws)
    streams = store.list_workstreams(project)
    resumed = asyncio.run(server.call_tool("init", {"path": path, "branch": "old"}))
    # UUID identity migration requires newer code on restart.
    try:
        Store(database)
    except RuntimeError as error:
        restart_error = str(error)
    else:
        raise AssertionError("old server accepted schema11")
    print(json.dumps({
        "state": ready["state"],
        "titles": sorted(card["title"] for card in listed["items"]),
        "status": bool(status),
        "action": action["action"],
        "streams": len(streams["items"]),
        "mcp_error": resumed.is_error,
        "restart_error": restart_error,
    }))
    """
)


@pytest.mark.parametrize("label", sorted(PREVIOUS))
def test_previous_code_keeps_working_against_the_migrated_database(tmp_path, label):
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
        # The newer server migrates (with a backup) and archives a workstream meanwhile.
        current = Store(database, actor="simon")
        assert current.migration_backup_path.name.startswith("tasks.sqlite3.pre-schema-11.")
        current.archive_workstream(ids["old"], 0, "Inactive snapshot")
        output, _ = running.communicate("go\n", timeout=60)
    finally:
        running.kill()
    assert running.returncode == 0, output
    report = json.loads(output.strip().splitlines()[-1])
    # Previous code knows nothing of archiving and keeps its earlier behaviour.
    assert report == {
        "state": "ready",
        "titles": ["Edited by previous code", "Seeded"],
        "status": True,
        "action": "implement",
        "streams": 2,
        "mcp_error": False,
        "restart_error": "task database schema is newer than this server supports",
    }
    # The newer server sees the previous code's writes and the unchanged archive state.
    assert current.init(str(checkout), "old")["state"] == "archived"
    resumed = current.init(str(checkout), "old", include_archived=True)
    assert {card["title"] for card in resumed["queue"]} == {"Edited by previous code", "Seeded"}
    assert resumed["workstream"]["archive"]["revision"] == 1
    assert current.list_workstreams(ids["project"])["archived_hidden"] == 1
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchone()
