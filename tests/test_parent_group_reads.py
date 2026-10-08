"""Member reads carry a parent-group reference; a read of the group gives its body."""

import asyncio
import json

import pytest

from simtask.server import create_server
from simtask.store import Store
from simtask.viewer import dispatch

GROUP_BODY = "Context\nThe whole group design. " * 200
GROUP_CRITERIA = "- Every member ships the shared design."
REFERENCE_KEYS = {"id", "title", "summary", "summary_stale"}


@pytest.fixture
def grouped(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    setup = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    project, ws = setup["project"]["id"], setup["workstream"]["id"]
    group = store.create_task(
        project,
        "Shared design",
        GROUP_BODY,
        GROUP_CRITERIA,
        workstream_id=ws,
        summary="One shared design",
        kind="group",
    )
    member = store.create_task(
        project,
        "Member",
        "Build one part. Read the group for the design.",
        "- The part works.",
        workstream_id=ws,
        group_id=group["id"],
        group_expected_revision=group["revision"],
    )
    return store, ws, group["id"], member["id"]


def assert_reference(item, group_id):
    parent = item["parent_group"]
    assert parent == {
        "id": group_id,
        "title": "Shared design",
        "summary": "One shared design",
        "summary_stale": False,
    }
    # The member's own specification stays complete.
    assert item["body"] == "Build one part. Read the group for the design."
    assert item["acceptance_criteria"] == "- The part works."


def test_default_member_reads_carry_only_a_parent_reference(grouped):
    store, ws, group_id, member_id = grouped
    action = store.get_next_action(ws)
    assert action["action"] == "implement" and action["task"]["id"] == member_id
    spec = store.read_tasks([member_id], specification=True, workstream_id=ws)["items"][0]
    for item in (action["task"], spec):
        assert_reference(item, group_id)
    for response in (action, spec):
        text = json.dumps(response)
        assert "The whole group design" not in text and GROUP_CRITERIA not in text
    # The full parent costs well under its own size in every member read.
    assert len(json.dumps(spec)) < len(GROUP_BODY)


def test_a_stale_parent_summary_is_flagged(grouped):
    store, ws, group_id, member_id = grouped
    group = store.get_tasks([group_id])["items"][0]
    store.update_task(
        group_id,
        group["revision"],
        {"body": GROUP_BODY + "More."},
        group["specification_etag"],
    )
    spec = store.read_tasks([member_id], specification=True)["items"][0]
    assert spec["parent_group"]["summary_stale"] is True


def test_the_full_parent_is_one_read_away(grouped):
    store, ws, group_id, member_id = grouped
    # Opt-in: add the parent's ID to the same specification read.
    items = store.read_tasks([member_id, group_id], specification=True, workstream_id=ws)["items"]
    member, group = items
    assert_reference(member, group_id)
    assert group["id"] == group_id
    assert group["body"] == GROUP_BODY and group["acceptance_criteria"] == GROUP_CRITERIA
    assert group["summary"] == "One shared design"


def test_viewer_and_export_keep_their_group_context(grouped):
    store, ws, group_id, member_id = grouped
    # The viewer's chip names and opens the group; opening reads the group itself.
    detail = dispatch(store, "details", {"ids": [member_id]})["items"][0]
    assert detail["parent_group"]["id"] == group_id
    assert detail["parent_group"]["title"] == "Shared design"
    group = dispatch(store, "details", {"ids": [group_id]})["items"][0]
    assert group["body"] == GROUP_BODY and member_id in group["members"]
    exported = store.export_workstream(ws)["content"]
    assert f"- Group: Shared design (`{group_id}`)" in exported


def test_get_tasks_says_once_how_to_read_the_full_group(tmp_path):
    server = create_server(Store(tmp_path / "tasks.sqlite3"), tracing=False)
    tools = {tool.name: tool.description for tool in asyncio.run(server.list_tools())}
    clause = "A parent_group gives only id, title and summary: add its ID for the group's body."
    assert clause in tools["get_tasks"]
    assert [name for name, text in tools.items() if "parent_group" in text] == ["get_tasks"]
