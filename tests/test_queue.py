"""Nonexclusive effective membership, dynamic scopes and branch-local attempts."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="membership-coordinator")
    setup = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    project, ws = setup["project"]["id"], setup["workstream"]["id"]
    other = store.init_workstream(
        project, str(tmp_path / "repo"), branch="feature", confirmed=True
    )["workstream"]["id"]
    return store, project, ws, other


def full(store, task, **kwargs):
    return (store.read_tasks([task["id"]], **kwargs) if kwargs else store.get_tasks([task["id"]]))[
        "items"
    ][0]


def result(store, task, ws):
    task = full(store, task)
    return store.record_result(
        task["id"],
        ws,
        task["revision"],
        "worker",
        "Done",
        "Proof",
        [{"kind": "artifact", "reference": "tests/test_queue.py"}],
        "Checked",
        task["specification_etag"],
    )


def test_creation_and_no_separate_acceptance(context):
    store, project, ws, _ = context
    inbox = store.create_task(project, "Explore", source="user", user_request="Design first")
    assert inbox["workstream_ids"] == [] and not inbox["adopted"]
    assert inbox["gate_diagnostics"] == ["inbox"]
    task = store.create_task(project, "Build", workstream_id=ws)
    assert task["revision"] == 1 and task["workstream_ids"] == [ws] and task["adopted"]
    assert store.get_next_action(ws)["task"]["id"] == task["id"]
    assert full(store, inbox)["user_request"] == "Design first"
    for value in (inbox, task, full(store, task), store.list_tasks(project)["items"][0]):
        assert (
            not {
                "accepted",
                "accepted_spec_revision",
                "acceptance_basis",
                "acceptance_note",
                "approval_decision",
                "queue_workstream_id",
            }
            & value.keys()
        )
    for obsolete in ("accept_task", "withdraw_acceptance", "queue_task", "unqueue_task"):
        assert not hasattr(store, obsolete)
    with pytest.raises(TypeError):
        store.create_task(project, "Obsolete", approval={"basis": "specific", "note": "old"})


def test_add_b_keeps_a_remove_names_one_and_inbox_means_zero(context):
    store, project, ws, other = context
    task = store.create_task(project, "Shared", workstream_id=ws)
    noop = store.add_to_workstream(task["id"], ws, 1)
    assert not noop["changed"] and noop["revision"] == 1
    added = store.add_to_workstream(task["id"], other, 1)
    assert added["revision"] == 2 and set(added["workstream_ids"]) == {ws, other}
    for stream in (ws, other):
        assert store.list_tasks(project, stream)["items"][0]["id"] == task["id"]
    with pytest.raises(TaskError, match="revision_conflict"):
        store.remove_from_workstream(task["id"], ws, 1)
    removed = store.remove_from_workstream(task["id"], ws, 2)
    assert removed["revision"] == 3 and removed["workstream_ids"] == [other]
    assert removed["adopted"] and removed["gate_diagnostics"] == []
    assert store.list_tasks(project, ws)["items"] == []
    assert not store.remove_from_workstream(task["id"], ws, 3)["changed"]
    inbox = store.remove_from_workstream(task["id"], other, 3)
    assert inbox["revision"] == 4 and inbox["spec_revision"] == 1
    assert not inbox["adopted"] and inbox["gate_diagnostics"] == ["inbox"]
    with sqlite3.connect(store.path) as db:
        event = db.execute(
            "SELECT before_json,after_json FROM events WHERE action='task.workstream_added' "
            "AND task_id=? ORDER BY sequence DESC LIMIT 1",
            (task["id"],),
        ).fetchone()
    assert json.loads(event[0])["workstream_ids"] == [ws]
    assert set(json.loads(event[1])["workstream_ids"]) == {ws, other}


@pytest.mark.parametrize("field", ["title", "body", "acceptance_criteria", "summary"])
def test_edits_keep_both_memberships_and_old_proof(context, field):
    store, project, ws, other = context
    task = store.create_task(project, "Shared", workstream_id=ws)
    task = store.add_to_workstream(task["id"], other, 1)
    proof = result(store, task, ws)
    before = full(store, task)
    changed = store.update_task(
        task["id"],
        before["revision"],
        {field: "Amended"},
        specification_etag=before["specification_etag"],
    )
    assert set(changed["workstream_ids"]) == {ws, other} and changed["adopted"]
    assert changed["spec_revision"] == (1 if field == "summary" else 2)
    assert full(store, task)["attempts"][0]["id"] == proof["id"]
    assert store.get_next_action(other)["action"] == "implement"


def test_parallel_attempt_selection_review_and_removal_stay_local(context):
    store, project, ws, other = context
    task = store.create_task(project, "Parallel", workstream_id=ws)
    task = store.add_to_workstream(task["id"], other, 1)
    a = result(store, task, ws)
    assert store.get_next_action(ws)["action"] == "review"
    assert store.get_next_action(other)["action"] == "implement"
    b = result(store, task, other)
    store.record_review(a["id"], 1, "reviewer", "pass", "A checked")
    assert full(store, task, workstream_id=ws)["state"] == "signoff"
    assert full(store, task, workstream_id=other)["state"] == "review"
    assert store.get_next_action(other)["attempt"]["id"] == b["id"]
    before = full(store, task)
    removed = store.remove_from_workstream(task["id"], ws, before["revision"])
    assert removed["workstream_ids"] == [other]
    assert store.get_attempt(a["id"])["state"] == "passed"
    assert store.get_attempt(b["id"])["state"] == "review"
    assert store.get_next_action(ws)["task"] is None
    assert store.get_next_action(other)["attempt"]["id"] == b["id"]


def test_dynamic_group_inclusion_exclusions_base_and_decomposition(context):
    store, project, ws, other = context
    group = store.create_group(ws, "Context")
    member = store.create_task(
        project, "Member", group_id=group["id"], group_expected_revision=group["revision"]
    )
    assert member["workstream_ids"] == [ws]
    rev = store.workstream_status(other)["workstream"]["revision"]
    scoped = store.set_scope(other, rev, f"{ws} -{member['id']}")
    assert full(store, member)["workstream_ids"] == [ws]
    assert scoped["exclusions"] == [member["id"]]
    group = full(store, group)
    late = store.create_task(
        project, "Later", group_id=group["id"], group_expected_revision=group["revision"]
    )
    assert set(late["workstream_ids"]) == {ws, other}
    removed = store.remove_from_workstream(late["id"], ws, 1)
    assert removed["workstream_ids"] == [other]
    added = store.add_to_workstream(late["id"], ws, removed["revision"])
    assert set(added["workstream_ids"]) == {ws, other}
    assert not store.set_scope(other, scoped["revision"], other)["changed"]
    parent = store.create_task(project, "Split", workstream_id=ws)
    parent = store.add_to_workstream(parent["id"], other, 1)
    children = store.decompose_task(
        parent["id"], parent["revision"], [{"title": "A"}, {"title": "B"}]
    )
    assert children["workstream_ids"] == []
    assert all(
        set(t["workstream_ids"]) == {ws, other}
        for t in store.get_tasks(children["members"])["items"]
    )
    # Whole-group exclusion removes inherited scope, but independent direct scope survives.
    rev = store.workstream_status(ws)["workstream"]["revision"]
    store.set_scope(ws, rev, f"{ws} -{group['id']}")
    assert full(store, member)["workstream_ids"] == []
    assert set(full(store, late)["workstream_ids"]) == {ws, other}


def test_membership_keeps_questions_prerequisites_and_local_scope_gate(context):
    store, project, ws, other = context
    task = store.create_task(project, "Design")
    blocker = store.create_task(project, "Blocker")
    task = store.add_unresolved(task["id"], 1, "Decide")
    task = store.add_prerequisite(task["id"], task["revision"], blocker["id"])
    task = store.add_to_workstream(task["id"], ws, task["revision"])
    assert task["gate_diagnostics"] == ["unresolved_items", "prerequisites"]
    scoped = full(store, task, workstream_id=other, include=["blockers"])
    assert "task_out_of_scope" in scoped["gate_diagnostics"]
    assert scoped["state"] == "out_of_scope" and scoped["question_count"] == 1
    assert scoped["blockers"] == [blocker["id"]]
    assert store.get_next_action(ws)["task"] is None


@pytest.mark.parametrize("excluded_kind", ["task", "group"])
def test_workstream_status_pages_real_task_and_group_exclusions(context, excluded_kind):
    store, project, ws, other = context
    excluded_ids = []
    for index in range(3):
        group = store.create_group(ws, f"Group {index}")
        member = store.create_task(
            project,
            f"Inherited member {index}",
            group_id=group["id"],
            group_expected_revision=group["revision"],
        )
        assert member["workstream_ids"] == [ws]
        if excluded_kind == "task":
            store.remove_from_workstream(member["id"], ws, member["revision"])
            excluded_ids.append(member["id"])
        else:
            revision = store.workstream_status(ws)["workstream"]["revision"]
            store.set_scope(ws, revision, f"{ws} -{group['id']}")
            excluded_ids.append(group["id"])
        assert full(store, member)["workstream_ids"] == []

    excluded_ids.sort()
    assert "scope" not in store.workstream_status(ws)
    first = store.workstream_status(ws, limit=2, include_scope=True)
    assert first["items"] == [] and first["status"]["scoped_count"] == 0
    assert first["scope"]["exclusions"] == {
        "ids": excluded_ids[:2],
        "total": 3,
        "next_offset": 2,
    }
    last = store.workstream_status(ws, limit=2, offset=2, include_scope=True)
    assert last["scope"]["exclusions"] == {
        "ids": excluded_ids[2:],
        "total": 3,
        "next_offset": None,
    }
    beyond = store.workstream_status(ws, limit=2, offset=3, include_scope=True)
    assert beyond["scope"]["exclusions"] == {"ids": [], "total": 3, "next_offset": None}
    assert store.workstream_status(other, include_scope=True)["scope"]["exclusions"] == {
        "ids": [],
        "total": 0,
        "next_offset": None,
    }


def test_concurrent_adds_require_retry_but_can_include_both(context):
    store, project, ws, other = context
    task = store.create_task(project, "Race")
    barrier = Barrier(2)

    def add(target):
        instance = Store(store.path)
        barrier.wait(timeout=5)
        try:
            return instance.add_to_workstream(task["id"], target, 1)
        except TaskError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(add, [ws, other]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum("revision_conflict" in r for r in results if isinstance(r, str)) == 1
    winner = next(r for r in results if isinstance(r, dict))
    missing = next(w for w in (ws, other) if w not in winner["workstream_ids"])
    retried = store.add_to_workstream(task["id"], missing, winner["revision"])
    assert set(retried["workstream_ids"]) == {ws, other}


def test_cross_project_and_group_membership_fail(context, tmp_path):
    store, project, ws, _ = context
    task = store.create_task(project, "Task", workstream_id=ws)
    remote = store.init_project(str(tmp_path / "remote"), branch="main", confirmed=True)[
        "workstream"
    ]["id"]
    group = store.create_group(ws, "Context")
    with pytest.raises(TaskError, match="unknown_workstream"):
        store.add_to_workstream(task["id"], remote, 1)
    # A group ID is scope inclusion, never execution; remote workstreams may include it.
    included = store.add_to_workstream(group["id"], remote, group["revision"])
    assert included["changed"] and included["included"]
    with sqlite3.connect(store.path) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='queue_members'").fetchone()


def test_completed_spec_and_proof_immutable_scope_remains_independent(context):
    store, project, ws, other = context
    task = store.create_task(project, "Done", workstream_id=ws)
    proof = result(store, task, ws)
    store.record_review(proof["id"], 1, "reviewer", "pass", "Checked")
    done = store.signoff_task(task["id"], 2, "approve", "Actual verdict", proof["id"], 2)
    before = full(store, task)
    with pytest.raises(TaskError, match="completed_task_immutable"):
        store.add_to_workstream(task["id"], other, done["revision"])
    rev = store.workstream_status(other)["workstream"]["revision"]
    store.set_scope(other, rev, f"none +{task['id']}")
    after = full(store, task)
    assert set(after["workstream_ids"]) == {ws, other}
    for key in ("status", "spec_revision", "revision", "selected_attempt_id", "body", "attempts"):
        assert after[key] == before[key]
