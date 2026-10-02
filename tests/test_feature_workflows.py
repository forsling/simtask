"""Exercise the eligibility invariants used by the reference feature workflows.

These are synthetic user decisions in a disposable Store, not evaluations of
an agent's research or conversational judgment.
"""

import pytest

from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="workflow-test")
    initialized = store.init(
        str(tmp_path / "checkout"), branch="main", action="create_project", confirmed=True
    )
    return store, initialized["project"]["id"], initialized["workstream"]["id"]


def assert_no_implementation(store, workstream):
    assert store.get_next_task(workstream)["task"] is None


def test_capture_is_pending_before_and_after_adding_design_gate(context):
    store, project, workstream = context
    brief = store.create_task(
        project,
        "Remember filter choices",
        body="User requested a design task. Research: filters currently reset on navigation.",
        source="user",
        user_request="Save a design task; leave approval pending",
        workstream_id=workstream,
        scope="workstream",
    )
    assert not brief["accepted"]
    assert_no_implementation(store, workstream)

    brief = store.add_unresolved(
        brief["id"],
        brief["revision"],
        "Feature design required (feature-design): agree persistence and reset behavior.",
    )
    assert not brief["accepted"]
    assert_no_implementation(store, workstream)

    # Pending acceptance has view priority; an unresolved-only query misses it.
    assert store.list_tasks(project, workstream, state="unresolved_items")["items"] == []
    pending = store.list_tasks(project, workstream, state="pending_acceptance")["items"]
    assert [item["id"] for item in pending] == [brief["id"]]
    restored = store.get_tasks([pending[0]["id"]])["items"][0]
    assert restored["unresolved_items"] == brief["unresolved_items"]

    # Even accepting the draft does not bypass the still-open design discussion.
    store.accept_task(
        brief["id"],
        brief["revision"],
        {"basis": "specific", "note": "Synthetic acceptance of draft scope"},
    )
    assert_no_implementation(store, workstream)


def test_project_design_discovery_includes_captured_inbox_ideas(context):
    store, project, workstream = context
    brief = store.create_task(project, "Save an idea for later", source="agent")
    brief = store.add_unresolved(
        brief["id"], brief["revision"], "Feature design required (feature-design): choose scope."
    )
    assert store.list_tasks(project, workstream)["items"] == []
    candidates = store.list_tasks(project, state="pending_acceptance")["items"]
    assert [item["id"] for item in candidates] == [brief["id"]]
    assert store.get_tasks([brief["id"]])["items"][0]["unresolved_items"]
    brief = store.update_task(
        brief["id"], brief["revision"], {"body": "Synthetic agreed feature specification"}
    )
    brief = store.resolve_unresolved(
        brief["id"],
        brief["revision"],
        brief["unresolved_items"][0]["id"],
        "Synthetic design discussion completed",
    )
    brief = store.accept_task(
        brief["id"], brief["revision"], {"basis": "specific", "note": "Synthetic scope approval"}
    )
    assert_no_implementation(store, workstream)
    current = store.workstream_status(workstream)["workstream"]
    store.set_scope(workstream, current["revision"], f"{workstream} +{brief['id']}")
    assert store.get_next_task(workstream)["task"]["id"] == brief["id"]


def test_design_preserves_other_gates_and_requires_current_spec_acceptance(context):
    store, project, workstream = context
    brief = store.create_task(
        project, "Persist filters", source="agent", workstream_id=workstream, scope="workstream"
    )
    brief = store.add_unresolved(brief["id"], brief["revision"], "Choose persistence design")
    design_gate = brief["unresolved_items"][0]["id"]
    brief = store.add_unresolved(brief["id"], brief["revision"], "Need an authorized test fixture")
    fixture_gate = brief["unresolved_items"][1]["id"]
    brief = store.accept_task(
        brief["id"],
        brief["revision"],
        {"basis": "specific", "note": "Synthetic approval of draft scope"},
    )
    agreed = store.update_task(
        brief["id"],
        brief["revision"],
        {
            "body": "Agreed: session storage, scoped to a project. Reset clears its saved filters.",
            "acceptance_criteria": "Navigation preserves filters; reset clears them.",
        },
    )
    assert not agreed["accepted"]
    assert_no_implementation(store, workstream)
    resolved = store.resolve_unresolved(
        agreed["id"], agreed["revision"], design_gate, "Synthetic user decision: session storage"
    )
    assert [item["id"] for item in resolved["unresolved_items"]] == [fixture_gate]
    assert not resolved["accepted"]
    assert_no_implementation(store, workstream)
    resolved = store.resolve_unresolved(
        resolved["id"], resolved["revision"], fixture_gate, "Synthetic authorized fixture supplied"
    )
    assert_no_implementation(store, workstream)
    accepted = store.accept_task(
        resolved["id"],
        resolved["revision"],
        {"basis": "specific", "note": "Synthetic approval of the exact final specification"},
    )
    assert store.get_next_task(workstream)["task"]["id"] == accepted["id"]
    assert store.get_tasks([accepted["id"]])["items"][0]["attempts"] == []


def test_decomposition_keeps_parent_and_children_ineligible_until_ready(context):
    store, project, workstream = context
    parent = store.create_task(
        project,
        "Persist filters",
        source="user",
        user_request="Synthetic existing authorization",
        approval={"basis": "specific", "note": "Synthetic existing authorization"},
        workstream_id=workstream,
        scope="workstream",
    )
    parent = store.add_unresolved(parent["id"], parent["revision"], "Agree the feature split")
    members = [
        {"title": "Persist session filters", "acceptance_criteria": "Filters survive navigation"},
        {"title": "Add reset control", "acceptance_criteria": "Reset clears saved filters"},
    ]
    with pytest.raises(TaskError, match="unresolved_items"):
        store.decompose_task(parent["id"], parent["revision"], members)
    parent = store.update_task(
        parent["id"],
        parent["revision"],
        {
            "body": "Agreed split: persist session filters, then add the reset control.",
            "acceptance_criteria": "Navigation preserves filters; a reset control clears them.",
        },
    )
    assert not parent["accepted"]
    assert_no_implementation(store, workstream)
    parent = store.resolve_unresolved(
        parent["id"],
        parent["revision"],
        parent["unresolved_items"][0]["id"],
        "Synthetic agreement on these member boundaries",
    )
    assert_no_implementation(store, workstream)
    group = store.decompose_task(parent["id"], parent["revision"], members)
    assert group["object_type"] == "group"
    assert_no_implementation(store, workstream)
    persistence, reset = store.get_tasks(group["members"])["items"]
    assert not persistence["accepted"] and not reset["accepted"]
    reset = store.add_prerequisite(reset["id"], reset["revision"], persistence["id"])
    store.accept_task(
        reset["id"],
        reset["revision"],
        {"basis": "specific", "note": "Synthetic approval of reset member"},
    )
    assert_no_implementation(store, workstream)
    persistence = store.accept_task(
        persistence["id"],
        persistence["revision"],
        {"basis": "specific", "note": "Synthetic approval of persistence member"},
    )
    assert store.get_next_task(workstream)["task"]["id"] == persistence["id"]
    states = {item["id"]: item["view"] for item in store.list_tasks(project, workstream)["items"]}
    assert states == {persistence["id"]: "ready", reset["id"]: "prerequisites"}
