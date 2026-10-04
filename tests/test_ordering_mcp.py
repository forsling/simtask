"""Real SDK one-call prefix ordering on disposable overlapping workstreams."""

import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


def test_sdk_ordered_prefix_catalog_tokens_and_independent_lists(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "order.sqlite3"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "task_mcp", "--db", str(database)],
        env={"PYTHONPATH": str(root / "src"), "TASK_MCP_DB": str(database)},
    )

    async def exercise():
        async with Client(parameters, read_timeout_seconds=30) as client:
            catalog = {tool.name: tool for tool in (await client.list_tools()).tools}
            schema = catalog["reorder_tasks"].input_schema
            assert set(schema["required"]) == {
                "workstream_id",
                "task_ids",
                "expected_order_revision",
            }
            assert set(schema["properties"]) == set(schema["required"])
            assert schema["properties"]["task_ids"]["type"] == "array"

            async def call(name, **arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.content
                return result.structured_content

            ctx = await call(
                "init",
                path=str(tmp_path / "repo"),
                branch="a",
                action="create_project",
                confirmed=True,
            )
            project, a = ctx["project"]["id"], ctx["workstream"]["id"]
            bctx = await call(
                "init",
                path=str(tmp_path / "repo"),
                branch="b",
                action="new_workstream",
                confirmed=True,
            )
            b = bctx["workstream"]["id"]
            tasks = []
            for title in ("First", "Second", "Third", "Fourth"):
                task = await call("create_task", project=project, title=title, workstream_id=a)
                await call(
                    "add_to_workstream",
                    task_id=task["id"],
                    workstream_id=b,
                    expected_revision=task["revision"],
                )
                tasks.append(task["id"])
            before_b = await call("list_tasks", project=project, workstream_id=b)
            initial = await call("get_next_action", workstream_id=a)
            ack = await call(
                "reorder_tasks",
                workstream_id=a,
                task_ids=[tasks[3], tasks[1]],
                expected_order_revision=initial["workstream_order_revision"],
            )
            assert ack["changed"] and ack["supplied_count"] == 2 and ack["total"] == 4
            assert not {"task_ids", "items", "ordered_ids"} & ack.keys()
            board = await call("list_tasks", project=project, workstream_id=a)
            expected = [tasks[3], tasks[1], tasks[0], tasks[2]]
            assert [task["id"] for task in board["items"]] == expected
            assert (await call("list_tasks", project=project, workstream_id=b)) == before_b
            assert (await call("get_next_action", workstream_id=a))["task"]["id"] == tasks[3]
            for response in (
                await call("workstream_status", workstream_id=a),
                await call("init", path=str(tmp_path / "repo"), branch="a"),
            ):
                assert response["workstream_order_revision"] == ack["workstream_order_revision"]
            for ids in ([], [tasks[3]], expected):
                noop = await call(
                    "reorder_tasks",
                    workstream_id=a,
                    task_ids=ids,
                    expected_order_revision=ack["workstream_order_revision"],
                )
                assert (
                    not noop["changed"]
                    and noop["workstream_order_revision"] == ack["workstream_order_revision"]
                )
            for ids, revision in (
                ([tasks[0], tasks[0]], ack["workstream_order_revision"]),
                (["missing"], ack["workstream_order_revision"]),
                ([tasks[0]], initial["workstream_order_revision"]),
            ):
                result = await client.call_tool(
                    "reorder_tasks",
                    {"workstream_id": a, "task_ids": ids, "expected_order_revision": revision},
                )
                assert result.is_error
                assert (await call("list_tasks", project=project, workstream_id=a)) == board

    asyncio.run(exercise())
