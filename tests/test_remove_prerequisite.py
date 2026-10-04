"""Remove canonical dependency links atomically without rewriting task authority or proof."""

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from task_mcp.runtime import PROTOCOL_SCHEMA_REVISION
from task_mcp.store import DATABASE_SCHEMA_REVISION, Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="removal-coordinator")
    setup = store.init(str(tmp_path / "checkout"), "main", action="create_project", confirmed=True)
    return store, setup["project"]["id"], setup["workstream"]["id"]


def create(context, title, accepted=True):
    store, project, ws = context
    return store.create_task(
        project,
        title,
        body=f"Requirements for {title}",
        acceptance_criteria="Keep the recorded specification and proof",
        workstream_id=ws if accepted else None,
    )


def detail(store, identity):
    return store.get_tasks([identity])["items"][0]


def record(context, task):
    store, _, ws = context
    return store.record_result(
        task["id"],
        ws,
        task["revision"],
        "fixture implementer",
        "Recorded proof",
        "Synthetic durable evidence",
        artifacts=[{"kind": "artifact", "reference": __file__}],
        verification="Fixture checks",
        specification_etag=task["specification_etag"],
    )


def complete(context, task):
    store, _, _ = context
    attempt = record(context, task)
    store.record_review(attempt["id"], 1, "fixture reviewer", "pass", "Fixture review")
    return store.signoff_task(
        task["id"],
        task["revision"] + 1,
        "approve",
        "Synthetic informed approval",
        attempt["id"],
        expected_attempt_revision=2,
    )


@pytest.mark.parametrize("milestone", ["review", "signoff"])
def test_removal_immediately_recalculates_gate_and_audits_actor_note(context, milestone):
    store, project, ws = context
    dependent = create(context, "Dependent")
    blocker = create(context, "Blocker")
    linked = store.add_prerequisite(dependent["id"], 1, blocker["id"], milestone=milestone)
    ack = store.compact_call(
        "remove_prerequisite", dependent["id"], linked["revision"], blocker["id"], "Wrong link"
    )
    assert ack["changed"] and ack["revision"] == ack["task_revision"] == 3
    assert ack["blocked_by_id"] == blocker["id"]
    assert ack["gate_diagnostics"] == [] and ack["workstream_ids"]
    assert not {"body", "attempts", "prerequisites"} & ack.keys()
    after = detail(store, dependent["id"])
    assert after["blocked_by"] == after["prerequisites"] == []
    assert after["spec_revision"] == 1 and after["workstream_ids"] == [ws]
    row = store.list_tasks(project, ws)["items"][0]
    assert row["id"] == dependent["id"] and row["view"] == "ready"
    selected = store.get_next_action(ws)
    assert selected["action"] == "implement" and selected["task"]["id"] == dependent["id"]
    events = store.list_events(task_id=dependent["id"], include_details=True)["items"]
    removed = next(event for event in events if event["action"] == "gate.prerequisite_removed")
    assert removed["actor"] == "removal-coordinator" and removed["outcome"] == "ok"
    assert removed["request"]["note"] == removed["after"]["note"] == "Wrong link"
    assert removed["before"]["task"]["revision"] == 2
    assert removed["after"]["task"]["revision"] == 3
    assert (
        removed["before"]["prerequisite"]
        == removed["after"]["removed_prerequisite"]
        == {
            "task_id": dependent["id"],
            "blocked_by_id": blocker["id"],
            "milestone": milestone,
        }
    )
    assert removed["after"]["changed"]


@pytest.mark.parametrize("milestone", ["review", "signoff"])
def test_removal_preserves_other_milestones_specification_and_reviewed_proof(context, milestone):
    store, _, ws = context
    dependent = create(context, "Dependent")
    removed_blocker = create(context, "Mistaken blocker")
    retained_blocker = create(context, "Actual blocker")
    store.add_prerequisite(dependent["id"], 1, removed_blocker["id"], milestone=milestone)
    opposite = "signoff" if milestone == "review" else "review"
    dependent = store.add_prerequisite(
        dependent["id"], 2, retained_blocker["id"], milestone=opposite
    )
    attempt = record(context, dependent)
    store.record_review(attempt["id"], 1, "fixture reviewer", "pass", "Review retained")
    before = detail(store, dependent["id"])
    before_ws = store.workstream_status(ws)["workstream"]
    blocker_before = detail(store, removed_blocker["id"])
    after = store.remove_prerequisite(
        dependent["id"], before["revision"], removed_blocker["id"], "Remove mistaken dependency"
    )
    retained = set(before) - {"revision", "updated_at", "blocked_by", "prerequisites"}
    assert {k: before[k] for k in retained} == {k: after[k] for k in retained}
    assert after["blocked_by"] == [retained_blocker["id"]]
    assert after["prerequisites"] == [
        ref for ref in before["prerequisites"] if ref["id"] == retained_blocker["id"]
    ]
    assert after["prerequisites"][0]["milestone"] == opposite
    assert store.workstream_status(ws)["workstream"] == before_ws
    assert detail(store, removed_blocker["id"]) == blocker_before
    assert store.get_next_action(ws)["task"]["id"] != dependent["id"]


@pytest.mark.parametrize("blocked_by_id", ["unknown-task", "self"])
def test_absent_link_is_audited_noop_with_identical_revision_and_timestamp(context, blocked_by_id):
    store, _, _ = context
    task = detail(store, create(context, "Dependent")["id"])
    identity = task["id"] if blocked_by_id == "self" else blocked_by_id
    result = store.remove_prerequisite(task["id"], task["revision"], identity, "No longer needed")
    assert not result.pop("changed") and result == task
    ack = store.compact_call(
        "remove_prerequisite", task["id"], task["revision"], identity, "No longer needed"
    )
    assert not ack["changed"] and ack["revision"] == task["revision"]
    assert detail(store, task["id"]) == task
    events = store.list_events(task_id=task["id"], include_details=True)["items"]
    removals = [event for event in events if event["action"] == "gate.prerequisite_removed"]
    assert len(removals) == 2
    for event in removals:
        assert event["actor"] == "removal-coordinator" and event["outcome"] == "ok"
        assert not event["after"]["changed"] and event["before"]["prerequisite"] is None
        assert event["after"]["note"] == "No longer needed"


@pytest.mark.parametrize("link_exists", [False, True])
def test_stale_revision_is_rejected_even_for_absent_link(context, link_exists):
    store, _, _ = context
    task = create(context, "Dependent")
    blocker = create(context, "Blocker")
    if link_exists:
        store.add_prerequisite(task["id"], 1, blocker["id"])
    before = detail(store, task["id"])
    with pytest.raises(TaskError, match="revision_conflict"):
        store.remove_prerequisite(task["id"], 0, blocker["id"], "Remove obsolete dependency")
    assert detail(store, task["id"]) == before


@pytest.mark.parametrize("note", ["", " \n ", None, 4])
def test_required_note_fails_without_removing_or_bumping_revision(context, note):
    store, _, _ = context
    task = create(context, "Dependent")
    blocker = create(context, "Blocker")
    store.add_prerequisite(task["id"], 1, blocker["id"])
    before = detail(store, task["id"])
    with pytest.raises(TaskError, match="decision_note_required"):
        store.remove_prerequisite(task["id"], before["revision"], blocker["id"], note)
    assert detail(store, task["id"]) == before


@pytest.mark.parametrize("link_exists", [False, True])
def test_completed_task_is_immutable_even_for_absent_link(context, link_exists):
    store, _, _ = context
    blocker = complete(context, detail(store, create(context, "Blocker")["id"]))
    task = create(context, "Dependent")
    if link_exists:
        store.add_prerequisite(task["id"], 1, blocker["id"], milestone="signoff")
    completed = complete(context, detail(store, task["id"]))
    before = detail(store, task["id"])
    with pytest.raises(TaskError, match="completed_task_immutable"):
        store.remove_prerequisite(
            task["id"], completed["revision"], blocker["id"], "Remove obsolete dependency"
        )
    assert detail(store, task["id"]) == before


def test_groups_have_same_mutability_rule_as_add_prerequisite(context):
    store, _, ws = context
    group = store.create_group(ws, "Context only")
    blocker = create(context, "Blocker")
    with pytest.raises(TaskError, match="group_not_executable"):
        store.remove_prerequisite(group["id"], 1, blocker["id"], "Remove obsolete dependency")


@pytest.mark.parametrize("disposition", ["open", "deferred", "dropped"])
def test_removal_preserves_inbox_and_disposition(context, disposition):
    store, _, _ = context
    task = create(context, "Pending dependent", accepted=False)
    blocker = create(context, "Blocker")
    task = store.add_prerequisite(task["id"], 1, blocker["id"])
    if disposition != "open":
        task = store.set_disposition(
            task["id"], task["revision"], disposition, "Synthetic decision"
        )
    result = store.remove_prerequisite(task["id"], task["revision"], blocker["id"], "Wrong link")
    assert result["workstream_ids"] == [] and result["status"] == disposition
    assert result["spec_revision"] == 1 and result["workstream_ids"] == []


def test_failed_audit_rolls_back_link_deletion_and_task_revision(context):
    store, _, _ = context
    task = create(context, "Dependent")
    blocker = create(context, "Blocker")
    store.add_prerequisite(task["id"], 1, blocker["id"], milestone="signoff")
    before = detail(store, task["id"])
    with sqlite3.connect(store.path) as db:
        db.execute("""CREATE TRIGGER reject_removal BEFORE INSERT ON events
            WHEN NEW.action='gate.prerequisite_removed'
            BEGIN SELECT RAISE(ABORT, 'audit rejected'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="audit rejected"):
        store.remove_prerequisite(task["id"], before["revision"], blocker["id"], "Wrong link")
    assert detail(store, task["id"]) == before


def test_fresh_stdio_discovers_and_calls_removal_tool_on_disposable_database(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "stdio.sqlite3"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "task_mcp", "--db", str(database), "--actor", "stdio-remover", "--no-trace"],
        env={"PYTHONPATH": str(root / "src"), "TASK_MCP_DB": str(database)},
    )

    async def exercise():
        async with Client(params, read_timeout_seconds=30) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            tool = tools["remove_prerequisite"]
            assert len(tools) == 42
            assert tool.annotations.read_only_hint is False
            assert tool.annotations.destructive_hint is True
            assert set(tool.input_schema["required"]) == {
                "task_id",
                "expected_revision",
                "blocked_by_id",
                "note",
            }
            assert "add_prerequisite" in client.instructions
            assert "remove_prerequisite" in client.instructions

            async def call(name, **arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.content
                return result.structured_content

            runtime = await call("runtime_info")
            assert runtime["package_path"] == str(root / "src/task_mcp")
            assert runtime["protocol_schema_revision"] == PROTOCOL_SCHEMA_REVISION
            assert runtime["database_schema_revision"] == DATABASE_SCHEMA_REVISION
            setup = await call(
                "init",
                path=str(tmp_path / "checkout"),
                branch="main",
                action="create_project",
                confirmed=True,
            )
            ws = setup["workstream"]["id"]
            arguments = dict(
                project=setup["project"]["id"],
                workstream_id=ws,
            )
            dependent = await call("create_task", title="Dependent", **arguments)
            blocker = await call("create_task", title="Blocker", **arguments)
            for milestone in ("review", "signoff"):
                linked = await call(
                    "add_prerequisite",
                    task_id=dependent["id"],
                    expected_revision=dependent["revision"],
                    blocked_by_id=blocker["id"],
                    milestone=milestone,
                )
                assert "prerequisites" in linked["gate_diagnostics"]
                dependent = await call(
                    "remove_prerequisite",
                    task_id=dependent["id"],
                    expected_revision=linked["revision"],
                    blocked_by_id=blocker["id"],
                    note=f"Synthetic removal of obsolete {milestone} link",
                )
                assert dependent["changed"] and dependent["revision"] == linked["revision"] + 1
                assert dependent["gate_diagnostics"] == []
                selected = await call("get_next_action", workstream_id=ws)
                assert selected["task"]["id"] == dependent["id"]
            no_op = await call(
                "remove_prerequisite",
                task_id=dependent["id"],
                expected_revision=dependent["revision"],
                blocked_by_id=blocker["id"],
                note="Synthetic repeat after deletion",
            )
            assert not no_op["changed"] and no_op["revision"] == dependent["revision"]
            stale = await client.call_tool(
                "remove_prerequisite",
                {
                    "task_id": dependent["id"],
                    "expected_revision": 1,
                    "blocked_by_id": blocker["id"],
                    "note": "Synthetic stale deletion",
                },
            )
            assert stale.is_error and "revision_conflict" in str(stale.content)
            events = await call("list_events", task_id=dependent["id"], include_details=True)
            removals = [
                event
                for event in events["items"]
                if event["action"] == "gate.prerequisite_removed" and event["outcome"] == "ok"
            ]
            assert len(removals) == 3 and all(
                event["actor"] == "stdio-remover" for event in removals
            )
            assert all(event["request"]["note"] for event in removals)

    asyncio.run(exercise())
