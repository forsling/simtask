"""Actual verdicts hand factual rejection reasons to the next scoped agent."""

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


def result(store, task, ws):
    current = full(store, task)
    return store.record_result(
        task["id"],
        ws,
        current["revision"],
        "worker",
        "Delivered",
        "Actual proof",
        artifacts=[{"kind": "artifact", "reference": "tests/test_signoff.py"}],
        verification="Actual proof",
        specification_etag=current["specification_etag"],
    )


def delivered(context, human=False, grouped=False):
    store, project, ws = context
    group = store.create_group(ws, "Delivery group") if grouped else None
    task = store.create_task(
        project,
        "Scope",
        body="Exact requirements",
        acceptance_criteria="Actual proof",
        workstream_id=ws,
        group_id=group["id"] if group else None,
        group_expected_revision=group["revision"] if group else None,
    )
    attempt = result(store, task, ws)
    if human:
        store.human_review(attempt["id"], 1, "Synthetic actual human review")
    else:
        store.record_review(attempt["id"], 1, "reviewer", "pass", "Independent proof")
    return full(store, task), store.get_attempt(attempt["id"])


def decide(store, task, attempt, decision, reasons="Synthetic actual user reasons"):
    return store.signoff_task(
        task["id"],
        task["revision"],
        decision,
        reasons,
        attempt["id"],
        attempt["revision"],
    )


@pytest.mark.parametrize(
    "decision,status",
    [
        ("approve", "done"),
        ("rework", "rework"),
        ("revise", "open"),
        ("drop", "dropped"),
    ],
)
@pytest.mark.parametrize("human", [False, True])
def test_verdict_records_only_actual_decision_and_reasons_with_compact_ack(
    context, decision, status, human
):
    store, _, ws = context
    task, attempt = delivered(context, human=human, grouped=True)
    reasons = "Synthetic user reasons. " * 1000
    ack = store.compact_call(
        "signoff_task", task["id"], task["revision"], decision, reasons, attempt["id"], 2
    )
    saved = full(store, task)
    recorded = saved["signoff_decisions"][-1]
    assert saved["status"] == ack["status"] == status
    assert saved["workstream_ids"] == [ws] and saved["spec_revision"] == 1
    assert recorded["decision"] == decision and recorded["reasons"] == reasons
    assert recorded["decision_ref"] == ack["decision_ref"]
    assert (
        not {"purpose_judgment", "purpose_source", "result_judgment", "result_note", "user_note"}
        & recorded.keys()
    )
    assert not {"reasons", "signoff_decisions", "attempts", "body"} & ack.keys()
    assert reasons not in json.dumps(ack) and len(json.dumps(ack)) < 1600
    saved_attempt = store.get_attempt(attempt["id"])
    assert ack["attempt_revision"] == saved_attempt["revision"] == 2 + (decision == "rework")
    if decision == "rework":
        assert saved_attempt["state"] == "rework"
        assert saved_attempt["review_note"] == attempt["review_note"]
        assert saved_attempt["human_review_note"] == attempt["human_review_note"]
    else:
        assert saved["attempts"] == task["attempts"]
    assert saved["selected_attempt_id"] == (attempt["id"] if decision == "approve" else None)
    if decision == "revise":
        assert saved["unresolved_items"] == [{"id": ack["unresolved_id"], "text": reasons}]
    if decision == "approve":
        with pytest.raises(TaskError, match="completed_task_immutable"):
            decide(store, saved, saved_attempt, "drop")


@pytest.mark.parametrize("decision", ["rework", "revise"])
@pytest.mark.parametrize("reasons", [None, "", " \n "])
def test_rejections_require_reasons_without_partial_changes(context, decision, reasons):
    store, _, _ = context
    task, attempt = delivered(context)
    with pytest.raises(TaskError, match="reasons_required"):
        decide(store, task, attempt, decision, reasons)
    assert full(store, task) == task
    assert store.get_attempt(attempt["id"]) == attempt


@pytest.mark.parametrize("decision", ["approve", "drop"])
def test_approve_and_drop_allow_omitted_reasons(context, decision):
    store, _, _ = context
    task, attempt = delivered(context)
    ack = store.signoff_task(
        task["id"],
        task["revision"],
        decision,
        attempt_id=attempt["id"],
        expected_attempt_revision=2,
    )
    assert ack["status"] == {"approve": "done", "drop": "dropped"}[decision]
    assert full(store, task)["signoff_decisions"][-1]["reasons"] is None


def test_latest_rejection_replaces_previous_without_changing_factual_history(context, tmp_path):
    store, project, ws = context
    task, attempt = delivered(context)
    first = decide(store, task, attempt, "rework", "User: fix lost input")
    selected = store.get_next_action(ws)
    rejection = selected["task"]["latest_rejection"]
    assert selected["action"] == "implement" and selected["attempt"]["id"] == attempt["id"]
    assert rejection["source"] == "signoff" and rejection["reasons"] == "User: fix lost input"
    assert rejection["attempt_id"] == attempt["id"] and rejection["workstream_id"] == ws
    assert rejection["decision_ref"] == first["decision_ref"] and rejection["timestamp"]
    first_history = full(store, task)["signoff_decisions"]
    retry = result(store, task, ws)
    store.record_review(retry["id"], 1, "next reviewer", "rework", "Reviewer: handle empty input")
    assert full(store, task)["signoff_decisions"] == first_history
    latest = store.read_tasks([task["id"]], specification=True, workstream_id=ws)["items"][0][
        "latest_rejection"
    ]
    assert latest["source"] == "review" and latest["verdict"] == "rework"
    assert (
        latest["reasons"] == "Reviewer: handle empty input" and latest["attempt_id"] == retry["id"]
    )
    card = store.list_tasks(project, ws)["items"][0]
    assert card["rejected"] is True and card["state"] == "rework"
    card = store.list_tasks(project, ws, include=["attempt"])["items"][0]
    assert card["latest_rejection"] == {k: v for k, v in latest.items() if k != "reasons"}
    fixed = result(store, task, ws)
    # A newer recorded result answers the rejection; its review is now pending.
    card = store.list_tasks(project, ws)["items"][0]
    assert "rejected" not in card and card["state"] == "review"
    store.record_review(fixed["id"], 1, "fresh reviewer", "pass", "Checked fresh proof")
    decide(
        store,
        full(store, task),
        store.get_attempt(fixed["id"]),
        "revise",
        "Design: accept both input formats",
    )
    saved = full(Store(store.path), task)
    assert saved["latest_rejection"]["verdict"] == "revise"
    assert saved["latest_rejection"]["reasons"] == saved["unresolved_items"][-1]["text"]
    assert saved["signoff_decisions"][0] == first_history[0]
    # Rejection context is canonical; attempt eligibility remains branch-local.
    other = store.init(
        str(tmp_path / "repo"), branch="feature", action="new_workstream", confirmed=True
    )
    other_ws = other["workstream"]["id"]
    store.resolve_unresolved(
        task["id"], saved["revision"], saved["unresolved_items"][-1]["id"], "Settled"
    )
    current = full(store, task)
    store.add_to_workstream(task["id"], other_ws, current["revision"])
    action = store.get_next_action(other_ws)
    assert action["action"] == "implement" and action["attempt"] is None
    assert action["task"]["latest_rejection"]["workstream_id"] == ws
    assert action["task"]["latest_rejection"]["attempt_id"] == fixed["id"]
    assert action["task"]["attempt_total"] == 0
    text = store.export_workstream(other_ws)["content"]
    assert "#### Latest rejection" in text and "Design: accept both input formats" in text
    assert ws in text and "Sign-off history" in text


def test_legacy_decisions_remain_readable_and_unchanged_and_lookup_is_indexed(context):
    store, _, ws = context
    task, attempt = delivered(context)
    legacy = {
        "task_revision": 2,
        "spec_revision": 1,
        "attempt_id": attempt["id"],
        "attempt_revision": 2,
        "decision": "defer",
        "disposition": "deferred",
        "user_note": "Legacy defer note",
        "purpose_judgment": "deferred",
        "purpose_source": "user_verdict",
        "result_judgment": "accepted",
        "result_note": "Old actual judgment",
    }
    with sqlite3.connect(store.path) as db:
        for decision in ("rework", "defer"):
            item = {**legacy, "decision": decision}
            db.execute(
                "INSERT INTO events(timestamp,actor,action,outcome,task_id,"
                "request_json,after_json) "
                "VALUES('legacy-time','test','task.signoff','ok',?,?,?)",
                (
                    task["id"],
                    json.dumps(
                        {
                            "decision": decision,
                            "attempt_id": attempt["id"],
                            "user_note": "Legacy rework reasons",
                        }
                    ),
                    json.dumps({"signoff_decision": item}),
                ),
            )
        before = db.execute("SELECT * FROM events").fetchall()
    assert Store(store.path).migration_backup_path is None
    saved = full(store, task)
    assert {
        k: v
        for k, v in saved["signoff_decisions"][-1].items()
        if k not in {"decision_ref", "timestamp"}
    } == legacy
    assert saved["latest_rejection"]["reasons"] == "Legacy rework reasons"
    assert saved["latest_rejection"]["workstream_id"] == ws
    with store._connect() as db:
        queries = []
        db.set_trace_callback(queries.append)
        Store._latest_rejection(db, task["id"])
        db.set_trace_callback(None)
        plan = db.execute("EXPLAIN QUERY PLAN " + queries[0]).fetchall()
        assert any("SEARCH e USING INDEX rejection_history" in row[3] for row in plan)
        assert [
            tuple(row)
            for row in db.execute("SELECT * FROM events WHERE sequence<=?", (before[-1][0],))
        ] == before
    text = store.export_workstream(ws)["content"]
    assert "Legacy defer note" in text and "Old actual judgment" in text


def test_rework_requires_fresh_result_and_review_and_completion_checks_gates(context):
    store, project, ws = context
    task, attempt = delivered(context)
    ack = decide(store, task, attempt, "rework")
    with pytest.raises(TaskError, match="review_required"):
        decide(store, full(store, task), store.get_attempt(attempt["id"]), "approve")
    retry = result(store, task, ws)
    for decision in ("rework", "revise", "drop"):
        with pytest.raises(TaskError, match="review_required"):
            decide(store, full(store, task), retry, decision)
    store.record_review(retry["id"], 1, "reviewer", "pass", "Fresh independent proof")
    gate = store.add_unresolved(task["id"], ack["revision"] + 1, "Unanswered")
    blocker = store.create_task(project, "Blocker")
    gate = store.add_prerequisite(task["id"], gate["revision"], blocker["id"])
    with pytest.raises(TaskError, match="task_not_ready_for_signoff: answer the open questions"):
        decide(store, full(store, task), store.get_attempt(retry["id"]), "approve")
    gate = store.resolve_unresolved(
        task["id"], gate["revision"], gate["unresolved_items"][0]["id"], "Settled"
    )
    # An open prerequisite is the user's judgment, not a refusal.
    done = decide(store, full(store, task), store.get_attempt(retry["id"]), "approve")
    assert done["status"] == "done" and len(full(store, task)["signoff_decisions"]) == 2
    assert [p["id"] for p in done["open_prerequisites"]] == [blocker["id"]]


def test_explicit_user_approve_accepts_a_result_awaiting_independent_review(context):
    store, project, ws = context
    task = store.create_task(project, "Unreviewed", body="Exact", workstream_id=ws)
    attempt = store.get_attempt(result(store, task, ws)["id"])
    assert attempt["state"] == "review"
    with pytest.raises(TaskError, match="reasons_required"):
        decide(store, full(store, task), attempt, "approve", reasons=None)
    stale = store.update_task(
        task["id"], full(store, task)["revision"], {"title": "Changed requirement"}
    )
    with pytest.raises(TaskError, match="review_required"):
        decide(store, full(store, task), attempt, "approve")
    current = store.get_attempt(result(store, task, ws)["id"])
    reasons = "User explicitly approved this result without independent review"
    done = decide(store, full(store, task), current, "approve", reasons=reasons)
    assert stale["spec_changed"]
    assert done["status"] == "done" and done["selected_attempt_id"] == current["id"]
    assert done["attempt_state"] == "human_review" and done["independent_review"] is False
    proof = store.get_attempt(current["id"])
    assert proof["state"] == "human_review" and proof["human_review_note"] == reasons
    assert proof["revision"] == current["revision"] + 1 == done["attempt_revision"]
    decision = full(store, task)["signoff_decisions"][-1]
    assert decision["reasons"] == reasons and decision["independent_review"] is False
    reviewed_task, reviewed = delivered(context)
    assert decide(store, reviewed_task, reviewed, "approve")["independent_review"] is True


def test_removed_contract_invalid_decisions_and_exact_attempt_concurrency(context):
    store, _, _ = context
    task, attempt = delivered(context)
    with pytest.raises(TaskError, match="revision_conflict"):
        store.signoff_task(task["id"], 2, "approve", "Actual verdict", attempt["id"], 1)
    with pytest.raises(TaskError, match="user_verdict_required"):
        decide(store, task, attempt, "defer")
    for key in ("user_note", "result_judgment", "result_note", "specification_question", "note"):
        with pytest.raises(TypeError):
            store.signoff_task(
                task["id"],
                2,
                "approve",
                attempt_id=attempt["id"],
                expected_attempt_revision=2,
                **{key: "removed"},
            )
    assert full(store, task) == task


def test_ordinary_deferral_and_authorized_revival_are_status_changes(context):
    store, project, ws = context
    task = store.create_task(project, "Unbuilt", body="Scope", workstream_id=ws)
    deferred = store.set_disposition(task["id"], 1, "deferred", "Later")
    assert deferred["workstream_ids"] == [ws]
    dropped = store.set_disposition(task["id"], 2, "dropped", "No longer wanted")
    with pytest.raises(TaskError, match="revival_authorization_required"):
        store.set_disposition(task["id"], 3, "open", "Restore")
    revived = store.set_disposition(
        task["id"],
        dropped["revision"],
        "open",
        "Restore",
        authorization="Actual user requested revival",
    )
    assert revived["workstream_ids"] == [ws]
    assert full(store, task)["signoff_decisions"] == []


def test_concurrent_actual_verdicts_have_one_winner(context):
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
        results = list(pool.map(race, ["drop", "rework"]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum("revision_conflict" in r for r in results if isinstance(r, str)) == 1
    assert len(full(store, task)["signoff_decisions"]) == 1


def blocked_by(store, task, dependency, milestone="review"):
    current = full(store, task)
    store.add_prerequisite(task["id"], current["revision"], dependency["id"], milestone=milestone)
    return full(store, task)


def test_approve_records_open_prerequisites_and_leaves_dependents_to_their_rules(context):
    store, project, ws = context
    task, attempt = delivered(context)
    blocker = store.create_task(project, "Open blocker", workstream_id=ws)
    signed = store.create_task(project, "Awaiting sign-off blocker", workstream_id=ws)
    store.record_review(result(store, signed, ws)["id"], 1, "reviewer", "pass", "Proof")
    blocked_by(store, task, blocker)
    task = blocked_by(store, task, signed, "signoff")
    dependent = blocked_by(store, store.create_task(project, "Dependent", workstream_id=ws), task)
    assert all(p["blocking"] for p in task["prerequisites"])
    cards = {card["id"]: card for card in store.list_tasks(project, ws)["items"]}
    assert cards[task["id"]]["state"] == "signoff"
    ack = decide(store, task, attempt, "approve")
    expected = [
        {"id": blocker["id"], "title": "Open blocker", "milestone": "review", "state": "open"},
        {
            "id": signed["id"],
            "title": "Awaiting sign-off blocker",
            "milestone": "signoff",
            "state": "open",
        },
    ]
    expected.sort(key=lambda p: p["id"])
    saved = full(store, task)
    assert ack["status"] == saved["status"] == "done"
    assert ack["open_prerequisites"] == expected
    assert saved["signoff_decisions"][-1]["open_prerequisites"] == expected
    assert saved["selected_attempt_id"] == attempt["id"]
    # The approval leaves the links themselves and the blockers untouched.
    assert sorted(p["id"] for p in saved["prerequisites"]) == [p["id"] for p in expected]
    assert full(store, blocker)["status"] == "open"
    # Dependents of the approved task follow the ordinary prerequisite rules.
    assert all(p["satisfied"] for p in full(store, dependent)["prerequisites"])
    text = store.export_workstream(ws)["content"]
    assert f"Approved while prerequisite open: Open blocker (`{blocker['id']}`" in text


def test_approve_without_open_prerequisites_records_an_empty_list(context):
    store, project, ws = context
    task, attempt = delivered(context)
    blocker, _ = delivered(context)
    task = blocked_by(store, task, blocker)
    assert full(store, task)["prerequisites"][0]["satisfied"] is True
    ack = decide(store, task, attempt, "approve")
    assert ack["open_prerequisites"] == []
    assert full(store, task)["signoff_decisions"][-1]["open_prerequisites"] == []
    assert "Approved while prerequisite open" not in store.export_workstream(ws)["content"]


@pytest.mark.parametrize("decision", ["rework", "revise", "drop"])
def test_other_verdicts_are_unchanged_by_open_prerequisites(context, decision):
    store, project, ws = context
    task, attempt = delivered(context)
    task = blocked_by(store, task, store.create_task(project, "Open blocker"))
    ack = decide(store, task, attempt, decision)
    assert ack["status"] == {"rework": "rework", "revise": "open", "drop": "dropped"}[decision]
    assert "open_prerequisites" not in ack
    assert "open_prerequisites" not in full(store, task)["signoff_decisions"][-1]


def test_explicit_approve_without_review_records_an_open_group_prerequisite(context):
    store, project, ws = context
    group = store.create_group(ws, "Unfinished group")
    store.create_task(
        project,
        "Unfinished member",
        workstream_id=ws,
        group_id=group["id"],
        group_expected_revision=group["revision"],
    )
    task = store.create_task(project, "Unreviewed", body="Exact", workstream_id=ws)
    task = blocked_by(store, task, group)
    attempt = store.get_attempt(result(store, task, ws)["id"])
    assert attempt["state"] == "review"
    reasons = "User explicitly approved this result without independent review"
    ack = decide(store, full(store, task), attempt, "approve", reasons=reasons)
    assert ack["status"] == "done" and ack["independent_review"] is False
    assert ack["attempt_state"] == "human_review"
    recorded = full(store, task)["signoff_decisions"][-1]
    assert recorded["independent_review"] is False
    assert recorded["open_prerequisites"] == [
        {
            "id": group["id"],
            "title": "Unfinished group",
            "milestone": "review",
            "state": "incomplete",
        }
    ]


def test_open_questions_still_refuse_approve_even_without_blockers(context):
    store, _, _ = context
    task, attempt = delivered(context)
    task = store.add_unresolved(task["id"], task["revision"], "Which format?")
    before = full(store, task)
    with pytest.raises(TaskError, match="task_not_ready_for_signoff"):
        decide(store, before, attempt, "approve")
    assert full(store, task) == before
    assert store.get_attempt(attempt["id"]) == attempt
