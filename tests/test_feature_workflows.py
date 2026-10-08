"""Feature capture is safely gated in the inbox before branch queue placement."""

import pytest

from simtask.store import Store


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    setup = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    return store, setup["project"]["id"], setup["workstream"]["id"]


def test_capture_inbox_gate_then_queue_and_decide_without_delivery(context):
    store, project, ws = context
    brief = store.create_task(
        project, "Design filters", source="user", user_request="Save a design task"
    )
    assert store.get_next_action(ws)["task"] is None
    brief = store.add_unresolved(
        brief["id"],
        brief["revision"],
        "Feature design required (feature-design): choose persistence",
    )
    brief = store.add_to_workstream(brief["id"], ws, brief["revision"])
    assert store.get_next_action(ws)["task"] is None
    full = store.get_tasks([brief["id"]])["items"][0]
    agreed = store.update_task(
        brief["id"],
        brief["revision"],
        {"body": "Persist per project", "acceptance_criteria": "Restore on restart"},
        full["specification_etag"],
    )
    assert agreed["workstream_ids"] == [ws]
    assert store.get_next_action(ws)["task"] is None
    resolved = store.resolve_unresolved(
        brief["id"],
        agreed["revision"],
        full["unresolved_items"][0]["id"],
        "User chose project persistence",
    )
    assert store.get_next_action(ws)["task"]["id"] == resolved["id"]
    assert store.get_tasks([brief["id"]])["items"][0]["attempts"] == []


def test_project_inbox_design_is_discoverable(context):
    store, project, ws = context
    brief = store.create_task(project, "Explore reset behavior")
    store.add_unresolved(brief["id"], 1, "Feature design required (task-design): choose reset")
    assert [t["id"] for t in store.list_tasks(project, state="inbox")["items"]] == [brief["id"]]
    assert store.list_tasks(project, ws)["items"] == []


def test_design_decomposition_gates_children_before_queue(context):
    store, project, ws = context
    parent = store.create_task(project, "Filter feature", body="Agreed split")
    group = store.decompose_task(
        parent["id"], 1, [{"title": "Persist filters"}, {"title": "Reset filters"}]
    )
    persistence, reset = store.get_tasks(group["members"])["items"]
    assert persistence["workstream_ids"] == reset["workstream_ids"] == []
    reset = store.add_prerequisite(reset["id"], reset["revision"], persistence["id"])
    store.set_scope(
        ws, store.workstream_status(ws)["workstream"]["revision"], f"{ws} +{group['id']}"
    )
    assert store.get_next_action(ws)["task"]["id"] == persistence["id"]
    assert store.get_tasks([reset["id"]])["items"][0]["prerequisites"][0]["blocking"]
