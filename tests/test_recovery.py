"""Durable factual proof and one-call autonomous recovery, on disposable databases."""

import json
import sqlite3

import pytest

from task_mcp.store import DATABASE_SCHEMA_REVISION, Store, TaskError


def setup(tmp_path):
    store = Store(tmp_path / "recovery.sqlite3")
    context = store.init(
        str(tmp_path / "checkout"), "main", action="create_project", confirmed=True
    )
    return store, context["project"]["id"], context["workstream"]["id"]


def full(store, task):
    return store.get_tasks([task["id"]])["items"][0]


def task(store, project, ws, title="Recover result", accepted=True):
    ack = store.create_task(
        project,
        title,
        body="The exact full requirements",
        acceptance_criteria="Check durable proof",
        workstream_id=ws if accepted else None,
    )
    return full(store, ack)


def proof(store, work, ws, **overrides):
    fields = dict(
        task_id=work["id"],
        workstream_id=ws,
        expected_revision=work["revision"],
        implementer="fixture implementer",
        summary="Durable implementation",
        evidence="Actual fixture artifact checked against the full requirements",
        artifacts=[{"kind": "artifact", "reference": __file__}],
        verification="pytest fixture inspected this file and current full specification",
        specification_etag=work["specification_etag"],
    )
    return store.record_result(**(fields | overrides))


def business_state(store):
    with sqlite3.connect(store.path) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {
            t: db.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall()
            for t in tables
            if t not in {"events", "sqlite_sequence"}
        }


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("disposition", ["open", "deferred", "dropped"])
def test_factual_result_preserves_approval_disposition_and_every_gate(
    tmp_path, accepted, disposition
):
    store, project, ws = setup(tmp_path)
    blocker = task(store, project, ws, "Prerequisite")
    work = task(store, project, ws, accepted=accepted)
    work = store.add_unresolved(work["id"], work["revision"], "Real unresolved fixture question")
    work = store.add_prerequisite(work["id"], work["revision"], blocker["id"])
    if disposition != "open":
        store.set_disposition(work["id"], work["revision"], disposition, "Synthetic pause/drop")
    before = full(store, work)
    ack = proof(store, before, ws)
    after = full(store, work)
    assert ack["id"] == after["attempts"][0]["id"] and ack["revision"] == 1
    assert ack["task_revision"] == before["revision"] + 1
    assert ack["workstream_ids"] == before["workstream_ids"] and ack["status"] == disposition
    assert not {"summary", "evidence", "artifacts", "verification", "attempts"} & ack.keys()
    retained = set(before) - {"attempts", "revision", "updated_at"}
    assert {k: before[k] for k in retained} == {k: after[k] for k in retained}
    attempt = after["attempts"][0]
    assert attempt["state"] == "review" and attempt["reviewer"] is None
    assert attempt["implementer"] == "fixture implementer"
    assert attempt["specification_etag"] == before["specification_etag"]
    assert attempt["spec_revision"] == before["spec_revision"]
    # Keep the prerequisite in the inbox without changing the recovered task's placement.
    store.remove_from_workstream(blocker["id"], ws, full(store, blocker)["revision"])
    assert full(store, work)["workstream_ids"] == before["workstream_ids"]
    assert store.get_next_action(ws)["action"] is None
    # Explicit manual evidence review remains usable and grants no acceptance/completion.
    store.record_review(
        ack["id"], 1, "independent fixture reviewer", "pass", "Synthetic manual check"
    )
    assert store.get_next_action(ws)["action"] is None
    assert full(store, work)["blocked_by"] == [blocker["id"]]
    with pytest.raises(TaskError, match="task_not_ready_for_signoff|invalid_signoff_target"):
        store.signoff_task(
            work["id"], ack["task_revision"], "approve", "Synthetic approval", ack["id"], 2
        )


def test_result_contract_requires_current_full_spec_and_concrete_proof_atomically(tmp_path):
    store, project, ws = setup(tmp_path)
    work = task(store, project, ws)
    other = task(store, project, ws, "Other specification")
    before = business_state(store)
    bad = [
        ({"specification_etag": ""}, "full_specification_required"),
        ({"specification_etag": other["specification_etag"]}, "full_specification_required"),
        ({"expected_revision": 0}, "revision_conflict"),
        ({"artifacts": []}, r"durable_artifacts_required: .*\{kind, reference\}.*artifact or"),
        ({"artifacts": [{"kind": [], "reference": "bad"}]}, "durable_artifacts_required"),
        ({"artifacts": [{"kind": "commit", "reference": " "}]}, "durable_artifacts_required"),
        ({"verification": " "}, "result_required"),
        ({"implementer": " "}, "result_required"),
    ]
    for overrides, error in bad:
        with pytest.raises(TaskError, match=error):
            proof(store, work, ws, **overrides)
        assert business_state(store) == before
    amended = store.update_task(work["id"], work["revision"], {"title": "Changed specification"})
    with pytest.raises(TaskError, match="full_specification_required"):
        proof(store, work, ws, expected_revision=amended["revision"])
    assert not full(store, work)["attempts"]
    # A gate revision leaves the full-spec token valid; task revision is checked separately.
    current = full(store, work)
    gated = store.add_unresolved(work["id"], current["revision"], "Question")
    ack = proof(store, current, ws, expected_revision=gated["revision"])
    assert (
        ack["workstream_ids"] == current["workstream_ids"]
        and "unresolved_items" in ack["gate_diagnostics"]
    )


def test_mutability_project_and_scope_remain_result_gates(tmp_path):
    store, project, ws = setup(tmp_path)
    work = task(store, project, ws)
    empty = store.init_workstream(project, str(tmp_path / "checkout"), "empty", confirmed=True)
    recovered = proof(store, work, empty["workstream"]["id"])
    assert recovered["workstream_ids"] == [ws]
    work = full(store, work)
    remote = store.init(str(tmp_path / "remote"), "main", action="create_project", confirmed=True)
    with pytest.raises(TaskError, match="unknown_workstream"):
        proof(store, work, remote["workstream"]["id"])
    group = store.create_group(ws, "Context only")
    with pytest.raises(TaskError, match="group_not_executable"):
        proof(store, group, ws)
    ack = proof(store, work, ws)
    store.record_review(ack["id"], 1, "fresh reviewer", "pass", "Synthetic review")
    store.signoff_task(
        work["id"], ack["task_revision"], "approve", "Synthetic verdict", ack["id"], 2
    )
    done = full(store, work)
    with pytest.raises(TaskError, match="completed_task_immutable"):
        proof(store, done, ws)
    assert full(store, work) == done


def test_restart_selects_one_complete_local_current_pending_proof_and_preserves_history(tmp_path):
    store, project, ws = setup(tmp_path)
    work = task(store, project, ws)
    old = proof(store, work, ws, evidence="Old spec proof")
    current = full(store, work)
    store.update_task(
        work["id"],
        current["revision"],
        {"title": "Current requirements"},
    )
    remote = store.init_workstream(project, str(tmp_path / "checkout"), "remote", confirmed=True)[
        "workstream"
    ]["id"]
    remote_ack = proof(store, full(store, work), remote, evidence="Other workstream proof")
    first = proof(store, full(store, work), ws, evidence="Earlier current local proof")
    second = proof(
        store, full(store, work), ws, evidence="Full selected evidence\n" + "details\n" * 800
    )
    # Identical timestamps require the stable ID tie-break; ordinary timestamps choose newest.
    selected = store.get_next_action(ws)
    assert selected["action"] == "review" and selected["attempt"]["id"] == second["id"]
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE attempts SET created_at=? WHERE id IN (?,?)",
            ("2030-01-01", first["id"], second["id"]),
        )
    history = full(store, work)["attempts"]
    store = Store(store.path)
    resumed = store.init(str(tmp_path / "checkout"), "main")
    assert resumed["workstream"]["id"] == ws
    selected = store.get_next_action(ws)
    expected = min(first["id"], second["id"])
    assert selected["action"] == "review" and selected["attempt"]["id"] == expected
    assert selected["attempt"] == next(a for a in history if a["id"] == expected)
    assert selected["task"]["body"] == work["body"]
    assert selected["task"]["spec_revision"] == 2 and selected["task"]["specification_etag"]
    assert selected["task"]["gate_diagnostics"] == ["review"]
    assert "attempts" not in selected["task"] and "signoff_decisions" not in selected["task"]
    assert old["id"] != expected and remote_ack["id"] != expected
    assert full(store, work)["attempts"] == history
    # Every returned byte of the selected proof is available without another read.
    if expected == second["id"]:
        assert len(selected["attempt"]["evidence"]) > 5000


def test_workstream_order_actions_rework_findings_and_human_waiting_are_read_only(tmp_path):
    store, project, ws = setup(tmp_path)
    first = task(store, project, ws, "First implementation")
    later = task(store, project, ws, "Later pending review")
    pending = proof(store, later, ws)
    selected = store.get_next_action(ws)
    assert selected["action"] == "implement" and selected["task"]["id"] == first["id"]
    board = store.list_tasks(project, ws)
    store.reorder_tasks(
        ws,
        [later["id"]],
        board["workstream_order_revision"],
    )
    selected = store.get_next_action(ws)
    assert selected["action"] == "review" and selected["attempt"]["id"] == pending["id"]
    store.record_review(pending["id"], 1, "fresh reviewer", "rework", "Fix the actual defect")
    selected = store.get_next_action(ws)
    assert selected["action"] == "implement"
    assert selected["attempt"]["review_note"] == "Fix the actual defect"
    assert selected["attempt"]["reviewer"] == "fresh reviewer"
    retry = proof(store, full(store, later), ws)
    # Pending review takes precedence even if another passed local attempt exists.
    passed = proof(store, full(store, later), ws)
    store.human_review(passed["id"], 1, "Synthetic actual human review")
    assert store.get_next_action(ws)["attempt"]["id"] == retry["id"]
    store.record_review(retry["id"], 1, "another reviewer", "pass", "Synthetic pass")
    assert store.get_next_action(ws)["task"]["id"] == first["id"]
    first_result = proof(store, first, ws)
    store.record_review(first_result["id"], 1, "first reviewer", "pass", "Synthetic pass")
    before = business_state(store)
    for _ in range(2):
        waiting = store.get_next_action(ws)
        assert waiting["action"] is waiting["task"] is waiting["attempt"] is None
        assert waiting["diagnostics"]["signoff"] == 2
        assert len(waiting["diagnostics"]) == 8
    assert business_state(store) == before


def test_legacy_proof_and_audit_survive_factual_record_and_selector(tmp_path):
    store, project, ws = setup(tmp_path)
    work = task(store, project, ws)
    legacy = proof(store, work, ws)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE attempts SET evidence=? WHERE id=?", ("Historical plain proof", legacy["id"])
        )
        events = db.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        old_row = db.execute("SELECT * FROM attempts WHERE id=?", (legacy["id"],)).fetchone()
        schema = db.execute("PRAGMA user_version").fetchone()
    assert store.get_next_action(ws)["attempt"]["evidence"] == "Historical plain proof"
    recovered = proof(store, full(store, work), ws)
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("SELECT * FROM attempts WHERE id=?", (legacy["id"],)).fetchone() == old_row
        )
        assert (
            db.execute("SELECT * FROM events ORDER BY sequence").fetchall()[: len(events)] == events
        )
        assert db.execute("PRAGMA user_version").fetchone() == schema == (DATABASE_SCHEMA_REVISION,)
        raw = json.loads(
            db.execute("SELECT evidence FROM attempts WHERE id=?", (recovered["id"],)).fetchone()[0]
        )
        assert raw["format"] == "durable-result-v1" and raw["artifacts"]
