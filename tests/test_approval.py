"""Classified approval is independent of origin and can be withdrawn without edits."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    initialized = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    return store, initialized["project"]["id"], initialized["workstream"]["id"]


def detail(store, task):
    return store.get_tasks([task["id"]])["items"][0]


def approve(note="Synthetic exact-scope user decision", basis="specific"):
    return {"basis": basis, "note": note}


def test_pending_user_origin_preserves_request_and_compact_continuation(context):
    store, project, ws = context
    request = "Add a design task for filters; keep it pending"
    created = store.create_task(
        project,
        "Filters",
        source="user",
        user_request=request,
        workstream_id=ws,
        scope="workstream",
    )
    assert not created["accepted"] and created["gate_diagnostics"] == ["pending_acceptance"]
    assert created["in_scope"] and created["workstream_revision"] == 2
    assert not {"title", "body", "user_request", "attempts", "acceptance_note"} & created.keys()
    full = detail(store, created)
    assert full["source"] == "user" and full["user_request"] == request
    assert store.get_next_task(ws)["task"] is None
    gated = store.add_unresolved(created["id"], created["revision"], "Feature design required")
    accepted = store.accept_task(gated["id"], gated["revision"], approve())
    assert accepted["accepted"] and accepted["gate_diagnostics"] == ["unresolved_items"]
    assert not {"body", "attempts", "acceptance_note"} & accepted.keys()
    assert store.get_next_task(ws)["task"] is None


@pytest.mark.parametrize("basis", ["specific", "delegated"])
@pytest.mark.parametrize("source", ["user", "agent", "unknown"])
def test_creation_approval_atomically_accepts_exact_scope(context, basis, source):
    store, project, ws = context
    payload = approve("Synthetic authority: select blank-name validation within this goal", basis)
    created = store.create_task(
        project,
        "Validate blank names",
        body="Reject whitespace",
        source=source,
        user_request="Original request",
        approval=payload,
        workstream_id=ws,
        scope="workstream",
    )
    assert (
        created["accepted"] and created["spec_revision"] == created["accepted_spec_revision"] == 1
    )
    assert created["acceptance_basis"] == basis and created["gate_diagnostics"] == []
    assert detail(store, created)["acceptance_note"] == payload["note"]
    selected = store.get_next_task(ws)["task"]
    assert selected["id"] == created["id"] and selected["source"] == source
    with sqlite3.connect(store.path) as db:
        event = db.execute(
            "SELECT request_json,after_json FROM events WHERE action='task.created'"
        ).fetchone()
    assert json.loads(event[0])["approval"] == payload
    assert json.loads(event[1])["accepted_spec_revision"] == 1
    assert json.loads(event[1])["acceptance_note"] == payload["note"]


@pytest.mark.parametrize(
    "payload",
    [
        "old note",
        {},
        {"basis": "unknown", "note": "yes"},
        {"basis": [], "note": "yes"},
        {"basis": "specific", "note": "  "},
        {"basis": "delegated", "note": None},
        {"basis": "specific", "note": "yes", "extra": True},
    ],
)
def test_invalid_approval_rolls_back_creation_and_cannot_accept(context, payload):
    store, project, ws = context
    before = store.workstream_status(ws)["workstream"]["revision"]
    with pytest.raises(TaskError, match="invalid_approval"):
        store.create_task(
            project, "Invalid", approval=payload, workstream_id=ws, scope="workstream"
        )
    assert store.list_tasks(project)["items"] == []
    assert store.workstream_status(ws)["workstream"]["revision"] == before
    pending = store.create_task(project, "Pending")
    with pytest.raises(TaskError, match="invalid_approval"):
        store.accept_task(pending["id"], pending["revision"], payload)
    assert detail(store, pending)["revision"] == 1


def test_withdrawal_retains_spec_decisions_proof_and_reapproval_reuses_review(context):
    store, project, ws = context
    created = store.create_task(
        project,
        "Deliver",
        body="Exact scope",
        approval=approve(),
        workstream_id=ws,
        scope="workstream",
    )
    attempt = store.record_result(
        created["id"], ws, created["revision"], "worker", "Done", "Tests pass"
    )
    store.record_review(attempt["id"], 1, "independent reviewer", "pass", "Verified")
    before = detail(store, created)
    withdrawn = store.withdraw_acceptance(
        created["id"], before["revision"], "Mistaken scope approval"
    )
    assert not withdrawn["accepted"] and withdrawn["spec_revision"] == before["spec_revision"]
    assert withdrawn["gate_diagnostics"] == ["pending_acceptance", "signoff"]
    after = detail(store, withdrawn)
    for key in (
        "title",
        "body",
        "acceptance_criteria",
        "spec_revision",
        "acceptance_note",
        "acceptance_basis",
        "attempts",
        "source",
        "user_request",
    ):
        assert after[key] == before[key]
    assert store.get_next_task(ws)["task"] is None
    assert store.list_tasks(project, ws)["items"][0]["view"] == "pending_acceptance"
    with pytest.raises(TaskError, match="task_not_eligible"):
        store.record_result(created["id"], ws, after["revision"], "worker", "Another", "Proof")
    for verdict in ("approve", "reject"):
        with pytest.raises(TaskError, match="task_not_ready_for_signoff"):
            store.signoff_task(
                created["id"], after["revision"], verdict, "Synthetic verdict", attempt["id"]
            )
    with sqlite3.connect(store.path) as db:
        event = db.execute(
            "SELECT request_json,before_json,after_json FROM events "
            "WHERE action='task.acceptance_withdrawn'"
        ).fetchone()
    assert json.loads(event[0])["note"] == "Mistaken scope approval"
    assert json.loads(event[1])["accepted_spec_revision"] == 1
    assert json.loads(event[2])["accepted_spec_revision"] is None
    assert json.loads(event[2])["accepted"] is False
    reapproved = store.accept_task(
        created["id"], after["revision"], approve("Synthetic renewed approval", "delegated")
    )
    assert reapproved["gate_diagnostics"] == ["signoff"]
    assert detail(store, created)["attempts"] == before["attempts"]
    done = store.signoff_task(
        created["id"],
        reapproved["revision"],
        "approve",
        "Synthetic informed verdict",
        attempt["id"],
    )
    for operation in (
        lambda: store.withdraw_acceptance(created["id"], done["revision"], "Reopen"),
        lambda: store.accept_task(created["id"], done["revision"], approve()),
    ):
        with pytest.raises(TaskError, match="completed_task_immutable"):
            operation()


def test_withdrawal_reason_revision_and_other_gates_are_preserved(context):
    store, project, ws = context
    dependency = store.create_task(project, "Prerequisite")
    created = store.create_task(
        project, "Gated", approval=approve(), workstream_id=ws, scope="workstream"
    )
    gated = store.add_unresolved(created["id"], created["revision"], "Open question")
    gated = store.add_prerequisite(created["id"], gated["revision"], dependency["id"])
    with pytest.raises(TaskError, match="withdrawal_reason_required"):
        store.withdraw_acceptance(created["id"], gated["revision"], " ")
    with pytest.raises(TaskError, match="revision_conflict"):
        store.withdraw_acceptance(created["id"], 1, "Stale")
    withdrawn = store.withdraw_acceptance(created["id"], gated["revision"], "Correct approval")
    assert withdrawn["gate_diagnostics"] == [
        "pending_acceptance",
        "unresolved_items",
        "prerequisites",
    ]
    deferred = store.set_disposition(created["id"], withdrawn["revision"], "deferred", "Later")
    accepted = store.accept_task(created["id"], deferred["revision"], approve())
    assert accepted["status"] == "deferred" and accepted["gate_diagnostics"] == []
    restored = detail(store, created)
    assert restored["unresolved_items"] == gated["unresolved_items"]
    assert restored["blocked_by"] == [dependency["id"]]
    resumed = store.set_disposition(created["id"], accepted["revision"], "open", "Resume")
    assert store.get_next_task(ws)["task"] is None
    assert resumed["accepted"]


def test_real_spec_changes_keep_prior_review_on_its_original_revision(context):
    store, project, ws = context
    created = store.create_task(
        project, "Deliver", approval=approve(), workstream_id=ws, scope="workstream"
    )
    attempt = store.record_result(created["id"], ws, 1, "worker", "Done", "Proof")
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Checked")
    edited = store.update_task(created["id"], 2, {"body": "Changed requirements"})
    accepted = store.accept_task(created["id"], edited["revision"], approve())
    assert accepted["gate_diagnostics"] == [] and accepted["spec_revision"] == 2
    assert detail(store, created)["attempts"][0]["spec_revision"] == 1
    with pytest.raises(TaskError, match="review_required"):
        store.signoff_task(
            created["id"], accepted["revision"], "approve", "Synthetic verdict", attempt["id"]
        )


def test_acceptance_and_withdrawal_race_has_one_revision_winner(context):
    store, project, _ = context
    created = store.create_task(project, "Race", approval=approve())
    barrier = Barrier(2)

    def run(withdraw):
        barrier.wait()
        try:
            if withdraw:
                return store.withdraw_acceptance(created["id"], 1, "Withdraw")
            return store.accept_task(created["id"], 1, approve())
        except TaskError as exc:
            return str(exc)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, (True, False)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum("revision_conflict" in result for result in results if isinstance(result, str)) == 1
    assert detail(store, created)["revision"] == 2
