"""Picked markers: get_next_action notes what it handed out, as information only."""

import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import textwrap
from contextlib import closing
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest

from task_mcp.store import DATABASE_SCHEMA_REVISION, Store
from task_mcp.viewer import dispatch

ROOT = Path(__file__).resolve().parents[1]
# Code that predates picked markers: main as the live server runs it, and this branch.
PREVIOUS = {
    "main": "e038e690a537a41c44ca262a5e1540c2aaa2e028",
    "workflow-overhead": "29d710fa8f27570408ddec53dee04a1cf73cfde8",
}


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="simon")
    repo = str(tmp_path / "repo")
    made = store.init(repo, "main", action="create_project", confirmed=True)
    project, main = made["project"]["id"], made["workstream"]["id"]
    side = store.init(repo, "side", action="new_workstream", confirmed=True)["workstream"]["id"]
    first = store.create_task(project, "First", workstream_id=main)
    second = store.create_task(project, "Second", workstream_id=main)
    store.add_to_workstream(first["id"], side, first["revision"])
    return store, project, main, side, first["id"], second["id"]


def picks(store):
    with closing(sqlite3.connect(store.path)) as db:
        return db.execute(
            "SELECT task_id, workstream_id, action, picked_at FROM task_picks ORDER BY 1, 2"
        ).fetchall()


def age(store, hours):
    """Move every recorded pick into the past."""
    when = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="microseconds")
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("UPDATE task_picks SET picked_at=?", (when.replace("+00:00", "Z"),))
        db.commit()


def viewer_card(store, project, task_id, workstream_id=None):
    board = dispatch(store, "tasks", {"project": project, "workstream_id": workstream_id})
    return next(card for card in board["items"] if card["id"] == task_id)


def record(store, task_id, workstream_id):
    task = store.get_tasks([task_id])["items"][0]
    return store.record_result(
        task_id,
        workstream_id,
        task["revision"],
        "worker",
        "Done",
        "Proof",
        [{"kind": "artifact", "reference": "tests/test_picks.py"}],
        "Checked",
        task["specification_etag"],
    )


def test_next_action_records_an_information_only_pick(setup):
    store, project, main, side, first, second = setup
    assert picks(store) == []
    action = store.get_next_action(main)
    assert (action["action"], action["task"]["id"]) == ("implement", first)
    [(task_id, ws, kind, picked_at)] = picks(store)
    assert (task_id, ws, kind) == (first, main, "implement")
    # Selection is unaffected: the picked task is handed out again, not skipped or locked.
    again = store.get_next_action(main)
    assert again["task"]["id"] == first
    [(_, _, _, replaced)] = picks(store)
    assert replaced >= picked_at, "a new pick of the same task replaces the marker"
    # The viewer's cards and the full read show it; slim agent cards stay slim.
    card = viewer_card(store, project, first, main)
    assert card["picked"] == {"workstream_id": main, "action": "implement", "picked_at": replaced}
    assert "picked" not in viewer_card(store, project, second, main)
    assert viewer_card(store, project, first)["picked"]["workstream_id"] == main
    assert "picked" not in viewer_card(store, project, first, side), "picks are per workstream"
    assert store.get_tasks([first])["items"][0]["picks"] == [card["picked"]]
    assert "picks" not in store.get_tasks([second])["items"][0]
    slim = store.list_tasks(project, main)["items"][0]
    assert slim["id"] == first and "picked" not in slim
    detailed = store.list_tasks(project, main, include=["attempt"])["items"][0]
    assert detailed["picked"] == card["picked"]
    # The selected specification carries no pick of itself.
    assert "picks" not in again["task"]
    # Another workstream's pick is its own marker.
    assert store.get_next_action(side)["task"]["id"] == first
    assert [(t, w) for t, w, *_ in picks(store)] == sorted([(first, main), (first, side)])


def test_picks_expire_after_about_four_hours(setup):
    store, project, main, _, first, _ = setup
    store.get_next_action(main)
    age(store, 3.9)
    assert viewer_card(store, project, first, main)["picked"]["action"] == "implement"
    age(store, 4.1)
    card = viewer_card(store, project, first, main)
    assert "picked" not in card
    assert "picks" not in store.get_tasks([first])["items"][0]
    # The next pick prunes expired markers and records a fresh one.
    store.get_next_action(main)
    [(_, _, _, picked_at)] = picks(store)
    assert "picked" in viewer_card(store, project, first, main)
    assert picked_at > (datetime.now(UTC) - timedelta(minutes=5)).isoformat()[:19]


def test_a_newer_result_or_review_clears_the_pick(setup):
    store, project, main, side, first, _ = setup
    store.get_next_action(main)
    store.get_next_action(side)
    record(store, first, main)
    # Ignored in the workstream with the newer result; the other workstream's pick stays.
    assert "picked" not in viewer_card(store, project, first, main)
    assert viewer_card(store, project, first, side)["picked"]["workstream_id"] == side
    assert [p["workstream_id"] for p in store.get_tasks([first])["items"][0]["picks"]] == [side]
    # The review is handed out and picked in turn; recording it clears that pick.
    review = store.get_next_action(main)
    assert (review["action"], review["task"]["id"]) == ("review", first)
    assert viewer_card(store, project, first, main)["picked"]["action"] == "review"
    proof = review["attempt"]
    store.record_review(proof["id"], proof["revision"], "reviewer", "pass", "Checked")
    assert "picked" not in viewer_card(store, project, first, main)


def test_no_action_records_no_pick(setup):
    store, project, main, _, first, second = setup
    for task_id in (first, second):
        task = store.get_tasks([task_id])["items"][0]
        store.add_unresolved(task_id, task["revision"], "Which variant?")
    assert store.get_next_action(main)["action"] is None
    assert picks(store) == []


PREVIOUS_SERVER = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    from task_mcp.store import Store

    database, path = Path(sys.argv[1]), sys.argv[2]
    if sys.argv[3] == "seed":
        store = Store(database)
        setup = store.init(path, "main", action="create_project", confirmed=True)
        task = store.create_task(setup["project"]["id"], "Seeded",
                                 workstream_id=setup["workstream"]["id"])
        print(json.dumps({"project": setup["project"]["id"],
                          "workstream": setup["workstream"]["id"], "task": task["id"]}))
        sys.exit()
    # Already running when the newer server adds the table and records a pick.
    store = Store(database)
    print("started", flush=True)
    sys.stdin.readline()
    ready = store.init(path, "main")
    ws = ready["workstream"]["id"]
    action = store.get_next_action(ws)
    task = store.get_tasks([action["task"]["id"]])["items"][0]
    store.record_result(task["id"], ws, task["revision"], "worker", "Done", "Proof",
                        [{"kind": "artifact", "reference": "previous"}], "Checked",
                        task["specification_etag"])
    listed = store.list_tasks(ready["project"]["id"], ws)
    try:
        Store(database)
    except RuntimeError as error:
        restart_error = str(error)
    else:
        raise AssertionError("old server accepted schema11")
    print(json.dumps({
        "state": ready["state"],
        "action": action["action"],
        "titles": [card["title"] for card in listed["items"]],
        "restart_error": restart_error,
    }))
    """
)


@pytest.mark.parametrize("label", sorted(PREVIOUS))
def test_previous_code_keeps_working_against_a_database_with_picks(tmp_path, label):
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
        backup = current.migration_backup_path
        assert backup.name.startswith(f"tasks.sqlite3.pre-schema-{DATABASE_SCHEMA_REVISION}.")
        with closing(sqlite3.connect(backup)) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='task_picks'").fetchone()
        assert current.get_next_action(ids["workstream"])["task"]["id"] == ids["task"]
        assert "picked" in viewer_card(current, ids["project"], ids["task"], ids["workstream"])
        output, _ = running.communicate("go\n", timeout=60)
    finally:
        running.kill()
    assert running.returncode == 0, output
    report = json.loads(output.strip().splitlines()[-1])
    # Previous code ignores the marker: the picked task is still selected and recorded.
    assert report == {
        "state": "ready",
        "action": "implement",
        "titles": ["Seeded"],
        "restart_error": "task database schema is newer than this server supports",
    }
    # Its result, recorded without knowing of picks, still clears the marker here.
    card = viewer_card(current, ids["project"], ids["task"], ids["workstream"])
    assert "picked" not in card and card["attempt_counts"]["review"] == 1
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_REVISION
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchone()
