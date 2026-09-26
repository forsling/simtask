"""Exercise the real stdio MCP interface against a disposable v1 database."""

import asyncio
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
        env={"PYTHONPATH": str(root / "src")},
    )
    async with Client(server, read_timeout_seconds=15) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 32
        assert "dismiss_gate_proposal" in {tool.name for tool in tools}
        assert {"init", "workstream_status"} <= {tool.name for tool in tools}
        print(f"Connected over stdio; discovered {len(tools)} tools.")

        async def call(name, **arguments):
            result = await client.call_tool(name, arguments)
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
            scope="workstream",
        )
        task_id = task["id"]
        resumed = await call("init", path=project_path, branch="main")
        assert resumed["state"] == "ready" and resumed["queue"][0]["id"] == task_id
        overview = await call("list_workstreams")
        assert overview["items"][0]["id"] == workstream_id
        status = await call("workstream_status", workstream_id=workstream_id)
        assert status["items"][0]["id"] == task_id
        selected = await call("get_next_task", workstream_id=workstream_id)
        assert selected["task"]["id"] == task_id
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
            proposal_id=proposal["id"],
            expected_revision=1,
            note="Synthetic coordinator decision: concern does not apply",
        )
        assert dismissed["gate_proposals"] == [] and dismissed["revision"] == 2
        result = await call(
            "record_result",
            task_id=task_id,
            workstream_id=workstream_id,
            expected_revision=2,
            implementer="demo-implementer",
            summary="Demo delivered",
            evidence="Synthetic demonstration passed.",
        )
        reviewed = await call(
            "record_review",
            attempt_id=result["id"],
            expected_revision=1,
            reviewer="demo-reviewer",
            verdict="pass",
            note="Independent demo review passed.",
        )
        assert reviewed["state"] == "passed"
        current = (await call("get_tasks", ids=[task_id]))["items"][0]
        signed = await call(
            "signoff_task",
            task_id=task_id,
            expected_revision=current["revision"],
            attempt_id=result["id"],
            verdict="approve",
            user_note="Synthetic demo approval, not a real user verdict.",
        )
        assert signed["status"] == "done"
        exported = await call("export_workstream", workstream_id=workstream_id)
        assert "task-mcp/v1" in exported["content"] and task_id in exported["content"]
        catalog = await call("get_default_skills")
        assert catalog["version"] == "1.0.0" and len(catalog["items"]) == 4
    async with Client(server, read_timeout_seconds=15) as restarted:
        result = await restarted.call_tool("get_tasks", {"ids": [task_id]})
        assert not result.is_error
        assert result.structured_content["items"][0]["status"] == "done"
    print("Restarted the server and verified persistence. Demo passed.")


def main():
    with TemporaryDirectory(prefix="task-mcp-demo-") as temporary:
        asyncio.run(exercise(Path(temporary) / "tasks.sqlite3"))


if __name__ == "__main__":
    main()
