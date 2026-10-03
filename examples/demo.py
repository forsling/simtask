"""Exercise the real stdio MCP interface against a disposable explicit database."""

import asyncio
import hashlib
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def exercise(database: Path):
    root = Path(__file__).resolve().parents[1]
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "task_mcp", "--db", str(database), "--actor", "demo-coordinator"],
        env={"PYTHONPATH": str(root / "src"), "TASK_MCP_DB": str(database)},
    )
    async with Client(server, read_timeout_seconds=60) as client:
        assert client.instructions
        tools = (await client.list_tools()).tools
        assert len(tools) == 42
        assert "runtime_info" in {tool.name for tool in tools}
        assert "open_task_viewer" in {tool.name for tool in tools}
        assert "dismiss_gate_proposal" in {tool.name for tool in tools}
        assert {"init", "workstream_status", "create_group", "list_groups", "add_group_member"} <= {
            tool.name for tool in tools
        }
        signoff_schema = next(tool.input_schema for tool in tools if tool.name == "signoff_task")
        assert signoff_schema["properties"]["decision"]["enum"] == [
            "approve",
            "rework",
            "revise",
            "drop",
            "defer",
        ]
        assert "expected_attempt_revision" in signoff_schema["required"]
        assert not {"verdict", "rejection"} & signoff_schema["properties"].keys()
        for name in ("add_prerequisite", "propose_prerequisite"):
            descriptor = next(tool for tool in tools if tool.name == name)
            milestone = descriptor.input_schema["properties"]["milestone"]
            assert milestone["default"] == "review"
            assert milestone["enum"] == ["review", "signoff"]
            assert "exceptionally" in descriptor.description
        move_schema = next(tool.input_schema for tool in tools if tool.name == "reorder_tasks")
        assert move_schema["properties"]["position"]["enum"] == ["before", "after"]
        assert {"task_id", "anchor_id", "expected_order_revision", "instruction"} <= set(
            move_schema["required"]
        )
        assert not {"ordered_ids", "expected_order"} & move_schema["properties"].keys()
        print(f"Connected over stdio; discovered {len(tools)} tools.")

        async def call(tool_name, **arguments):
            result = await client.call_tool(tool_name, arguments)
            assert not result.is_error, result.content
            return result.structured_content

        project_path = "/tmp/task-mcp-demo-project"
        discovered = await call("init", path=project_path, branch="main")
        assert discovered["state"] == "unregistered_checkout"
        setup = await call(
            "init", path=project_path, branch="main", action="create_project", confirmed=True
        )
        project_id = setup["project"]["id"]
        workstream_id = setup["workstream"]["id"]
        task = await call(
            "create_task",
            project=project_id,
            title="Demonstrate reviewed delivery",
            body="Keep the useful behavior.",
            acceptance_criteria="The demonstration passes.",
            source="user",
            user_request="Synthetic demo request",
            workstream_id=workstream_id,
        )
        task_id = task["id"]
        resumed = await call("init", path=project_path, branch="main")
        assert resumed["state"] == "ready" and resumed["queue"][0]["id"] == task_id
        overview = await call("list_workstreams")
        assert overview["items"][0]["id"] == workstream_id
        status = await call("workstream_status", workstream_id=workstream_id)
        assert status["items"][0]["id"] == task_id
        selected = await call("get_next_action", workstream_id=workstream_id)
        assert selected["action"] == "implement" and selected["task"]["id"] == task_id
        assert "attempts" not in selected["task"]
        stale = await client.call_tool(
            "update_task",
            {
                "task_id": task_id,
                "expected_revision": 0,
                "changes": {"title": "Stale"},
            },
        )
        assert stale.is_error and "revision_conflict" in str(stale.content)
        print("Rejected an update from an outdated revision.")
        proposal = await call(
            "add_unresolved",
            task_id=task_id,
            expected_revision=1,
            text="Synthetic observer concern",
            handling="observer",
        )
        dismissed = await call(
            "dismiss_gate_proposal",
            proposal_id=proposal["proposal_id"],
            expected_revision=1,
            note="Synthetic coordinator decision: concern does not apply",
        )
        assert "gate_proposals" not in dismissed and dismissed["revision"] == 2
        result = await call(
            "record_result",
            task_id=task_id,
            workstream_id=workstream_id,
            expected_revision=2,
            implementer="demo-implementer",
            summary="Demo delivered",
            evidence="Synthetic demonstration passed.",
            artifacts=[{"kind": "artifact", "reference": "examples/demo.py"}],
            verification="Synthetic demonstration checks",
            specification_etag=(await call("get_tasks", specification=True, ids=[task_id]))[
                "items"
            ][0]["specification_etag"],
        )
        assert not {"evidence", "summary", "artifacts", "verification"} & result.keys()
        resumed_action = await call("get_next_action", workstream_id=workstream_id)
        assert resumed_action["action"] == "review"
        assert resumed_action["attempt"]["id"] == result["id"]
        assert resumed_action["attempt"]["artifacts"] == [
            {"kind": "artifact", "reference": "examples/demo.py"}
        ]
        assert resumed_action["attempt"]["verification"] == "Synthetic demonstration checks"
        assert "attempts" not in resumed_action["task"]
        reviewed = await call(
            "record_review",
            attempt_id=result["id"],
            expected_revision=1,
            reviewer="demo-reviewer",
            verdict="pass",
            note="Independent demo review passed.",
        )
        assert reviewed["state"] == "passed"
        waiting = await call("get_next_action", workstream_id=workstream_id)
        assert waiting["action"] is None and waiting["diagnostics"]["signoff"] == 1
        current = (await call("get_tasks", specification=True, ids=[task_id]))["items"][0]
        signed = await call(
            "signoff_task",
            task_id=task_id,
            expected_revision=current["revision"],
            attempt_id=result["id"],
            decision="approve",
            expected_attempt_revision=reviewed["revision"],
            user_note="Synthetic demo approval, not a real user verdict.",
        )
        assert signed["status"] == "done"
        signed_full = (await call("get_tasks", specification=True, ids=[task_id]))["items"][0]
        assert signed_full["selected_attempt_id"] == result["id"]
        prioritized = await call(
            "create_task",
            project=project_id,
            title="Prioritized remaining work",
            workstream_id=workstream_id,
        )
        board = await call("list_tasks", project=project_id)
        move_args = dict(
            project=project_id,
            task_id=prioritized["id"],
            anchor_id=task_id,
            position="before",
            expected_order_revision=board["project_order_revision"],
            instruction="Synthetic user scheduling decision",
        )
        moved = await call("reorder_tasks", **move_args)
        assert moved["changed"] and not {"ordered_ids", "items"} & moved.keys()
        stale_move = await client.call_tool("reorder_tasks", move_args)
        assert stale_move.is_error and "revision_conflict" in str(stale_move.content)
        move_args["expected_order_revision"] = moved["project_order_revision"]
        assert not (await call("reorder_tasks", **move_args))["changed"]
        selected = await call("get_next_action", workstream_id=workstream_id)
        assert selected["task"]["id"] == prioritized["id"]
        done_after = (await call("get_tasks", specification=True, ids=[task_id]))["items"][0]
        assert {k: v for k, v in signed_full.items() if k != "order_key"} == {
            k: v for k, v in done_after.items() if k != "order_key"
        }
        print("Atomic move preserved completed proof and selected remaining work.")
        for decision in ("rework", "revise", "drop", "defer"):
            item = await call(
                "create_task",
                project=project_id,
                title=f"Signoff {decision}",
                workstream_id=workstream_id,
            )
            built = await call(
                "record_result",
                task_id=item["id"],
                workstream_id=workstream_id,
                expected_revision=1,
                implementer="builder",
                summary="Synthetic result",
                evidence="Synthetic proof",
                artifacts=[{"kind": "artifact", "reference": "examples/demo.py"}],
                verification="Synthetic demonstration checks",
                specification_etag=(await call("get_tasks", specification=True, ids=[item["id"]]))[
                    "items"
                ][0]["specification_etag"],
            )
            await call(
                "record_review",
                attempt_id=built["id"],
                expected_revision=1,
                reviewer="separate reviewer",
                verdict="pass",
                note="Synthetic independent review",
            )
            extras = (
                {"specification_question": "Which actual scope should replace this?"}
                if decision == "revise"
                else {}
            )
            judged = await call(
                "signoff_task",
                task_id=item["id"],
                expected_revision=2,
                attempt_id=built["id"],
                expected_attempt_revision=2,
                decision=decision,
                user_note="Synthetic actual user decision",
                **extras,
            )
            details = (await call("get_tasks", specification=True, ids=[item["id"]]))["items"][0]
            events = await call("list_events", task_id=item["id"], include_details=True, limit=100)
            event = next(e for e in events["items"] if e["sequence"] == judged["decision_ref"])
            record = {"decision_ref": event["sequence"], **event["after"]["signoff_decision"]}
            assert record["decision_ref"] == judged["decision_ref"]
            assert record["result_judgment"] == ("rework" if decision == "rework" else "not_judged")
            assert details["spec_revision"] == 1
            if decision == "drop":
                rejected = await client.call_tool(
                    "set_disposition",
                    {
                        "task_id": item["id"],
                        "expected_revision": judged["revision"],
                        "disposition": "open",
                        "note": "Restore",
                    },
                )
                assert rejected.is_error and "revival_authorization_required" in str(
                    rejected.content
                )
                revived = await call(
                    "set_disposition",
                    task_id=item["id"],
                    expected_revision=judged["revision"],
                    disposition="open",
                    note="Restore",
                    authorization="Synthetic actual revival instruction",
                )
                assert revived["queue_workstream_id"] == workstream_id
        # User origin preserves the request without approving an exploratory spec.
        draft = await call(
            "create_task",
            project=project_id,
            title="Explore a design later",
            source="user",
            user_request="Synthetic design-first request; leave pending",
        )
        assert (
            draft["queue_workstream_id"] is None and "body" not in draft and "attempts" not in draft
        )
        details = (await call("get_tasks", specification=True, ids=[draft["id"]]))["items"][0]
        assert details["source"] == "user" and details["user_request"]
        accepted = await call(
            "queue_task",
            task_id=draft["id"],
            expected_revision=draft["revision"],
            workstream_id=workstream_id,
        )
        assert accepted["queue_workstream_id"] == workstream_id
        withdrawn = await call(
            "unqueue_task",
            task_id=draft["id"],
            expected_revision=accepted["revision"],
        )
        assert (
            withdrawn["queue_workstream_id"] is None
            and withdrawn["spec_revision"] == accepted["spec_revision"]
        )
        assert "inbox" in withdrawn["gate_diagnostics"]
        approved_again = await call(
            "queue_task",
            task_id=draft["id"],
            expected_revision=withdrawn["revision"],
            workstream_id=workstream_id,
        )
        assert approved_again["revision"] == withdrawn["revision"] + 1
        full_spec = (await call("get_tasks", specification=True, ids=[draft["id"]]))["items"][0]
        unsafe = await client.call_tool(
            "update_task",
            {
                "task_id": draft["id"],
                "expected_revision": full_spec["revision"],
                "changes": {"body": "A body preview cannot safely replace the spec"},
            },
        )
        assert unsafe.is_error and "specification_read_required" in str(unsafe.content)
        approved_again = await call(
            "update_task",
            task_id=draft["id"],
            expected_revision=full_spec["revision"],
            changes={"body": "Synthetic complete amended specification"},
            specification_etag=full_spec["specification_etag"],
        )
        assert approved_again["spec_revision"] == 2
        assert (
            approved_again["queue_workstream_id"] == workstream_id
            and approved_again["spec_changed"]
        )
        assert "body" not in approved_again and "attempts" not in approved_again
        obsolete = await client.call_tool(
            "queue_task",
            {
                "task_id": draft["id"],
                "expected_revision": approved_again["revision"],
                "user_note": "Obsolete unclassified approval",
            },
        )
        assert obsolete.is_error
        second = await call(
            "init",
            path="/tmp/task-mcp-demo-service-b",
            branch="feature",
            action="create_project",
            confirmed=True,
        )
        third = await call(
            "init",
            path="/tmp/task-mcp-demo-service-c",
            branch="release",
            action="create_project",
            confirmed=True,
        )
        group = await call(
            "create_group", workstream_id=workstream_id, title="Shared three-repository feature"
        )
        assert group["project_id"] is None and not group["complete"]
        for context in (second, third):
            await call(
                "set_scope",
                workstream_id=context["workstream"]["id"],
                expected_revision=1,
                expression=f"none +{group['id']}",
            )
        members = []
        for context, title in ((setup, "Service A"), (second, "Service B"), (third, "Service C")):
            current_group = (await call("get_tasks", specification=True, ids=[group["id"]]))[
                "items"
            ][0]
            member = await call(
                "create_task",
                project=context["project"]["id"],
                title=title,
                source="user",
                user_request="Synthetic group demo",
                group_id=group["id"],
                group_expected_revision=current_group["revision"],
                workstream_id=context["workstream"]["id"],
            )
            members.append(member)
        detail = (await call("get_tasks", specification=True, ids=[group["id"]]))["items"][0]
        assert detail["progress"]["total"] == 3 and not detail["complete"]
        for context, member in zip((setup, second, third), members, strict=True):
            status = await call("workstream_status", workstream_id=context["workstream"]["id"])
            assert member["id"] in {item["id"] for item in status["items"]}
            foreign = {item["id"] for item in members} - {member["id"]}
            assert not foreign & {item["id"] for item in status["items"]}
            groups = await call("list_groups", project=context["project"]["id"])
            assert groups["items"][0]["id"] == group["id"]
        second_export = await call("export_workstream", workstream_id=second["workstream"]["id"])
        assert members[1]["id"] in second_export["content"]
        assert members[2]["id"] not in second_export["content"]
        # Direct remote blockers do not need shared-group membership.
        remote_blocker = await call(
            "create_task",
            project=second["project"]["id"],
            title="Independent remote blocker",
            body="Remote full requirements",
            workstream_id=second["workstream"]["id"],
        )
        dependent = await call(
            "create_task",
            project=project_id,
            title="Wait for remote completion",
            workstream_id=workstream_id,
        )
        linked = await call(
            "add_prerequisite",
            task_id=dependent["id"],
            expected_revision=1,
            blocked_by_id=remote_blocker["id"],
        )
        assert linked["blocked_by_id"] == remote_blocker["id"]
        linked_full = (await call("get_tasks", ids=[dependent["id"]], specification=True))["items"][
            0
        ]
        reference = linked_full["prerequisites"][0]
        assert reference["project_id"] == second["project"]["id"] and reference["blocking"]
        assert reference["project_name"] == second["project"]["name"]
        assert (
            linked["queue_workstream_id"] == workstream_id
            and linked["spec_revision"] == 1
            and linked["revision"] == 2
        )
        assert "attempts" not in linked and "Remote full requirements" not in str(linked)
        proposal = await call(
            "add_prerequisite",
            task_id=dependent["id"],
            expected_revision=2,
            blocked_by_id=members[2]["id"],
            handling="observer",
        )
        accepted_gate = await call(
            "accept_gate_proposal",
            proposal_id=proposal["proposal_id"],
            expected_revision=2,
        )
        assert accepted_gate["revision"] == 3 and accepted_gate["changed"]
        accepted_full = (await call("get_tasks", ids=[dependent["id"]], specification=True))[
            "items"
        ][0]
        assert len(accepted_full["prerequisites"]) == 2
        cycle = await client.call_tool(
            "add_prerequisite",
            {
                "task_id": remote_blocker["id"],
                "expected_revision": 1,
                "blocked_by_id": dependent["id"],
            },
        )
        assert cycle.is_error and "prerequisite_cycle" in str(cycle.content)
        remote_attempt = await call(
            "record_result",
            task_id=remote_blocker["id"],
            expected_revision=1,
            workstream_id=second["workstream"]["id"],
            implementer="demo remote builder",
            summary="Remote result",
            evidence="Remote evidence deliberately retrieved only",
            artifacts=[{"kind": "artifact", "reference": "examples/demo.py"}],
            verification="Synthetic demonstration checks",
            specification_etag=(
                await call("get_tasks", specification=True, ids=[remote_blocker["id"]])
            )["items"][0]["specification_etag"],
        )
        await call(
            "record_review",
            attempt_id=remote_attempt["id"],
            expected_revision=1,
            reviewer="demo independent reviewer",
            verdict="pass",
            note="Synthetic review",
        )
        pending = (await call("get_tasks", specification=True, ids=[dependent["id"]]))["items"][0]
        reviewed_ref = next(p for p in pending["prerequisites"] if p["id"] == remote_blocker["id"])
        assert reviewed_ref["milestone"] == "review" and reviewed_ref["satisfied"]
        assert not reviewed_ref["blocking"] and not reviewed_ref["complete"]
        await call(
            "signoff_task",
            task_id=remote_blocker["id"],
            expected_revision=2,
            decision="approve",
            user_note="Synthetic informed human verdict",
            attempt_id=remote_attempt["id"],
            expected_attempt_revision=2,
        )
        queue = await call("list_tasks", project=project_id, workstream_id=workstream_id)
        dependent_row = next(row for row in queue["items"] if row["id"] == dependent["id"])
        completed_ref = next(
            p for p in dependent_row["prerequisites"] if p["id"] == remote_blocker["id"]
        )
        assert completed_ref["complete"] and not completed_ref["blocking"]
        assert remote_blocker["id"] not in {row["id"] for row in queue["items"]}
        exported = await call("export_workstream", workstream_id=workstream_id)
        assert exported["format"] == "task-mcp/v4" and task_id in exported["content"]
        assert "- Workflow: Done (done)" in exported["content"]
        assert "```json" not in exported["content"]
        open_export = await call(
            "export_workstream", workstream_id=workstream_id, include_closed=False
        )
        assert task_id not in open_export["content"]
        legacy = await call("export_workstream", workstream_id=workstream_id, format="legacy")
        assert legacy["format"] == "task-mcp/v1" and "```json" in legacy["content"]
        invalid = await client.call_tool(
            "export_workstream", {"workstream_id": workstream_id, "format": "unsupported"}
        )
        assert invalid.is_error
        catalog = await call("get_default_skills")
        assert catalog["version"] == "1.14.0"
        assert {item["name"] for item in catalog["items"]} == {
            "init",
            "feature-capture",
            "feature-design",
            "proposal-review",
            "superdevloop",
            "signoff",
        }
        for item in catalog["items"]:
            canonical = (root / "src/task_mcp/reference_skills" / item["path"]).read_bytes()
            named = await call("get_default_skills", name=item["name"])
            assert named["items"][0]["content"].encode() == canonical
            assert "content" not in item
            assert item["sha256"] == hashlib.sha256(canonical).hexdigest()
            assert item["version"] == catalog["version"]
    async with Client(server, read_timeout_seconds=60) as restarted:
        result = await restarted.call_tool("get_tasks", {"ids": [task_id]})
        assert not result.is_error
        assert result.structured_content["items"][0]["status"] == "done"
    print("Restarted the server and verified persistence. Demo passed.")


def main():
    with TemporaryDirectory(prefix="task-mcp-demo-") as temporary:
        asyncio.run(exercise(Path(temporary) / "tasks.sqlite3"))


if __name__ == "__main__":
    main()
