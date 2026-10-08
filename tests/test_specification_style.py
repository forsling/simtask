"""The user's specification-style instruction stays verbatim and said once."""

import asyncio

from simtask.reference import default_skills
from simtask.server import create_server
from simtask.store import Store

# The user's exact text. Do not paraphrase it here or in the server instructions.
INSTRUCTION = (
    "Write task specifications using ASD-STE100 sentence and structure principles. "
    "Use short sentences, explicit subjects, active voice, and consistent technical terms. "
    "Put conditions before the behavior they control. "
    "State one requirement or instruction per sentence. "
    "Separate context, requirements, procedures, boundaries, and acceptance criteria. "
    "Preserve every exception and unresolved question. "
    "Retain exact technical identifiers and UI labels."
)


def test_server_instructions_hold_the_instruction_verbatim_once(tmp_path):
    server = create_server(Store(tmp_path / "tasks.sqlite3"), tracing=False)
    assert server.instructions.count(INSTRUCTION) == 1


def test_no_skill_or_tool_description_repeats_the_instruction(tmp_path):
    server = create_server(Store(tmp_path / "tasks.sqlite3"), tracing=False)
    tools = asyncio.run(server.list_tools())
    assert tools
    for tool in tools:
        assert "ASD-STE100" not in (tool.description or ""), tool.name
    for item in default_skills()["items"]:
        content = default_skills(item["name"])["items"][0]["content"]
        assert "ASD-STE100" not in content, item["name"]
