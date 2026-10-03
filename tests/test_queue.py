"""Single owning branch, atomic moves and gate-independent placement."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="queue-coordinator")
    setup = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    project, ws = setup["project"]["id"], setup["workstream"]["id"]
    other = store.init_workstream(
        project, str(tmp_path / "repo"), branch="feature", confirmed=True
    )["workstream"]["id"]
    return store, project, ws, other


def full(store, task):
    return store.get_tasks([task["id"]])["items"][0]


def test_creation_queue_or_inbox_and_descriptive_origin(context):
    store, project, ws, _ = context
    inbox = store.create_task(project, "Explore", source="user", user_request="Design first")
    assert inbox["queue_workstream_id"] is None and inbox["gate_diagnostics"] == ["inbox"]
    task = store.create_task(project, "Build", workstream_id=ws)
    assert task["revision"] == 1 and task["queue_workstream_id"] == ws
    assert task["gate_diagnostics"] == [] and store.get_next_action(ws)["task"]["id"] == task["id"]
    assert full(store, inbox)["user_request"] == "Design first"
    for value in (inbox, task, full(store, task), store.list_tasks(project)["items"][0]):
        assert (
            not {
                "accepted",
                "accepted_spec_revision",
                "acceptance_basis",
                "acceptance_note",
                "approval_decision",
            }
            & value.keys()
        )
    assert not hasattr(store, "accept_task") and not hasattr(store, "withdraw_acceptance")
    with pytest.raises(TypeError):
        store.create_task(project, "Obsolete", approval={"basis": "specific", "note": "old"})


def test_move_unqueue_noops_revision_and_history(context):
    store, project, ws, other = context
    task = store.create_task(project, "Move", workstream_id=ws)
    before = full(store, task)
    noop = store.queue_task(task["id"], ws, 1)
    assert not noop["changed"] and noop["revision"] == 1
    assert full(store, task)["updated_at"] == before["updated_at"]
    moved = store.queue_task(task["id"], other, 1)
    assert moved["revision"] == 2 and moved["queue_workstream_id"] == other
    assert len(moved["workstream_revisions"]) == 2
    assert store.list_tasks(project, ws)["items"] == []
    assert [t["id"] for t in store.list_tasks(project, other)["items"]] == [task["id"]]
    with pytest.raises(TaskError, match="revision_conflict"):
        store.unqueue_task(task["id"], 1)
    inbox = store.unqueue_task(task["id"], 2)
    assert inbox["revision"] == 3 and inbox["spec_revision"] == 1
    assert inbox["gate_diagnostics"] == ["inbox"] and not store.get_next_action(other)["task"]
    assert not store.unqueue_task(task["id"], 3)["changed"]
    with sqlite3.connect(store.path) as db:
        events = db.execute(
            "SELECT action,before_json,after_json FROM events WHERE task_id=? AND outcome='ok' "
            "AND action IN ('task.queued','task.unqueued')",
            (task["id"],),
        ).fetchall()
    assert [e[0] for e in events] == [
        "task.queued",
        "task.queued",
        "task.unqueued",
        "task.unqueued",
    ]
    assert json.loads(events[1][1])["queue_workstream_id"] == ws
    assert json.loads(events[1][2])["queue_workstream_id"] == other


@pytest.mark.parametrize("field", ["title", "body", "acceptance_criteria", "summary"])
@pytest.mark.parametrize("queued", [False, True])
def test_edits_retain_placement_and_execution_gates(context, field, queued):
    store, project, ws, _ = context
    task = store.create_task(project, "Scope", workstream_id=ws if queued else None)
    before = full(store, task)
    changed = store.update_task(
        task["id"], 1, {field: "Amended"}, specification_etag=before["specification_etag"]
    )
    assert changed["queue_workstream_id"] == (ws if queued else None)
    assert changed["gate_diagnostics"] == ([] if queued else ["inbox"])
    assert bool(store.get_next_action(ws)["task"]) == queued
    assert changed["spec_revision"] == (1 if field == "summary" else 2)


def test_queue_preserves_unresolved_and_prerequisite_gates(context):
    store, project, ws, _ = context
    task = store.create_task(project, "Design")
    blocker = store.create_task(project, "Blocker")
    task = store.add_unresolved(task["id"], 1, "Decide scope")
    task = store.add_prerequisite(task["id"], task["revision"], blocker["id"])
    task = store.queue_task(task["id"], ws, task["revision"])
    assert task["gate_diagnostics"] == ["unresolved_items", "prerequisites"]
    assert store.get_next_action(ws)["task"] is None
    assert "pending_acceptance" not in json.dumps(store.workstream_status(ws))
    assert "pending_acceptance" not in json.dumps(store.get_next_action(ws))


def test_two_concurrent_moves_have_one_winner(context):
    store, project, ws, other = context
    task = store.create_task(project, "Race")
    barrier = Barrier(2)

    def move(target):
        instance = Store(store.path)
        barrier.wait(timeout=5)
        try:
            return instance.queue_task(task["id"], target, 1)
        except TaskError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(move, [ws, other]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum("revision_conflict" in r for r in results if isinstance(r, str)) == 1
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM queue_members WHERE task_id=?", (task["id"],)
            ).fetchone()[0]
            == 1
        )


def test_queue_cross_project_group_and_sql_invariants(context, tmp_path):
    store, project, ws, other = context
    task = store.create_task(project, "Task", workstream_id=ws)
    remote = store.init_project(str(tmp_path / "remote"), branch="main", confirmed=True)[
        "workstream"
    ]["id"]
    group = store.create_group(ws, "Context")
    with pytest.raises(TaskError, match="unknown_workstream"):
        store.queue_task(task["id"], remote, 1)
    with pytest.raises(TaskError, match="group_not_executable"):
        store.queue_task(group["id"], ws, group["revision"])
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO queue_members VALUES (?,?)", (task["id"], other))
        with pytest.raises(sqlite3.IntegrityError, match="invalid_queue_member"):
            db.execute(
                "UPDATE queue_members SET workstream_id=? WHERE task_id=?", (remote, task["id"])
            )


def test_group_snapshot_membership_bulk_and_decomposition(context):
    store, project, ws, other = context
    group = store.create_group(ws, "Context")
    member = store.create_task(
        project, "Member", group_id=group["id"], group_expected_revision=group["revision"]
    )
    assert member["queue_workstream_id"] is None
    rev = store.workstream_status(ws)["workstream"]["revision"]
    placed = store.set_scope(ws, rev, f"{ws} +{group['id']}")
    assert placed["revision"] == rev + 1
    assert full(store, member)["queue_workstream_id"] == ws
    member = full(store, member)
    moved = store.queue_task(member["id"], other, member["revision"])
    group = full(store, group)
    late = store.create_task(
        project, "Later", group_id=group["id"], group_expected_revision=group["revision"]
    )
    assert late["queue_workstream_id"] is None
    rev = store.workstream_status(ws)["workstream"]["revision"]
    assert not store.set_scope(ws, rev, ws)["changed"]
    assert (
        full(store, moved)["queue_workstream_id"] == other
        and full(store, late)["queue_workstream_id"] is None
    )
    store.set_scope(ws, rev, f"{ws} +{group['id']}")
    assert full(store, moved)["queue_workstream_id"] == ws
    assert full(store, late)["queue_workstream_id"] == ws
    parent = store.create_task(project, "Split", workstream_id=other)
    children = store.decompose_task(parent["id"], 1, [{"title": "A"}, {"title": "B"}])
    assert children["queue_workstream_id"] is None
    assert all(
        t["queue_workstream_id"] == other for t in store.get_tasks(children["members"])["items"]
    )


@pytest.mark.parametrize("mutation", ["queue", "unqueue", "bulk"])
def test_completed_placement_is_immutable(context, mutation):
    store, project, ws, other = context
    task = store.create_task(project, "Delivered", workstream_id=ws)
    proof = store.record_result(
        task["id"],
        ws,
        1,
        "worker",
        "Done",
        "Proof",
        [{"kind": "artifact", "reference": "tests/test_queue.py"}],
        "Checked",
        task["specification_etag"],
    )
    store.record_review(proof["id"], 1, "reviewer", "pass", "Checked")
    done = store.signoff_task(task["id"], 2, "approve", "Actual verdict", proof["id"], 2)
    before = full(store, task)
    with pytest.raises(TaskError, match="completed_task_immutable"):
        if mutation == "queue":
            store.queue_task(task["id"], other, done["revision"])
        elif mutation == "unqueue":
            store.unqueue_task(task["id"], done["revision"])
        else:
            store.set_scope(ws, store.workstream_status(ws)["workstream"]["revision"], "none")
    assert full(store, task) == before


def test_factual_proof_preserves_inbox_and_branch_provenance(context):
    store, project, ws, other = context
    task = store.create_task(project, "Recovered")
    proof = store.record_result(
        task["id"],
        other,
        1,
        "worker",
        "Recovered",
        "Actual artifact",
        [{"kind": "artifact", "reference": "tests/test_queue.py"}],
        "Checked",
        task["specification_etag"],
    )
    assert proof["queue_workstream_id"] is None
    reviewed = store.record_review(proof["id"], 1, "reviewer", "pass", "Actual review")
    task = store.queue_task(task["id"], ws, proof["task_revision"])
    assert store.get_next_action(ws)["action"] == "implement"
    assert reviewed["workstream_id"] == other
    assert full(store, task)["attempts"][0]["workstream_id"] == other
    # Actual human signoff requires current reviewed proof, even for an inbox task.
    task = store.unqueue_task(task["id"], task["revision"])
    done = store.signoff_task(
        task["id"], task["revision"], "approve", "Actual verdict", proof["id"], 2
    )
    assert done["status"] == "done" and done["queue_workstream_id"] is None
