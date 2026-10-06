import hashlib
from importlib.resources import files

import pytest

from task_mcp.reference import default_skills


def test_catalog_is_exact_canonical_on_disk_content():
    catalog = default_skills()
    assert catalog["version"] == "2.0.0"
    assert {item["name"] for item in catalog["items"]} == {
        "init",
        "task-capture",
        "task-design",
        "proposal-review",
        "superdevloop",
        "task-signoff",
    }
    root = files("task_mcp").joinpath("reference_skills")
    for item in catalog["items"]:
        contents = root.joinpath(item["path"]).read_bytes()
        assert "content" not in item and item["description"]
        named = default_skills(item["name"])
        assert len(named["items"]) == 1
        content = named["items"][0]["content"]
        assert content.encode() == contents
        frontmatter = content.split("---", 2)[1]
        assert f"name: {item['name']}\n" in frontmatter
        assert f"description: {item['description']}\n" in frontmatter
        assert "projects using Task MCP" in item["description"]
        assert item["sha256"] == hashlib.sha256(contents).hexdigest()
        assert item["version"] == catalog["version"]


def test_unknown_skill_does_not_return_an_unrelated_workflow():
    with pytest.raises(ValueError, match="unknown_skill"):
        default_skills("unknown")


def test_short_ids_and_going_through_ideas_are_said_once_where_agents_look(tmp_path):
    import asyncio

    from task_mcp.server import create_server
    from task_mcp.store import Store

    server = create_server(Store(tmp_path / "tasks.sqlite3"), tracing=False)
    tools = {tool.name: tool.description for tool in asyncio.run(server.list_tools())}
    # The limit is stated in the creation tool descriptions, which ask for a short ID.
    for name in ("create_task", "decompose_task"):
        assert "short" in tools[name] and "at most 40 characters" in tools[name], name
        assert "derives" not in tools[name] and "slug" not in tools[name], name
    # The advice and the ideas walkthrough live in task-capture, reached by both routes.
    assert 'For "go through my ideas", read task-capture.' in server.instructions
    capture = next(i for i in default_skills()["items"] if i["name"] == "task-capture")
    assert '"go through my ideas"' in capture["description"]
    content = default_skills("task-capture")["items"][0]["content"]
    assert "short `public_id` of about 2-4" in " ".join(content.split())
    section = content.split("## Go through my ideas", 1)[1]
    assert "Idea to process:" in section and "never unattended" in section
    assert 40 <= len(section.split()) <= 60
    assert "40" not in content  # The limit itself is said in the tool descriptions.
