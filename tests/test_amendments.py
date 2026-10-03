"""Whole-field concurrency and current-spec proof survive queue-preserving edits."""

import sqlite3

import pytest

from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    setup = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    return store, setup["project"]["id"], setup["workstream"]["id"]


def full(store, task):
    return store.get_tasks([task["id"]])["items"][0]


@pytest.mark.parametrize("field", ["body", "acceptance_criteria"])
def test_replacement_requires_complete_current_etag_and_atomic_rollback(context, field):
    store, project, ws = context
    task = store.create_task(
        project, "Original", body="Body", acceptance_criteria="Criteria", workstream_id=ws
    )
    before = full(store, task)
    for token in (None, "wrong", "sha256:old"):
        with pytest.raises(TaskError, match="specification_read_required"):
            store.update_task(task["id"], 1, {field: "Replace"}, specification_etag=token)
        assert full(store, task) == before
    changed = store.update_task(task["id"], 1, {field: "Replace"}, before["specification_etag"])
    assert changed["revision"] == changed["spec_revision"] == 2
    assert changed["queue_workstream_id"] == ws
    assert changed["specification_etag"] != before["specification_etag"]
    with pytest.raises(TaskError, match="revision_conflict"):
        store.update_task(task["id"], 1, {field: "Stale"}, before["specification_etag"])


def test_noop_token_and_summary_completed_correction(context):
    store, project, ws = context
    task = store.create_task(project, "Original", workstream_id=ws)
    noop = store.update_task(task["id"], 1, {"body": ""}, task["specification_etag"])
    assert not noop["changed"] and noop["revision"] == 1
    assert noop["specification_etag"] == task["specification_etag"]
    proof = store.record_result(
        task["id"],
        ws,
        1,
        "worker",
        "Delivered",
        "Proof",
        [{"kind": "artifact", "reference": "tests/test_amendments.py"}],
        "Checked",
        task["specification_etag"],
    )
    store.record_review(proof["id"], 1, "reviewer", "pass", "Checked")
    done = store.signoff_task(task["id"], 2, "approve", "User verdict", proof["id"], 2)
    summary = store.update_task(task["id"], done["revision"], {"summary": "Corrected intent"})
    assert summary["spec_revision"] == 1 and summary["summary_changed"]
    assert not summary["spec_changed"] and summary["queue_workstream_id"] == ws
    with pytest.raises(TaskError, match="completed_task_immutable"):
        store.update_task(task["id"], summary["revision"], {"title": "New requirement"})


def test_real_spec_edit_keeps_queue_and_supersedes_review(context):
    store, project, ws = context
    task = store.create_task(project, "Original", workstream_id=ws)
    proof = store.record_result(
        task["id"],
        ws,
        1,
        "worker",
        "Delivered",
        "Proof",
        [{"kind": "artifact", "reference": "tests/test_amendments.py"}],
        "Checked",
        task["specification_etag"],
    )
    store.record_review(proof["id"], 1, "reviewer", "pass", "Checked")
    before = full(store, task)
    changed = store.update_task(
        task["id"], 2, {"body": "Changed requirements"}, before["specification_etag"]
    )
    after = full(store, task)
    assert after["attempts"] == before["attempts"] and changed["queue_workstream_id"] == ws
    assert store.get_next_action(ws)["action"] == "implement"
    with pytest.raises(TaskError, match="review_required"):
        store.signoff_task(
            task["id"], changed["revision"], "approve", "User verdict", proof["id"], 2
        )
    with sqlite3.connect(store.path) as db:
        event = db.execute(
            "SELECT before_json,after_json FROM events WHERE action='task.updated'"
        ).fetchone()
    assert '"queue_workstream_id":' in event[0] and '"queue_workstream_id":' in event[1]
