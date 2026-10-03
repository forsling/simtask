"""Exact approved amendments and full-spec replacement guards on disposable databases."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from task_mcp.server import create_server
from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    initialized = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    return store, initialized["project"]["id"], initialized["workstream"]["id"]


def approval(basis="specific"):
    return {"basis": basis, "note": "Synthetic authorization of the resulting exact scope"}


def full(store, task):
    return store.get_tasks([task["id"]])["items"][0]


@pytest.mark.parametrize("basis", ["specific", "delegated"])
def test_amendment_saves_and_accepts_exact_result_in_one_revision(context, basis):
    store, project, ws = context
    task = store.create_task(
        project, "Original", approval=approval(), workstream_id=ws, scope="workstream"
    )
    before = full(store, task)
    changes = {"title": "Amended", "body": "Full amended scope", "acceptance_criteria": "Proof"}
    ack = store.update_task(
        task["id"], before["revision"], changes, approval(basis), before["specification_etag"]
    )
    assert ack["changed"] and ack["spec_changed"] and ack["approval_changed"]
    assert ack["revision"] == 2 and ack["spec_revision"] == ack["accepted_spec_revision"] == 2
    assert ack["accepted"] and ack["acceptance_basis"] == basis
    assert not {"body", "title", "attempts", "acceptance_note"} & ack.keys()
    after = full(store, ack)
    assert all(after[key] == value for key, value in changes.items())
    assert store.get_next_action(ws)["task"]["specification_etag"] == after["specification_etag"]
    assert after["acceptance_note"] == approval(basis)["note"]
    assert after["attempts"] == []
    with sqlite3.connect(store.path) as db:
        events = db.execute(
            "SELECT request_json,before_json,after_json FROM events WHERE action='task.updated'"
        ).fetchall()
    assert len(events) == 1
    request, old, saved = map(json.loads, events[0])
    assert request["approval"] == approval(basis)
    assert old["spec_revision"] == old["accepted_spec_revision"] == 1
    assert saved["spec_revision"] == saved["accepted_spec_revision"] == 2


@pytest.mark.parametrize("field", ["title", "body", "acceptance_criteria"])
def test_every_real_spec_field_edit_without_approval_becomes_pending(context, field):
    store, project, _ = context
    task = store.create_task(project, "Original", approval=approval())
    before = full(store, task)
    ack = store.update_task(
        task["id"],
        1,
        {field: "Even editorial edits change the spec"},
        specification_etag=before["specification_etag"],
    )
    assert ack["spec_changed"] and ack["approval_changed"] and not ack["accepted"]
    assert ack["spec_revision"] == 2 and ack["accepted_spec_revision"] == 1
    assert full(store, task)["acceptance_note"] == before["acceptance_note"]


def test_noop_and_unchanged_pending_approval_do_not_bump_spec(context):
    store, project, _ = context
    task = store.create_task(project, "Pending", body="Whole body", acceptance_criteria="Criteria")
    before = full(store, task)
    ack = store.update_task(
        task["id"], 1, {"body": before["body"]}, specification_etag=before["specification_etag"]
    )
    assert not ack["changed"] and not ack["spec_changed"] and not ack["approval_changed"]
    assert full(store, task)["updated_at"] == before["updated_at"]
    approved = store.update_task(task["id"], 1, {}, approval())
    assert approved["changed"] and approved["approval_changed"] and not approved["spec_changed"]
    assert approved["revision"] == 2 and approved["spec_revision"] == 1
    assert approved["accepted"]
    noop = store.update_task(task["id"], 2, {"title": "Pending"}, approval())
    assert not noop["changed"] and noop["revision"] == 2
    with pytest.raises(TaskError, match="revision_conflict"):
        store.update_task(task["id"], 1, {})


def test_replacements_require_full_current_task_spec_even_with_approval(context):
    store, project, _ = context
    task = store.create_task(
        project,
        "Long",
        body="Requirements\n" * 5000,
        acceptance_criteria="Do not lose the full scope",
        approval=approval(),
    )
    other = store.create_task(
        project,
        "Other",
        body="Requirements\n" * 5000,
        acceptance_criteria="Do not lose the full scope",
    )
    before = full(store, task)
    assert "specification_etag" not in store.list_tasks(project)["items"][0]
    for field in ("body", "acceptance_criteria"):
        for token in (None, "wrong", full(store, other)["specification_etag"]):
            with pytest.raises(TaskError, match="specification_read_required.*full specification"):
                store.update_task(task["id"], 1, {field: "A preview"}, approval(), token)
            assert full(store, task) == before
    renamed = store.update_task(task["id"], 1, {"title": "Current"})
    with pytest.raises(TaskError, match="specification_read_required"):
        store.update_task(
            task["id"],
            renamed["revision"],
            {"body": "Replacement"},
            approval(),
            before["specification_etag"],
        )
    with pytest.raises(TaskError, match="revision_conflict.*full specification"):
        store.update_task(
            task["id"], 1, {"body": "Stale replacement"}, approval(), before["specification_etag"]
        )
    assert full(store, task)["body"] == before["body"]


def test_non_spec_gate_revision_keeps_token_but_requires_current_revision(context):
    store, project, ws = context
    dependency = store.create_task(project, "Dependency")
    task = store.create_task(project, "Task")  # inbox must stay out of scope
    before = full(store, task)
    gated = store.add_unresolved(task["id"], 1, "Open decision")
    gated = store.add_prerequisite(task["id"], gated["revision"], dependency["id"])
    proposal = store.add_unresolved(task["id"], gated["revision"], "Observed blocker", "observer")
    gated = full(store, task)
    assert gated["specification_etag"] == before["specification_etag"]
    ack = store.update_task(
        task["id"],
        gated["revision"],
        {"body": "Approved amendment"},
        approval("delegated"),
        before["specification_etag"],
    )
    assert ack["gate_diagnostics"] == ["unresolved_items", "prerequisites", "task_out_of_scope"]
    after = full(store, task)
    assert after["unresolved_items"] == gated["unresolved_items"]
    assert after["blocked_by"] == gated["blocked_by"]
    assert after["gate_proposals"] == gated["gate_proposals"]
    assert after["gate_proposals"][0]["id"] == proposal["id"]
    assert store.get_next_action(ws)["task"] is None
    with pytest.raises(TaskError, match="task_not_eligible|task_out_of_scope"):
        store.record_result(
            task["id"],
            ws,
            ack["revision"],
            "worker",
            "Done",
            "Proof",
            artifacts=[{"kind": "artifact", "reference": "tests/test_amendments.py"}],
            verification="Proof",
            specification_etag=store.get_tasks([task["id"]])["items"][0]["specification_etag"],
        )
    deferred = store.set_disposition(task["id"], ack["revision"], "deferred", "Later")
    accepted = store.update_task(
        task["id"], deferred["revision"], {"title": "Deferred scope"}, approval()
    )
    assert accepted["status"] == "deferred" and accepted["accepted"]
    assert full(store, task)["unresolved_items"] == gated["unresolved_items"]


def test_invalid_approval_and_failed_audit_roll_back_both_sides(context, monkeypatch):
    store, project, _ = context
    task = store.create_task(project, "Original", approval=approval())
    before = full(store, task)
    with pytest.raises(TaskError, match="invalid_approval"):
        store.update_task(task["id"], 1, {"title": "Wrong"}, {"basis": "specific", "note": " "})
    assert full(store, task) == before
    original_event = store._event

    def fail_audit(db, action, *args, **kwargs):
        if action == "task.updated":
            raise RuntimeError("Synthetic audit failure")
        return original_event(db, action, *args, **kwargs)

    monkeypatch.setattr(store, "_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit failure"):
        store.update_task(task["id"], 1, {"title": "Atomic"}, approval("delegated"))
    assert full(store, task) == before


def test_concurrent_approved_amendments_have_one_atomic_winner(context):
    store, project, _ = context
    task = store.create_task(project, "Original", approval=approval())
    token = full(store, task)["specification_etag"]
    barrier = Barrier(2)

    def run(body):
        barrier.wait()
        try:
            return store.update_task(
                task["id"], 1, {"body": body}, {"basis": "specific", "note": body}, token
            )
        except TaskError as error:
            return str(error)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, ("First exact scope", "Second exact scope")))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum("revision_conflict" in result for result in results if isinstance(result, str)) == 1
    after = full(store, task)
    assert after["revision"] == after["spec_revision"] == after["accepted_spec_revision"] == 2
    assert after["body"] == after["acceptance_note"]


def test_mcp_schema_and_handler_share_replacement_and_approval_contract(context):
    store, project, _ = context
    server = create_server(store)
    catalog = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    schema = catalog["update_task"].input_schema
    assert {"approval", "specification_etag"} <= schema["properties"].keys()
    assert set(schema["required"]) == {"task_id", "expected_revision", "changes"}
    assert "whole-field replacements" in catalog["update_task"].description
    task = store.create_task(project, "Pending")

    async def run():
        read = await server.call_tool("get_tasks", {"ids": [task["id"]], "specification": True})
        token = read.structured_content["items"][0]["specification_etag"]
        saved = await server.call_tool(
            "update_task",
            {
                "task_id": task["id"],
                "expected_revision": 1,
                "changes": {"body": "Exact scope"},
                "approval": approval(),
                "specification_etag": token,
            },
        )
        assert saved.structured_content["accepted"]
        assert saved.structured_content["spec_changed"]

    asyncio.run(run())


def test_group_context_edits_require_read_but_do_not_imply_approval(context):
    store, _, ws = context
    group = store.create_group(ws, "Group", body="Complete context")
    with pytest.raises(TaskError, match="specification_read_required"):
        store.update_task(group["id"], 1, {"body": "Amended context"})
    ack = store.update_task(
        group["id"], 1, {"body": "Amended context"}, specification_etag=group["specification_etag"]
    )
    assert ack["accepted"] is None and ack["gate_diagnostics"] == []
    with pytest.raises(TaskError, match="group_not_executable"):
        store.update_task(group["id"], ack["revision"], {}, approval())
