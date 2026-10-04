"""Bounded personal project/workstream notes: one audited, revision-checked tool and init."""

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
from task_mcp.store import DATABASE_SCHEMA_REVISION, NOTE_LIMIT, Store, TaskError
from task_mcp.viewer import dispatch

ROOT = Path(__file__).resolve().parents[1]
# Code that predates notes: main as the live server runs it, and this branch before notes.
PREVIOUS = {
    "main": "078f2be0c8a1ea5418ef2f501dd9ec89232b0b6b",
    "workflow-overhead": "86e14e820936ab19664985eb188ba34963d99ebe",
}


@pytest.fixture
def ready(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="simon")
    setup = store.init(str(tmp_path), "main", action="create_project", confirmed=True)
    return store, setup["project"]["id"], setup["workstream"]["id"], str(tmp_path)


def test_notes_are_set_replaced_cleared_and_shown_by_init(ready):
    store, project, ws, path = ready
    assert "notes" not in store.init(path, "main")
    ack = store.set_note("project", project, 0, "Never push to origin.")
    assert ack == ack | {
        "kind": "project",
        "target_id": project,
        "revision": 1,
        "length": 21,
        "limit": NOTE_LIMIT,
        "cleared": False,
        "updated_by": "simon",
        "changed": True,
    }
    assert "text" not in ack
    # Only the nonempty project note is shown.
    notes = store.init(path, "main")["notes"]
    assert set(notes) == {"project"}
    assert notes["project"] == {
        "text": "Never push to origin.",
        "revision": 1,
        "updated_at": ack["updated_at"],
        "updated_by": "simon",
    }
    store.set_note("workstream", ws, 0, "Deployed build 41 to staging.\nRollback: build 40.")
    replaced = store.set_note("workstream", ws, 1, "Rolled back to build 40.")
    assert replaced["revision"] == 2
    notes = store.init(path, "main")["notes"]
    assert notes["workstream"]["text"] == "Rolled back to build 40."
    assert notes["workstream"]["revision"] == 2
    # Saving identical text changes nothing.
    same = store.set_note("workstream", ws, 2, "Rolled back to build 40.")
    assert same["changed"] is False and same["revision"] == 2
    cleared = store.set_note("workstream", ws, 2, "")
    assert cleared["cleared"] is True and cleared["revision"] == 3
    assert set(store.init(path, "main")["notes"]) == {"project"}
    # Whitespace-only text also clears; clearing an empty note is a no-op.
    store.set_note("project", project, 1, "   \n")
    assert "notes" not in store.init(path, "main")
    assert store.set_note("project", project, 0, "")["changed"] is False


def test_saves_are_revision_checked_and_never_reuse_a_cleared_revision(ready):
    store, project, ws, _ = ready
    store.set_note("workstream", ws, 0, "first")
    with pytest.raises(TaskError, match="revision_conflict: expected 0, current 1"):
        store.set_note("workstream", ws, 0, "blind overwrite")
    store.set_note("workstream", ws, 1, "")
    # An empty note (omitted by init) is saved with 0, or with its actual revision.
    assert store.set_note("workstream", ws, 0, "second")["revision"] == 3
    # A writer holding revision 1 cannot overwrite text it never saw.
    with pytest.raises(TaskError, match="revision_conflict: expected 1, current 3"):
        store.set_note("workstream", ws, 1, "stale")
    store.set_note("workstream", ws, 3, "")
    assert store.set_note("workstream", ws, 4, "third")["revision"] == 5
    for bad in (None, "1", True):
        with pytest.raises(TaskError, match="revision_conflict"):
            store.set_note("workstream", ws, bad, "x")


def test_the_limit_is_exact_and_the_error_states_limit_and_length(ready):
    store, project, _, path = ready
    assert store.set_note("project", project, 0, "é" * NOTE_LIMIT)["length"] == 2000
    with pytest.raises(TaskError) as error:
        store.set_note("project", project, 1, "x" * 2345)
    assert str(error.value).startswith("note_too_long:")
    assert "at most 2,000 characters" in str(error.value)
    assert "this text has 2,345" in str(error.value)
    assert store.init(path, "main")["notes"]["project"]["text"] == "é" * NOTE_LIMIT
    with closing(sqlite3.connect(store.path)) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE notes SET text=?", ("x" * 2001,))


def test_targets_are_validated_and_projects_resolve_by_path(ready, tmp_path):
    store, project, ws, path = ready
    with pytest.raises(TaskError, match="invalid_note_kind"):
        store.set_note("group", project, 0, "x")
    with pytest.raises(TaskError, match="unknown_workstream"):
        store.set_note("workstream", "wst_missing", 0, "x")
    with pytest.raises(TaskError, match="unknown_workstream"):
        store.set_note("workstream", project, 0, "x")
    with pytest.raises(TaskError, match="project_not_initialized"):
        store.set_note("project", ws, 0, "x")
    with pytest.raises(TaskError, match="invalid_note_text"):
        store.set_note("project", project, 0, None)
    assert store.set_note("project", path, 0, "by path")["target_id"] == project
    # Notes belong to their own project and workstream only.
    other = store.init(path, "feature", action="new_workstream", confirmed=True)
    store.set_note("workstream", ws, 0, "main only")
    shown = store.init(path, "feature")
    assert shown["workstream"]["id"] == other["workstream"]["id"]
    assert set(shown["notes"]) == {"project"}


def test_every_save_is_audited_with_actor_before_and_after(ready):
    store, project, ws, _ = ready
    store.set_note("workstream", ws, 0, "one")
    store.set_note("workstream", ws, 1, "two")
    with pytest.raises(TaskError):
        store.set_note("workstream", ws, 1, "x" * 2001)
    events = [
        e
        for e in store.list_events(project=project, include_details=True, limit=50)["items"]
        if e["action"] == "note.set"
    ]
    assert [e["outcome"] for e in events] == ["ok", "ok", "error"]
    assert all(e["actor"] == "simon" for e in events)
    assert events[0]["before"] is None and events[0]["after"]["text"] == "one"
    assert events[1]["before"]["text"] == "one" and events[1]["after"]["text"] == "two"
    assert events[1]["after"]["revision"] == 2
    assert "note_too_long" in events[2]["error"]


def test_mcp_tool_sets_notes_and_init_returns_them(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    server = create_server(store, tracing=False)

    async def call(name, **arguments):
        result = await server.call_tool(name, arguments)
        assert not result.is_error, result.content
        return result.structured_content

    async def scenario():
        tools = {tool.name: tool for tool in await server.list_tools()}
        described = tools["set_note"].description
        assert "personal, uncommitted" in described and "2,000 characters" in described
        assert tools["set_note"].annotations.destructive_hint is True
        assert set(tools["set_note"].input_schema["required"]) == {
            "kind",
            "target_id",
            "expected_revision",
            "text",
        }
        setup = await call(
            "init", path=str(tmp_path), branch="main", action="create_project", confirmed=True
        )
        assert "notes" not in setup
        ws = setup["workstream"]["id"]
        ack = await call(
            "set_note", kind="workstream", target_id=ws, expected_revision=0, text="Live: v2"
        )
        assert ack["changed"] is True and ack["revision"] == 1
        resumed = await call("init", path=str(tmp_path), branch="main")
        assert resumed["notes"]["workstream"]["text"] == "Live: v2"
        assert resumed["notes"]["workstream"]["updated_by"] == "local-agent"
        with pytest.raises(ToolError, match=r"at most 2,000 characters; this text has 2,001"):
            await server.call_tool(
                "set_note",
                {"kind": "workstream", "target_id": ws, "expected_revision": 1, "text": "x" * 2001},
            )
        assert "set_note" in server.instructions and "notes" in server.instructions

    asyncio.run(scenario())


def test_viewer_reads_notes_for_its_project_and_workstream(ready):
    store, project, ws, _ = ready
    store.set_note("project", project, 0, "Rules")
    store.set_note("workstream", ws, 0, "State")
    shown = dispatch(store, "notes", {"project": project, "workstream_id": ws})["notes"]
    assert {kind: note["text"] for kind, note in shown.items()} == {
        "project": "Rules",
        "workstream": "State",
    }
    assert set(dispatch(store, "notes", {"project": project})["notes"]) == {"project"}
    with pytest.raises(TaskError, match="unknown_workstream"):
        dispatch(store, "notes", {"project": project, "workstream_id": "wst_other"})


def _without_notes_table(database):
    with closing(sqlite3.connect(database)) as db:
        db.execute("DROP TABLE notes")
        db.commit()


def test_existing_database_gains_the_table_after_a_verified_backup(ready):
    store, project, _, path = ready
    store.create_task(project, "Existing")
    _without_notes_table(store.path)
    with closing(sqlite3.connect(store.path)) as db:
        before = db.execute("SELECT * FROM tasks").fetchall()
    migrated = Store(store.path)
    backup = migrated.migration_backup_path
    assert backup is not None and backup.name.startswith("tasks.sqlite3.pre-notes.")
    with closing(sqlite3.connect(backup)) as db:
        assert db.execute("SELECT * FROM tasks").fetchall() == before
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='notes'").fetchone()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_REVISION == 10
        assert db.execute("SELECT * FROM tasks").fetchall() == before
    assert Store(store.path).migration_backup_path is None
    assert migrated.set_note("project", project, 0, "after migration")["revision"] == 1


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
        store.create_task(setup["project"]["id"], "Seeded", workstream_id=setup["workstream"]["id"])
        print(json.dumps({"project": setup["project"]["id"], "ws": setup["workstream"]["id"]}))
        sys.exit()
    # Already running when the newer server migrates: start, then wait for the signal.
    store = Store(database)
    server = create_server(store, tracing=False)
    print("started", flush=True)
    sys.stdin.readline()
    ready = store.init(path, "main")
    project, ws = ready["project"]["id"], ready["workstream"]["id"]
    created = store.create_task(project, "Written by previous code", workstream_id=ws)
    store.update_task(created["id"], created["revision"], {"title": "Edited by previous code"})
    listed = store.list_tasks(project, ws)
    status = store.workstream_status(ws)
    events = store.list_events(project=project, include_details=True, limit=100)
    resumed = asyncio.run(server.call_tool("init", {"path": path, "branch": "main"}))
    # A previous-code server started after the migration opens the database too.
    restarted = Store(database)
    after_restart = restarted.init(path, "main")
    print(json.dumps({
        "state": ready["state"],
        "titles": sorted(card["title"] for card in listed["items"]),
        "status": bool(status),
        "note_events": [e["action"] for e in events["items"] if e["action"] == "note.set"],
        "mcp_error": resumed.is_error,
        "restart_state": after_restart["state"],
        "restart_backup": str(restarted.migration_backup_path),
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
    env = {**os.environ, "PYTHONPATH": str(previous / "src"), "TASK_MCP_TRACE": "0"}
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
        # The newer server migrates (with a backup) and writes notes meanwhile.
        current = Store(database, actor="simon")
        assert current.migration_backup_path.name.startswith("tasks.sqlite3.pre-notes-")
        current.set_note("project", ids["project"], 0, "Project rules")
        current.set_note("workstream", ids["ws"], 0, "Live state")
        output, _ = running.communicate("go\n", timeout=60)
    finally:
        running.kill()
    assert running.returncode == 0, output
    report = json.loads(output.strip().splitlines()[-1])
    assert report == {
        "state": "ready",
        "titles": ["Edited by previous code", "Seeded"],
        "status": True,
        "note_events": ["note.set", "note.set"],
        "mcp_error": False,
        "restart_state": "ready",
        "restart_backup": "None",
    }
    # The newer server sees both the previous code's writes and its unchanged notes.
    resumed = current.init(str(checkout), "main")
    assert {card["title"] for card in resumed["queue"]} == {"Edited by previous code", "Seeded"}
    assert {kind: note["text"] for kind, note in resumed["notes"].items()} == {
        "project": "Project rules",
        "workstream": "Live state",
    }
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchone()
