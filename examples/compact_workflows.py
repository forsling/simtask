"""Measure the public compact workflows over real MCP stdio on a disposable DB."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def exercise(database):
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "task_mcp", "--db", str(database), "--actor", "synthetic-compact-trace"],
        env={"PYTHONPATH": str(root / "src"), "TASK_MCP_DB": str(database)},
    )
    trace = []
    report = {
        "boundary": (
            "Real MCP stdio, disposable synthetic data; no production migration or human verdict."
        ),
        "calls": trace,
    }
    async with Client(params, read_timeout_seconds=60) as client:
        catalog = await client.list_tools()
        report["catalog"] = {
            "tools": len(catalog.tools),
            "raw_tools_list_bytes": len(catalog.model_dump_json().encode()),
            "description_characters": sum(len(t.description or "") for t in catalog.tools),
            "instructions_characters": len(client.instructions or ""),
            "host_repeated_instructions_estimate_characters": sum(
                len(t.description or "") for t in catalog.tools
            )
            + len(catalog.tools) * len(client.instructions or ""),
            "host_estimate_limit": (
                "Estimate: descriptions plus one instructions copy per tool; "
                "excludes host wrapper/schema text. Refreshed host verification awaits rollout."
            ),
        }
        assert len(catalog.tools) == 26

        async def call(tool_name, **args):
            result = await client.call_tool(tool_name, args)
            trace.append(
                {
                    "tool": tool_name,
                    "structured_bytes": len(
                        json.dumps(result.structured_content, ensure_ascii=False).encode()
                    ),
                    "wire_result_bytes": len(result.model_dump_json().encode()),
                    "error": result.is_error,
                }
            )
            return result

        async def ok(tool_name, **args):
            result = await call(tool_name, **args)
            assert not result.is_error, result.content
            return result.structured_content

        setup = await ok(
            "init",
            path=str(database.parent / "repo"),
            branch="main",
            action="create_project",
            confirmed=True,
        )
        project, ws = setup["project"]["id"], setup["workstream"]["id"]
        other_setup = await ok(
            "init",
            path=str(database.parent / "repo"),
            branch="other",
            action="new_workstream",
            confirmed=True,
        )
        other = other_setup["workstream"]["id"]
        body, criteria = (
            "Complete current requirement.\n" * 2000,
            "Every required behavior verified.\n" * 500,
        )
        proof = "Durable historical evidence and verification detail.\n" * 1000
        task = await ok(
            "create_task",
            project=project,
            title="Keep ordinary calls compact",
            body=body,
            acceptance_criteria=criteria,
            summary="Bound ordinary context while preserving full requirements and chosen proof.",
            workstream_id=ws,
        )
        token, revision = task["specification_etag"], task["revision"]
        zero_history = (await ok("get_tasks", ids=[task["id"]], workstream_id=ws))["items"][0]
        old_ids = []
        for i in range(8):
            old = await ok(
                "record_result",
                task_id=task["id"],
                workstream_id=ws if i % 2 else other,
                expected_revision=revision,
                implementer=f"historical worker {i}",
                summary="Historical alternative",
                evidence=proof,
                artifacts=[{"kind": "artifact", "reference": f"synthetic/old-{i}.txt"}],
                verification="Synthetic old checks",
                specification_etag=token,
            )
            revision = old["task_revision"]
            old_ids.append(old["attempt_id"])
        changed = await ok(
            "update_task",
            task_id=task["id"],
            expected_revision=revision,
            changes={"body": body + "Current additional requirement.\n"},
            specification_etag=token,
        )
        token, revision = changed["specification_etag"], changed["revision"]
        card_before = (await ok("get_tasks", ids=[task["id"]], workstream_id=ws))["items"][0]
        assert "specification_etag" not in card_before
        attempt_before = (
            await ok("get_tasks", ids=[task["id"]], workstream_id=ws, include=["attempt"])
        )["items"][0]
        assert attempt_before["attempt"] is None
        spec = (
            await ok(
                "get_tasks",
                ids=[task["id"]],
                specification=True,
                workstream_id=ws,
                attempt_ids=[old_ids[0]],
            )
        )["items"][0]
        assert spec["body"] == body + "Current additional requirement.\n"
        assert (
            spec["attempts"][0]["spec_revision"] == 1
            and spec["attempts"][0]["workstream_id"] == other
        )
        noop = await ok(
            "update_task",
            task_id=task["id"],
            expected_revision=revision,
            changes={"title": task.get("title", "Keep ordinary calls compact")},
        )
        assert (
            not noop["changed"]
            and noop["revision"] == revision
            and "specification_etag" not in noop
        )
        stale = await call(
            "update_task",
            task_id=task["id"],
            expected_revision=1,
            changes={"body": "Unsafe replacement"},
            specification_etag=token,
        )
        assert stale.is_error and "specification=true" in str(stale.content)
        # Current remote proof does not block local autonomous implementation.
        await ok(
            "record_result",
            task_id=task["id"],
            workstream_id=other,
            expected_revision=revision,
            implementer="remote worker",
            summary="Remote alternative",
            evidence=proof,
            artifacts=[{"kind": "artifact", "reference": "synthetic/remote.txt"}],
            verification="Remote checks",
            specification_etag=token,
        )
        start = len(trace)
        assignment = await ok("get_next_action", workstream_id=ws)
        assert assignment["action"] == "implement" and assignment["attempt"] is None
        built = await ok(
            "record_result",
            task_id=task["id"],
            workstream_id=ws,
            expected_revision=assignment["task"]["revision"],
            implementer="local worker",
            summary="Built bounded cards and complete chosen proof",
            evidence=proof,
            artifacts=[{"kind": "artifact", "reference": "synthetic/local.txt"}],
            verification="Synthetic durable verification",
            specification_etag=assignment["task"]["specification_etag"],
        )
        assignment = await ok("get_next_action", workstream_id=ws)
        assert (
            assignment["action"] == "review" and assignment["attempt"]["id"] == built["attempt_id"]
        )
        assert assignment["attempt"]["evidence"] == proof
        await ok(
            "record_review",
            attempt_id=built["attempt_id"],
            expected_revision=assignment["attempt"]["revision"],
            reviewer="fresh independent reviewer",
            verdict="pass",
            note="Synthetic independent review",
        )
        report["implementation_result_review"] = {
            "call_count": len(trace) - start,
            "calls": trace[start:],
            "complete_reads": 2,
            "confirmation_reads": 0,
        }
        assert len(trace) - start == 4
        # Additional current local/remote alternatives cannot expand ordinary proof.
        revision = built["task_revision"]
        for i in range(6):
            alternative = await ok(
                "record_result",
                task_id=task["id"],
                workstream_id=ws if i % 2 else other,
                expected_revision=revision,
                implementer=f"alternative worker {i}",
                summary="Current alternative",
                evidence=proof,
                artifacts=[{"kind": "artifact", "reference": f"synthetic/alternative-{i}"}],
                verification="Actual synthetic checks",
                specification_etag=token,
            )
            revision = alternative["task_revision"]
        # Growing old history has no default proof expansion.
        card_after = (await ok("get_tasks", ids=[task["id"]], workstream_id=ws))["items"][0]
        report["representative_cards"] = {
            "default": (await ok("get_tasks", ids=[task["id"]]))["items"][0],
            "scoped": card_after,
        }
        report["card_bytes"] = {
            "zero_history": len(json.dumps(zero_history).encode()),
            "eight_superseded_attempts": len(json.dumps(card_before).encode()),
            "same_history_with_current_local_reference": len(json.dumps(card_after).encode()),
        }
        assert abs(len(json.dumps(zero_history)) - len(json.dumps(card_before))) < 10
        assert len(json.dumps(card_after)) < 1500 and proof not in json.dumps(card_after)
        start = len(trace)
        queue = await ok(
            "list_tasks", project=project, workstream_id=ws, state="signoff", include=["attempt"]
        )
        ref = queue["items"][0]["attempt"]
        informed = (
            await ok(
                "get_tasks",
                ids=[task["id"]],
                specification=True,
                workstream_id=ws,
                attempt_ids=[ref["attempt_id"]],
            )
        )["items"][0]
        assert (
            len(informed["attempts"]) == 1
            and informed["attempts"][0]["review_note"] == "Synthetic independent review"
        )
        report["synthetic_signoff_presentation"] = {
            "Asked": informed["body"],
            "Built": informed["attempts"][0]["summary"],
            "verification": informed["attempts"][0]["verification"],
            "independent_review": informed["attempts"][0]["review_note"],
            "inspection_handle": informed["attempts"][0]["artifacts"],
            "judgment_question": "Does this fulfill the approved goal with acceptable quality?",
            "limitations": "Synthetic demonstration only.",
            "recommendation": "Approve the synthetic result in this isolated demonstration.",
        }
        signed = await ok(
            "signoff_task",
            task_id=task["id"],
            expected_revision=informed["revision"],
            attempt_id=ref["attempt_id"],
            expected_attempt_revision=ref["attempt_revision"],
            decision="approve",
            reasons="Synthetic informed verdict, never a real user decision",
        )
        assert signed["status"] == "done" and signed["attempt_state"] == "passed"
        assert signed["decision"] == "approve"
        assert not {"purpose_judgment", "result_judgment", "reasons"} & signed.keys()
        report["signoff_acknowledgements"] = [signed]
        report["queue_informed_signoff_verdict"] = {
            "call_count": len(trace) - start,
            "calls": trace[start:],
            "complete_reads": 1,
            "confirmation_reads": 0,
        }
        assert len(trace) - start == 3
        group = await ok(
            "create_task",
            project=project,
            kind="group",
            workstream_id=ws,
            title="Many members",
            body="Full group context",
            summary="Shared member intent",
        )
        group_revision = group["revision"]
        for i in range(24):
            member = await ok(
                "create_task",
                project=project,
                title=f"Member {i}",
                group_id=group["id"],
                group_expected_revision=group_revision,
                workstream_id=ws,
            )
            group_revision = member["group_revision"]
        group_card = (await ok("get_tasks", ids=[group["id"]]))["items"][0]
        group_spec = (await ok("get_tasks", ids=[group["id"]], specification=True))["items"][0]
        assert (
            "members" not in group_card
            and len(group_spec["members"]) == 3
            and group_spec["member_total"] == 24
        )
        page = await ok("list_tasks", group_id=group["id"])
        rest = await ok("list_tasks", group_id=group["id"], offset=page["next_offset"])
        assert len(page["items"]) == 20 and len(rest["items"]) == 4
        init = await ok("init", path=str(database.parent / "repo"), branch="main")
        # The signed-off task is counted, not listed.
        assert len(init["queue"]) == 10 and init["queue_total"] == 24
        assert init["queue_hidden"] == {"done": 1}
        history = await ok(
            "list_task_attempts", task_id=task["id"], current_spec_only=False, limit=3
        )
        assert history["total"] == 16 and history["has_more"]
        exact = await ok("get_attempt", attempt_id=old_ids[0])
        assert exact["evidence"] == proof
        events = await ok("list_events")
        assert len(events["items"]) == 20 and "request" not in events["items"][0]
        index = await ok("get_default_skills")
        assert all("content" not in s for s in index["items"])
        named = await ok("get_default_skills", name="signoff")
        metadata = next(item for item in index["items"] if item["name"] == "signoff")
        assert named["items"][0]["sha256"] == metadata["sha256"]
        assert named["items"][0]["content"]
        report["fixture"] = {
            "specification_characters": len(body) + len(criteria),
            "proof_characters_per_attempt": len(proof),
            "task_attempts": 16,
            "members": 24,
        }
        # Every verdict returns actual state without repeating reasons.
        for decision in ("rework", "revise", "drop"):
            judged_task = await ok(
                "create_task",
                project=project,
                title=f"Synthetic {decision}",
                body=body,
                workstream_id=ws,
            )
            result = await ok(
                "record_result",
                task_id=judged_task["id"],
                workstream_id=ws,
                expected_revision=judged_task["revision"],
                implementer="synthetic worker",
                summary="Synthetic durable result",
                evidence=proof,
                artifacts=[{"kind": "artifact", "reference": "synthetic/signoff-proof"}],
                verification="Synthetic actual checks",
                specification_etag=judged_task["specification_etag"],
            )
            reviewed = await ok(
                "record_review",
                attempt_id=result["attempt_id"],
                expected_revision=result["attempt_revision"],
                reviewer="synthetic fresh reviewer",
                verdict="pass",
                note=proof,
            )
            ack = await ok(
                "signoff_task",
                task_id=judged_task["id"],
                expected_revision=reviewed["task_revision"],
                attempt_id=result["attempt_id"],
                expected_attempt_revision=reviewed["attempt_revision"],
                decision=decision,
                reasons=proof,
            )
            assert ack["decision"] == decision
            assert ack["attempt_state"] == ("rework" if decision == "rework" else "passed")
            assert ack["attempt_revision"] == 2 + (decision == "rework")
            assert bool(ack["unresolved_id"]) == (decision == "revise")
            assert proof not in json.dumps(ack) and body not in json.dumps(ack)
            report["signoff_acknowledgements"].append(ack)
        # A new prerequisite returns order continuation for a direct move.
        start = len(trace)
        created = await ok(
            "create_task",
            project=project,
            title="Synthetic prerequisite",
            body=body,
            workstream_id=ws,
        )
        linked = await ok(
            "add_prerequisite",
            task_id=member["id"],
            expected_revision=member["revision"],
            blocked_by_id=created["id"],
        )
        moved = await ok(
            "reorder_tasks",
            workstream_id=ws,
            task_ids=[created["id"]],
            expected_order_revision=created["workstream_order_revision"],
        )
        assert (
            moved["changed"]
            and moved["workstream_order_revision"] == created["workstream_order_revision"] + 1
        )
        report["prerequisite_order_continuation"] = {
            "call_count": len(trace) - start,
            "calls": trace[start:],
            "confirmation_reads": 0,
            "prerequisite": created,
            "link": linked,
            "move": moved,
        }
        report["proof_write_sizes"] = [
            row
            for row in trace
            if row["tool"] in ("record_result", "record_review", "signoff_task")
        ]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="task-mcp-compact-") as directory:
        report = asyncio.run(exercise(Path(directory) / "tasks.sqlite3"))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
