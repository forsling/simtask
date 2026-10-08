"""Fresh SDK catalog and concurrent workstream membership on disposable state."""

import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


def test_fresh_sdk_shared_membership_and_local_attempt_selection(tmp_path):
    database = tmp_path / "memberships.sqlite3"
    root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "simtask", "--db", str(database)],
        env={"PYTHONPATH": str(root / "src"), "SIMTASK_DB": str(database)},
    )

    async def exercise():
        async with Client(parameters, read_timeout_seconds=30) as client:
            catalog = {t.name: t for t in (await client.list_tools()).tools}
            assert {"add_to_workstream", "remove_from_workstream"} <= catalog.keys()
            assert (
                not {"queue_task", "unqueue_task", "accept_task", "withdraw_acceptance"}
                & catalog.keys()
            )
            for name in ("add_to_workstream", "remove_from_workstream"):
                assert "workstream_id" in catalog[name].input_schema["required"]
                assert "note" not in catalog[name].input_schema["properties"]

            async def call(name, **arguments):
                response = await client.call_tool(name, arguments)
                assert not response.is_error, response.content
                return response.structured_content

            ctx = await call(
                "init",
                path=str(tmp_path / "repo"),
                branch="a",
                action="create_project",
                confirmed=True,
            )
            project, a = ctx["project"]["id"], ctx["workstream"]["id"]
            ctx_b = await call(
                "init",
                path=str(tmp_path / "repo"),
                branch="b",
                action="new_workstream",
                confirmed=True,
            )
            b = ctx_b["workstream"]["id"]
            task = await call("create_task", project=project, title="Shared task", workstream_id=a)
            added = await call(
                "add_to_workstream",
                task_id=task["id"],
                workstream_id=b,
                expected_revision=task["revision"],
            )
            assert set(added["workstream_ids"]) == {a, b} and added["adopted"]
            for ws in (a, b):
                board = await call("list_tasks", project=project, workstream_id=ws)
                assert board["items"][0]["id"] == task["id"]
            attempt = await call(
                "record_result",
                task_id=task["id"],
                workstream_id=a,
                expected_revision=added["revision"],
                implementer="builder",
                summary="Built A",
                evidence="Checked A",
                artifacts=[{"kind": "artifact", "reference": str(__file__)}],
                verification="Synthetic SDK verification",
                specification_etag=task["specification_etag"],
            )
            assert (await call("get_next_action", workstream_id=a))["action"] == "review"
            assert (await call("get_next_action", workstream_id=b))["action"] == "implement"
            await call(
                "record_review",
                attempt_id=attempt["id"],
                expected_revision=1,
                reviewer="checker",
                verdict="pass",
                note="Synthetic review",
            )
            assert (await call("get_next_action", workstream_id=b))["action"] == "implement"
            removed = await call(
                "remove_from_workstream",
                task_id=task["id"],
                workstream_id=a,
                expected_revision=attempt["task_revision"],
            )
            assert removed["workstream_ids"] == [b] and removed["adopted"]
            assert (await call("get_attempt", attempt_id=attempt["id"]))["state"] == "passed"
            assert (await call("get_next_action", workstream_id=a))["task"] is None

    asyncio.run(exercise())
