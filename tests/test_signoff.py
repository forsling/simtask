"""An actual user decision judges purpose separately from delivered technical quality."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "signoff.sqlite3")
    setup = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    return store, setup["project"]["id"], setup["workstream"]["id"]


def full(store, task):
    return store.get_tasks([task["id"]])["items"][0]


def delivered(context, basis="specific", human=False, grouped=False):
    store, project, ws = context
    group = store.create_group(ws, "Delivery group") if grouped else None
    task = store.create_task(
        project,
        "Scope",
        body="Exact accepted requirements",
        acceptance_criteria="Actual proof",
        approval={"basis": basis, "note": "Synthetic actual supporting authority"},
        scope="workstream",
        workstream_id=ws,
        group_id=group["id"] if group else None,
        group_expected_revision=group["revision"] if group else None,
    )
    attempt = store.record_result(
        task["id"],
        ws,
        1,
        "worker",
        "Delivered",
        "Actual proof",
        artifacts=[{"kind": "artifact", "reference": "tests/test_signoff.py"}],
        verification="Actual proof",
        specification_etag=store.get_tasks([task["id"]])["items"][0]["specification_etag"],
    )
    if human:
        store.human_review(attempt["id"], 1, "Synthetic actual human review")
    else:
        store.record_review(
            attempt["id"], 1, "independent reviewer", "pass", "Actual independent proof"
        )
    return full(store, task), attempt


def decide(store, task, attempt, decision, **extra):
    return store.signoff_task(
        task["id"],
        task["revision"],
        decision,
        "Synthetic actual user's verdict",
        attempt["id"],
        task["attempts"][0]["revision"],
        **extra,
    )


@pytest.mark.parametrize("basis", ["specific", "delegated", "unknown"])
@pytest.mark.parametrize("human", [False, True])
def test_one_approve_covers_purpose_and_result_with_truthful_approval_reference(
    context, basis, human
):
    store, _, _ = context
    task, attempt = delivered(
        context, "specific" if basis == "unknown" else basis, human, grouped=True
    )
    if basis == "unknown":
        # Migrated classification is unknown. Neither supporting prose nor old
        # specific audit text may be interpreted as current specific authority.
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE tasks SET acceptance_basis='unknown' WHERE id=?", (task["id"],))
        task = full(store, task)
    approval = task["approval_decision"]
    if basis != "unknown":
        assert approval["action"] == "task.created" and approval["active"]
        assert approval["note"] == task["acceptance_note"]
    else:
        assert approval is None
    ack = decide(store, task, attempt, "approve")
    assert ack["status"] == "done" and ack["selected_attempt_id"] == attempt["id"]
    assert ack["attempt_revision"] == 2 and ack["spec_revision"] == 1
    assert not {"body", "attempts", "signoff_decisions", "user_note"} & ack.keys()
    saved = full(Store(store.path), task)
    judgment = saved["signoff_decisions"][-1]
    assert judgment["decision_ref"] == ack["decision_ref"]
    assert judgment["task_revision"] == task["revision"]
    assert judgment["spec_revision"] == attempt["spec_revision"] == 1
    assert judgment["attempt_id"] == attempt["id"] and judgment["attempt_revision"] == 2
    assert judgment["purpose_judgment"] == "approved" and judgment["result_judgment"] == "accepted"
    assert judgment["purpose_source"] == (
        "reused_specific_approval" if basis == "specific" else "user_verdict"
    )
    assert judgment["reused_approval_decision_ref"] == (
        approval["decision_ref"] if basis == "specific" else None
    )
    assert judgment["user_note"] == "Synthetic actual user's verdict"
    assert saved["attempts"] == task["attempts"]
    with pytest.raises(TaskError, match="completed_task_immutable"):
        decide(store, saved, attempt, "defer")


@pytest.mark.parametrize(
    "decision,status,accepted,purpose",
    [
        ("revise", "open", False, "revise"),
        ("drop", "dropped", False, "dropped"),
        ("defer", "deferred", True, "deferred"),
    ],
)
def test_direction_decision_preserves_sound_result_without_inventing_quality(
    context, decision, status, accepted, purpose
):
    store, project, ws = context
    task, attempt = delivered(context)
    extra = (
        {"specification_question": "Should this accept both input formats?"}
        if decision == "revise"
        else {}
    )
    dependent = store.create_task(
        project,
        "Dependent",
        approval={"basis": "specific", "note": "Yes"},
        scope="workstream",
        workstream_id=ws,
    )
    dependent = store.add_prerequisite(dependent["id"], 1, task["id"])
    ack = decide(store, task, attempt, decision, **extra)
    saved = full(Store(store.path), task)
    assert ack["status"] == status and ack["accepted"] == accepted
    assert saved["body"] == task["body"] and saved["spec_revision"] == task["spec_revision"]
    assert saved["acceptance_note"] == task["acceptance_note"]
    assert saved["attempts"] == task["attempts"]
    judgment = saved["signoff_decisions"][-1]
    assert judgment["purpose_judgment"] == purpose and judgment["purpose_source"] == "user_verdict"
    assert judgment["result_judgment"] == "not_judged" and judgment["result_note"] is None
    assert judgment["disposition"] == status
    assert judgment["resulting_task_revision"] == ack["revision"]
    assert judgment["resulting_attempt_revision"] == ack["attempt_revision"] == 2
    assert store.get_next_action(ws)["task"] is None
    assert "prerequisites" in store.workstream_status(ws)["items"][-1]["gate_diagnostics"]
    if decision == "revise":
        assert ack["unresolved_id"] == saved["unresolved_items"][-1]["id"]
        assert saved["unresolved_items"][-1]["text"] == extra["specification_question"]
        resolved = store.resolve_unresolved(
            task["id"], ack["revision"], ack["unresolved_id"], "Use both"
        )
        edited = store.update_task(
            task["id"],
            resolved["revision"],
            {"body": "Now accept both"},
            specification_etag=full(store, task)["specification_etag"],
        )
        assert edited["spec_revision"] == 2 and not edited["accepted"]
        assert full(store, task)["attempts"] == task["attempts"]
    elif decision == "defer":
        resumed = store.set_disposition(task["id"], ack["revision"], "open", "Resume")
        assert resumed["accepted"] and "signoff" in resumed["gate_diagnostics"]


@pytest.mark.parametrize("decision", ["revise", "drop", "defer"])
@pytest.mark.parametrize("quality", ["accepted", "rework"])
def test_separately_supplied_actual_quality_is_recorded_without_replacing_independent_review(
    context, decision, quality
):
    store, _, _ = context
    task, attempt = delivered(context)
    extra = (
        {"specification_question": "Which behavior should replace this?"}
        if decision == "revise"
        else {}
    )
    with pytest.raises(TaskError, match="result_note_required"):
        decide(store, task, attempt, decision, result_judgment=quality, **extra)
    ack = decide(
        store,
        task,
        attempt,
        decision,
        result_judgment=quality,
        result_note="Synthetic actual quality judgment separately supplied by user",
        **extra,
    )
    saved = full(store, task)
    assert saved["signoff_decisions"][-1]["result_judgment"] == quality
    result = saved["attempts"][0]
    assert result["state"] == ("rework" if quality == "rework" else "passed")
    assert (
        result["reviewer"] == "independent reviewer"
        and result["review_note"] == "Actual independent proof"
    )
    assert result["revision"] == ack["attempt_revision"] == (3 if quality == "rework" else 2)
    with sqlite3.connect(store.path) as db:
        review = db.execute(
            "SELECT after_json FROM events WHERE action='attempt.reviewed'"
        ).fetchone()
    assert json.loads(review[0])["state"] == "passed"


def test_rework_retains_approved_spec_requires_new_result_and_fresh_review(context):
    store, _, ws = context
    task, attempt = delivered(context, "delegated")
    ack = decide(store, task, attempt, "rework")
    saved = full(store, task)
    assert ack["accepted"] and ack["spec_revision"] == 1 and ack["attempt_revision"] == 3
    judgment = saved["signoff_decisions"][-1]
    assert (
        judgment["purpose_judgment"] == "approved" and judgment["purpose_source"] == "user_verdict"
    )
    assert judgment["result_judgment"] == "rework"
    assert store.get_next_action(ws)["task"]["id"] == task["id"]
    with pytest.raises(TaskError, match="review_required"):
        decide(store, saved, attempt, "approve")
    retry = store.record_result(
        task["id"],
        ws,
        ack["revision"],
        "worker",
        "Fixed",
        "Fresh proof",
        artifacts=[{"kind": "artifact", "reference": "tests/test_signoff.py"}],
        verification="Fresh proof",
        specification_etag=store.get_tasks([task["id"]])["items"][0]["specification_etag"],
    )
    with pytest.raises(TaskError, match="review_required"):
        store.signoff_task(task["id"], ack["revision"] + 1, "approve", "Approve", retry["id"], 1)
    reviewed = store.record_review(retry["id"], 1, "reviewer", "pass", "Fresh independent proof")
    complete = store.signoff_task(
        task["id"],
        ack["revision"] + 1,
        "approve",
        "Approve both",
        retry["id"],
        reviewed["revision"],
    )
    assert complete["status"] == "done"
    assert len(full(store, task)["signoff_decisions"]) == 2


def test_ordinary_drop_defer_and_authorized_revival_need_no_review(context):
    store, project, ws = context
    task = store.create_task(
        project,
        "Unbuilt",
        body="Scope",
        approval={"basis": "specific", "note": "Initial approval"},
        scope="workstream",
        workstream_id=ws,
    )
    deferred = store.set_disposition(task["id"], 1, "deferred", "Later")
    assert deferred["accepted"]
    dropped = store.set_disposition(task["id"], 2, "dropped", "No longer wanted")
    assert not dropped["accepted"] and dropped["spec_revision"] == 1
    for status in ("open", "deferred"):
        with pytest.raises(TaskError, match="revival_authorization_required"):
            store.set_disposition(task["id"], 3, status, "Agent decided to restore")
    revived = store.set_disposition(
        task["id"], 3, "open", "Restore", authorization="Synthetic actual user requested revival"
    )
    assert not revived["accepted"] and store.get_next_action(ws)["task"] is None
    full_task = full(store, task)
    assert full_task["body"] == "Scope" and full_task["acceptance_note"] == "Initial approval"
    assert not full_task["approval_decision"]["active"] and full_task["signoff_decisions"] == []
    approved = store.accept_task(
        task["id"], revived["revision"], {"basis": "specific", "note": "Actual reapproval"}
    )
    assert approved["accepted"] and store.get_next_action(ws)["task"]["id"] == task["id"]


def test_signoff_checks_exact_attempt_concurrency_and_both_completion_gates(context):
    store, project, _ = context
    task, attempt = delivered(context)
    with pytest.raises(TaskError, match="revision_conflict"):
        store.signoff_task(task["id"], 2, "approve", "Actual verdict", attempt["id"], 1)
    with pytest.raises(TaskError, match="specification_question_required"):
        decide(store, task, attempt, "revise")
    with pytest.raises(TaskError, match="user_verdict_required"):
        store.signoff_task(task["id"], 2, "approve", "  ", attempt["id"], 2)
    with pytest.raises(TaskError, match="conflicting_result_judgment"):
        decide(store, task, attempt, "approve", result_judgment="not_judged")
    blocker = store.create_task(project, "Blocker")
    gated = store.add_unresolved(task["id"], 2, "Unanswered")
    gated = store.add_prerequisite(task["id"], gated["revision"], blocker["id"])
    for clear_question in (False, True):
        if clear_question:
            gated = store.resolve_unresolved(
                task["id"], gated["revision"], gated["unresolved_items"][0]["id"], "Settled"
            )
        with pytest.raises(TaskError, match="task_not_ready_for_signoff"):
            decide(store, full(store, task), attempt, "approve")
    assert full(store, task)["signoff_decisions"] == []


def test_concurrent_actual_decisions_have_one_winner_and_no_partial_judgment(context):
    store, _, _ = context
    task, attempt = delivered(context)
    barrier = Barrier(2)

    def race(decision):
        barrier.wait()
        try:
            return decide(Store(store.path), task, attempt, decision)
        except TaskError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(race, ["drop", "defer"]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum("revision_conflict" in result for result in results if isinstance(result, str)) == 1
    saved = full(store, task)
    assert len(saved["signoff_decisions"]) == 1
    assert saved["signoff_decisions"][0]["disposition"] == saved["status"]
    assert saved["attempts"] == task["attempts"]
