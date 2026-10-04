"""Discover packaged workflows and validate their examples against the real catalog."""

import asyncio
import hashlib
import re
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


def referenced_calls(content):
    """Read inline tool examples without imposing prose or length requirements."""
    for match in re.finditer(r"`([a-z][a-z_]+)\(([^`]*)\)`", content):
        yield match.group(1), " ".join(match.group(2).split())


def test_fresh_client_packaged_skills_match_the_discovered_tool_contract(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "reference.sqlite3"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "task_mcp", "--db", str(database), "--no-trace"],
        env={"PYTHONPATH": str(root / "src"), "TASK_MCP_DB": str(database)},
    )

    async def exercise():
        async with Client(params, read_timeout_seconds=30) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert len(tools) == 26

            async def call(tool_name, **arguments):
                result = await client.call_tool(tool_name, arguments)
                assert not result.is_error, result.content
                return result.structured_content

            runtime = (await call("init", path=str(tmp_path / "probe"), branch="main"))["runtime"]
            assert runtime["package_path"] == str(root / "src/task_mcp")
            index = await call("get_default_skills")
            assert index["version"] == "1.18.0"
            checked = set()
            for item in index["items"]:
                named = await call("get_default_skills", name=item["name"])
                skill = named["items"][0]
                assert skill["version"] == item["version"]
                assert hashlib.sha256(skill["content"].encode()).hexdigest() == item["sha256"]
                assert skill["description"] == item["description"]
                for name, arguments in referenced_calls(skill["content"]):
                    assert name in tools, (item["name"], name)
                    checked.add(name)
                    # Complete positional signatures must match property order, including
                    # optional trailing arguments. Partial keyword examples stay contextual.
                    if arguments and re.fullmatch(r"[a-z_]+(?:, [a-z_]+)*", arguments):
                        assert arguments.split(", ") == list(
                            tools[name].input_schema["properties"]
                        ), (item["name"], name, arguments)
                    for argument in re.findall(r"\b([a-z_]+)=", arguments):
                        assert argument in tools[name].input_schema["properties"], (
                            item["name"],
                            name,
                            argument,
                        )
            assert {"add_to_workstream", "reorder_tasks", "signoff_task"} <= checked

    asyncio.run(exercise())
