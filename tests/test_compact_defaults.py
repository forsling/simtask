import asyncio
import json

import pytest

from task_mcp.server import create_server
from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "compact.sqlite3")
    setup = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    return store, setup["project"]["id"], setup["workstream"]["id"]


def test_summary_freshness_completion_and_token_continuation(context):
    store, project, ws = context
    task = store.create_task(
        project,
        "Deliver",
        body="Complete requirements" * 3000,
        summary="Preserve complete requirements.",
        workstream_id=ws,
    )
    token = task["specification_etag"]
    renamed = store.update_task(
        task["id"],
        task["revision"],
        {"title": "Deliver safely"},
        specification_etag=token,
    )
    assert renamed["spec_revision"] == 2 and renamed["specification_etag"] != token
    card = store.read_tasks([task["id"]])["items"][0]
    assert (
        card["summary_stale"]
        and not {"body", "acceptance_criteria", "specification_etag", "view"} & card.keys()
    )
    refreshed = store.update_task(task["id"], renamed["revision"], {"summary": card["summary"]})
    assert (
        refreshed["changed"] and not refreshed["spec_changed"] and refreshed["queue_workstream_id"]
    )
    assert "specification_etag" not in refreshed
    same = store.update_task(task["id"], refreshed["revision"], {"summary": card["summary"]})
    assert not same["changed"] and same["revision"] == refreshed["revision"]
    assert store.read_tasks([task["id"]])["items"][0]["summary_stale"] is False
    with pytest.raises(TaskError, match="specification_read_required"):
        store.update_task(task["id"], same["revision"], {"body": card["summary"]})
    result = store.record_result(
        task["id"],
        ws,
        same["revision"],
        "worker",
        "Built",
        "Exact evidence",
        [{"kind": "artifact", "reference": "synthetic.txt"}],
        "Checked",
        renamed["specification_etag"],
    )
    reviewed = store.compact_call(
        "record_review", result["id"], result["revision"], "reviewer", "pass", "Checked"
    )
    done = store.signoff_task(
        task["id"],
        reviewed["task_revision"],
        "approve",
        "Synthetic verdict",
        result["id"],
        reviewed["attempt_revision"],
    )
    before = store.get_tasks([task["id"]])["items"][0]
    corrected = store.update_task(
        task["id"], done["revision"], {"summary": "Corrected descriptive intent."}
    )
    after = store.get_tasks([task["id"]])["items"][0]
    assert corrected["changed"] and after["status"] == "done" and after["queue_workstream_id"]
    assert (
        after["body"],
        after["spec_revision"],
        after["attempts"],
        after["selected_attempt_id"],
    ) == (
        before["body"],
        before["spec_revision"],
        before["attempts"],
        before["selected_attempt_id"],
    )
    with pytest.raises(TaskError, match="completed_task_immutable"):
        store.update_task(task["id"], corrected["revision"], {"title": "New requirement"})
    cleared = store.update_task(task["id"], corrected["revision"], {"summary": None})
    assert (
        cleared["changed"] and store.read_tasks([task["id"]])["items"][0]["summary_stale"] is None
    )


@pytest.mark.parametrize("summary", [" ", "\n", "a\nb", "a\rb", "a\u2028b", "x" * 241, 4])
def test_invalid_summary_is_atomic(context, summary):
    store, project, ws = context
    task = store.create_task(project, "Task", summary="Intent")
    with pytest.raises(TaskError, match="invalid_summary"):
        store.update_task(task["id"], 1, {"summary": summary})
    assert store.read_tasks([task["id"]])["items"][0]["revision"] == 1
    with pytest.raises(TaskError, match="invalid_summary"):
        store.create_group(ws, "Group", summary=summary)
    with pytest.raises(TaskError, match="invalid_summary"):
        store.create_task(project, "Invalid", summary=summary)
    assert len(store.list_tasks(project)["items"]) == 1


def test_bounded_projections_exact_proof_and_local_gates(context, tmp_path):
    store, project, ws = context
    other = store.init_workstream(project, str(tmp_path / "repo"), branch="other", confirmed=True)[
        "workstream"
    ]["id"]
    task = store.create_task(
        project,
        "Long specification",
        body="Unabridged scope\n" * 4000,
        workstream_id=ws,
    )
    revision = task["revision"]
    attempts = []
    for stream in (other, ws, ws, ws, ws):
        attempt = store.record_result(
            task["id"],
            stream,
            revision,
            "worker",
            "Summary\n" * 1000,
            "Proof\n" * 5000,
            [{"kind": "artifact", "reference": "proof"}],
            "Tests passed",
            task["specification_etag"],
        )
        revision = attempt["task_revision"]
        attempts.append(attempt)
    scoped = store.read_tasks([task["id"]], workstream_id=ws)["items"][0]
    assert scoped["view"] == "review" and scoped["attempt_counts"]["review"] == 4
    assert scoped["attempt_reference"]["attempt_id"] == attempts[-1]["id"]
    assert scoped["alternative_attempt_count"] == 3
    unscoped = store.read_tasks([task["id"]])["items"][0]
    assert "view" not in unscoped and unscoped["aggregate_attempt_counts"]["review"] == 5
    assert "review" not in unscoped["gate_diagnostics"]
    spec = store.read_tasks([task["id"]], True, ws, [attempts[0]["id"]])["items"][0]
    assert len(spec["body"]) == 68000 and len(spec["attempt_summaries"]) == 3
    assert spec["attempt_total"] == spec["actionable_total"] == 4 and spec["has_more"]
    assert [a["id"] for a in spec["attempts"]] == [attempts[0]["id"]]
    assert spec["attempts"][0]["workstream_id"] == other
    assert all(len(a["summary"]) <= 240 for a in spec["attempt_summaries"])
    page = store.list_task_attempts(task["id"], ws, ["review", "passed", "human_review"], limit=2)
    next_page = store.list_task_attempts(
        task["id"], ws, ["review", "passed", "human_review"], limit=2, cursor=page["next_cursor"]
    )
    assert len({a["attempt_id"] for a in page["items"] + next_page["items"]}) == 4
    assert not next_page["has_more"]
    with pytest.raises(TaskError, match="invalid_cursor"):
        store.list_task_attempts(task["id"], other, cursor=page["next_cursor"])
    exact = store.get_attempt(attempts[0]["id"])
    assert exact["evidence"] == "Proof\n" * 5000 and exact["spec_revision"] == 1
    unrelated = store.create_task(project, "Unrelated")
    with pytest.raises(TaskError, match="invalid_attempt_owner"):
        store.read_tasks([unrelated["id"]], True, attempt_ids=[attempts[0]["id"]])
    updated = store.update_task(
        task["id"], revision, {"body": "New complete scope"}, task["specification_etag"]
    )
    assert store.read_tasks([task["id"]], workstream_id=ws)["items"][0]["view"] == "ready"
    assert store.list_task_attempts(task["id"])["items"] == []
    assert store.list_task_attempts(task["id"], current_spec_only=False)["total"] == 5
    explicit = store.read_tasks([task["id"]], True, ws, [attempts[0]["id"]])["items"][0]
    assert explicit["attempts"][0]["spec_revision"] == 1 and explicit["spec_revision"] == 2
    assert updated["specification_etag"] != task["specification_etag"]


def test_groups_init_events_and_scope_pages_stay_bounded(context):
    store, project, ws = context
    group = store.create_group(ws, "Group", summary="Shared intent")
    for i in range(24):
        group_revision = store.get_tasks([group["id"]])["items"][0]["revision"]
        store.create_task(
            project,
            f"Member {i}",
            group_id=group["id"],
            group_expected_revision=group_revision,
            workstream_id=ws,
        )
    spec = store.read_tasks([group["id"]], True)["items"][0]
    assert len(spec["members"]) == 3 and spec["member_total"] == 24 and spec["members_has_more"]
    card = store.read_tasks([group["id"]])["items"][0]
    assert (
        card["progress"]["total"] == 24
        and "members" not in card
        and "by_project" not in card["progress"]
    )
    first = store.list_group_members(group["id"])
    second = store.list_group_members(group["id"], cursor=first["next_cursor"])
    assert len(first["items"]) == 20 and len(second["items"]) == 4
    assert len({m["id"] for m in first["items"] + second["items"]}) == 24
    ready = store.init(store.workstream_status(ws)["workstream"]["checkout_path"], branch="main")
    assert (
        len(ready["queue"]) == 10
        and ready["queue_total"] == 24
        and ready["queue_next_offset"] == 10
    )
    assert len(store.list_tasks(project)["items"]) == len(store.list_events()["items"]) == 20
    assert all("request" not in e for e in store.list_events()["items"])
    explicit = store.workstream_status(ws, include_scope=True)["scope"]["groups"]
    assert explicit["ids"] == [group["id"]] and explicit["total"] == 1
    store.update_task(group["id"], spec["revision"], {"summary": "A descriptive correction."})
    assert store.read_tasks([group["id"]])["items"][0]["spec_revision"] == 1


def test_every_mcp_write_ack_is_compact_and_can_continue(context):
    store, project, ws = context
    server = create_server(store)
    secret = "Submitted full specification or proof" * 1000

    async def exercise():
        async def call(name, **args):
            result = await server.call_tool(name, args)
            assert not result.is_error, result.content
            data = result.structured_content
            if name not in {"get_tasks", "get_attempt"}:
                assert secret not in json.dumps(data)
                assert "changed" in data
                assert len(json.dumps(data)) < 2500
            return data

        task = await call(
            "create_task",
            project=project,
            title="Concrete",
            body=secret,
            workstream_id=ws,
        )
        gate = await call(
            "add_unresolved", task_id=task["id"], expected_revision=task["revision"], text=secret
        )
        resolved = await call(
            "resolve_unresolved",
            task_id=task["id"],
            expected_revision=gate["revision"],
            item_id=gate["unresolved_id"],
            user_note="Resolved",
        )
        proposal = await call(
            "add_unresolved",
            task_id=task["id"],
            expected_revision=resolved["revision"],
            text=secret,
            handling="observer",
        )
        dismissed = await call(
            "dismiss_gate_proposal",
            proposal_id=proposal["proposal_id"],
            expected_revision=proposal["task_revision"],
            note="Unwanted",
        )
        result = await call(
            "record_result",
            task_id=task["id"],
            workstream_id=ws,
            expected_revision=dismissed["revision"],
            implementer="worker",
            summary="Actual result",
            evidence=secret,
            artifacts=[{"kind": "artifact", "reference": "proof"}],
            verification=secret,
            specification_etag=task["specification_etag"],
        )
        review = await call(
            "record_review",
            attempt_id=result["attempt_id"],
            expected_revision=result["attempt_revision"],
            reviewer="fresh reviewer",
            verdict="pass",
            note=secret,
        )
        assert (
            review["task_revision"] == result["task_revision"] and review["attempt_revision"] == 2
        )
        done = await call(
            "signoff_task",
            task_id=task["id"],
            expected_revision=review["task_revision"],
            attempt_id=result["attempt_id"],
            expected_attempt_revision=review["attempt_revision"],
            decision="approve",
            reasons="Synthetic informed approval",
        )
        assert done["status"] == "done"

    asyncio.run(exercise())


def test_other_public_mutations_return_only_continuation_state(context, tmp_path):
    store, project, ws = context
    server = create_server(store)
    submitted = "Large submitted text.\n" * 2000

    async def exercise():
        async def call(tool_name, **args):
            result = await server.call_tool(tool_name, args)
            assert not result.is_error, result.content
            data = result.structured_content
            assert submitted not in json.dumps(data)
            assert data.get("changed") in (True, False)
            assert len(json.dumps(data)) < 3000
            return data

        await call(
            "attach_checkout", project=project, path=str(tmp_path / "attached"), confirmed=True
        )
        third = await call(
            "init_workstream",
            project=project,
            path=str(tmp_path / "attached"),
            branch="third",
            confirmed=True,
        )
        rebound = await call(
            "rebind_workstream",
            workstream_id=third["workstream"]["id"],
            expected_revision=1,
            path=str(tmp_path / "attached"),
            branch="third",
            confirmed=True,
        )
        assert not rebound["changed"] and rebound["revision"] == 1
        scoped = await call(
            "set_scope",
            workstream_id=third["workstream"]["id"],
            expected_revision=1,
            expression="none",
        )
        assert not scoped["changed"] and scoped["revision"] == 1
        group = await call("create_group", workstream_id=ws, title="Context", body=submitted)
        task = await call(
            "create_task",
            project=project,
            title="Deliver",
            body=submitted,
            workstream_id=ws,
        )
        added = await call(
            "add_group_member",
            group_id=group["id"],
            expected_revision=group["revision"],
            task_id=task["id"],
            expected_task_revision=task["revision"],
        )
        assert added["group"]["group_revision"] == 2 and added["member"]["task_revision"] == 2
        proposed = await call(
            "propose_prerequisite",
            task_id=task["id"],
            expected_revision=added["member"]["revision"],
            title="Prerequisite",
            body=submitted,
            workstream_id=ws,
        )
        reordered = await call(
            "reorder_tasks",
            project=project,
            task_id=proposed["proposal"]["id"],
            anchor_id=task["id"],
            position="before",
            expected_order_revision=proposed["project_order_revision"],
            instruction="Synthetic request to schedule the prerequisite first",
        )
        assert reordered["changed"]
        assert reordered["project_order_revision"] == proposed["project_order_revision"] + 1
        duplicate = await call(
            "add_prerequisite",
            task_id=task["id"],
            expected_revision=proposed["task"]["revision"],
            blocked_by_id=proposed["proposal"]["id"],
        )
        assert not duplicate["changed"] and duplicate["revision"] == proposed["task"]["revision"]
        observer = await call(
            "add_unresolved",
            task_id=task["id"],
            expected_revision=duplicate["revision"],
            text=submitted,
            handling="observer",
        )
        activated = await call(
            "accept_gate_proposal",
            proposal_id=observer["proposal_id"],
            expected_revision=observer["task_revision"],
        )
        resolved = await call(
            "resolve_unresolved",
            task_id=task["id"],
            expected_revision=activated["revision"],
            item_id=activated["unresolved_id"],
            user_note="Settled",
        )
        summary = await call(
            "update_task",
            task_id=task["id"],
            expected_revision=resolved["revision"],
            changes={"summary": "Retain approved delivery constraints."},
        )
        accepted = await call(
            "queue_task",
            task_id=task["id"],
            workstream_id=ws,
            expected_revision=summary["revision"],
        )
        same = await call(
            "queue_task",
            task_id=task["id"],
            workstream_id=ws,
            expected_revision=accepted["revision"],
        )
        assert not same["changed"]
        withdrawn = await call(
            "unqueue_task",
            task_id=task["id"],
            expected_revision=same["revision"],
        )
        deferred = await call(
            "set_disposition",
            task_id=task["id"],
            expected_revision=withdrawn["revision"],
            disposition="deferred",
            note="Pause",
        )
        same = await call(
            "set_disposition",
            task_id=task["id"],
            expected_revision=deferred["revision"],
            disposition="deferred",
            note="Still paused",
        )
        assert not same["changed"]
        result = await call(
            "record_result",
            task_id=task["id"],
            workstream_id=ws,
            expected_revision=same["revision"],
            implementer="worker",
            summary="Factual durable result",
            evidence=submitted,
            artifacts=[{"kind": "artifact", "reference": "proof"}],
            verification=submitted,
            specification_etag=task["specification_etag"],
        )
        reviewed = await call(
            "human_review",
            attempt_id=result["attempt_id"],
            expected_revision=result["attempt_revision"],
            user_note="Synthetic explicit actual review",
        )
        assert (
            reviewed["attempt_revision"] == 2
            and reviewed["task_revision"] == result["task_revision"]
        )
        parent = await call(
            "create_task",
            project=project,
            title="Split",
            body=submitted,
            workstream_id=ws,
        )
        split = await call(
            "decompose_task",
            task_id=parent["id"],
            expected_revision=parent["revision"],
            members=[{"title": "Child", "body": submitted}],
        )
        assert split["members"][0]["revision"] == 1 and "body" not in split["members"][0]
        moved = await call(
            "reorder_tasks",
            project=project,
            task_id=proposed["proposal"]["id"],
            anchor_id=task["id"],
            position="after",
            expected_order_revision=split["project_order_revision"],
            instruction="Synthetic requested order",
        )
        assert moved["changed"]

    asyncio.run(exercise())


def test_summary_only_correction_after_group_completion_preserves_members(context):
    store, project, ws = context
    group = store.create_group(ws, "Group", summary="Original group intent")
    member = store.create_task(project, "Member", group_id=group["id"], group_expected_revision=1)
    current_ws = store.workstream_status(ws)["workstream"]["revision"]
    store.set_scope(ws, current_ws, f"none +{group['id']}")
    accepted = store.get_tasks([member["id"]])["items"][0]
    attempt = store.record_result(
        member["id"],
        ws,
        accepted["revision"],
        "worker",
        "Built",
        "Proof",
        [{"kind": "artifact", "reference": "proof"}],
        "Checked",
        member["specification_etag"],
    )
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Checked")
    store.signoff_task(
        member["id"], attempt["task_revision"], "approve", "Synthetic verdict", attempt["id"], 2
    )
    before = store.get_tasks([group["id"]])["items"][0]
    ack = store.update_task(group["id"], before["revision"], {"summary": "Correct group intent"})
    after = store.get_tasks([group["id"]])["items"][0]
    assert (
        ack["changed"] and after["complete"] and after["spec_revision"] == before["spec_revision"]
    )
    assert after["members"] == before["members"] and after["body"] == before["body"]
    with pytest.raises(TaskError, match="completed_task_immutable"):
        store.update_task(
            group["id"],
            ack["revision"],
            {"body": "New scope"},
            specification_etag=before["specification_etag"],
        )
