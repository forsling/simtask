import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from task_mcp.store import Store, TaskError, default_database


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private" / "tasks.sqlite3", actor="test-coordinator")


def setup(store, tmp_path, branch="main"):
    path = str(tmp_path / "checkout")
    result = store.init_project(path, branch=branch, confirmed=True)
    return result["project"]["id"], result["workstream"]["id"], path


def task(store, project, ws, title="Implement", source="user"):
    return store.create_task(
        project,
        title,
        "Goal and boundaries",
        "Observable acceptance",
        source=source,
        user_request="User requested this outcome" if source == "user" else "",
        workstream_id=ws,
        scope="workstream",
    )


def complete(store, task_id, workstream_id, revision):
    result = store.record_result(task_id, workstream_id, revision, "worker", "Done", "Verified")
    store.record_review(result["id"], 1, "reviewer", "pass", "Checked")
    return store.signoff_task(task_id, revision + 1, "approve", "User approved", result["id"])


def test_explicit_init_and_attachment_do_not_write_checkouts(store, tmp_path):
    path = tmp_path / "checkout"
    path.mkdir()
    with pytest.raises(TaskError, match="project_not_initialized"):
        store.list_tasks(str(path))
    with pytest.raises(TaskError, match="confirmation_required"):
        store.init_project(str(path), branch="main")
    project, ws, canonical = setup(store, tmp_path)
    assert store.preflight(project, canonical, branch="main")["workstream"]["id"] == ws
    assert list(path.iterdir()) == []
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(TaskError, match="checkout_not_attached"):
        store.init_workstream(project, str(other), branch="feature", confirmed=True)
    store.attach_checkout(project, str(other), confirmed=True)
    feature = store.init_workstream(project, str(other), branch="feature", confirmed=True)
    assert feature["members"] == []
    with pytest.raises(TaskError, match="workstream_mismatch"):
        store.preflight(project, canonical, branch="feature", workstream_id=ws)
    assert list(other.iterdir()) == []


def test_workstream_rebinding_and_named_non_git_context(store, tmp_path):
    project, ws, path = setup(store, tmp_path)
    with pytest.raises(TaskError, match="workstream_name_required"):
        store.init_workstream(project, path, confirmed=True)
    renamed = store.rebind_workstream(ws, 1, path, branch="renamed", confirmed=True)
    assert renamed["id"] == ws and renamed["revision"] == 2
    assert store.preflight(project, path, branch="renamed")["workstream"]["id"] == ws
    with pytest.raises(TaskError, match="workstream_not_initialized"):
        store.preflight(project, path, branch="main")
    named = store.init_workstream(project, path, name="detached", confirmed=True)
    assert (
        store.preflight(project, path, workstream_id=named["workstream"]["id"])["workstream"][
            "name"
        ]
        == "detached"
    )


def test_pending_acceptance_spec_invalidation_and_unresolved_resolution(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    proposed = task(store, project, ws, source="agent")
    assert not proposed["accepted"]
    assert store.get_next_task(ws)["diagnostics"]["pending_acceptance"] == 1
    accepted = store.accept_task(proposed["id"], 1, "User accepted exact specification")
    assert accepted["accepted"]
    gated = store.add_unresolved(proposed["id"], 2, "Need user-only credential")
    assert store.get_next_task(ws)["diagnostics"]["unresolved_items"] == 1
    resolved = store.resolve_unresolved(
        proposed["id"], 3, gated["unresolved_items"][0]["id"], "Credential provided"
    )
    assert resolved["accepted"]
    assert store.get_next_task(ws)["task"]["id"] == proposed["id"]
    edited = store.update_task(proposed["id"], 4, {"body": "Materially different goal"})
    assert edited["spec_revision"] == 2 and not edited["accepted"]
    with pytest.raises(TaskError, match="task_not_eligible"):
        store.record_result(proposed["id"], ws, 5, "implementer", "Done", "Tests pass")


def test_scope_set_expression_snapshot_and_live_group_members(store, tmp_path):
    project, ws, path = setup(store, tmp_path)
    one = task(store, project, ws, "One")
    two = store.create_task(project, "Two", source="agent")
    assert store.preflight(project, path, branch="main")["scope"] == [one["id"]]
    branch = store.init_workstream(
        project,
        path,
        branch="other",
        name="other",
        scope_expression=f"main +{two['id']} -{one['id']}",
        confirmed=True,
    )
    other = branch["workstream"]["id"]
    assert store.get_next_task(other)["diagnostics"]["pending_acceptance"] == 1
    third = task(store, project, ws, "Three")
    assert third["id"] not in store.list_workstreams(project)["items"][1]["scope"]
    parent = task(store, project, ws, "Parent")
    group = store.decompose_task(parent["id"], 1, [{"title": "Child A"}])
    child_a = group["members"][0]
    scoped = store.set_scope(other, 1, f"none +{parent['id']}")
    assert parent["id"] in store.list_workstreams(project)["items"][1]["scope"]
    assert (
        child_a in scoped["groups"]
        or child_a in store.list_workstreams(project)["items"][1]["scope"]
    )
    child_b = store.create_task(project, "Child B", group_id=parent["id"])
    assert child_b["id"] in store.list_workstreams(project)["items"][1]["scope"]
    assert not child_b["accepted"]


def test_explicit_exclusions_override_live_group_expansion(store, tmp_path):
    project, ws, path = setup(store, tmp_path)
    parent = task(store, project, ws, "Parent")
    group = store.decompose_task(parent["id"], 1, [{"title": "A"}, {"title": "B"}])
    a, b = group["members"]
    revision = store.list_workstreams(project)["items"][0]["revision"]
    changed = store.set_scope(ws, revision, f"none +{parent['id']} -{a}")
    assert changed["exclusions"] == [a]
    assert a not in store.list_workstreams(project)["items"][0]["scope"]
    assert b in store.list_workstreams(project)["items"][0]["scope"]
    later = store.create_task(project, "Later", group_id=parent["id"])
    assert later["id"] in store.list_workstreams(project)["items"][0]["scope"]
    inherited = store.init_workstream(
        project, path, branch="other", scope_expression="main", confirmed=True
    )["workstream"]["id"]
    assert a not in store.list_workstreams(project)["items"][1]["scope"]
    store.set_scope(inherited, 1, f"main +{a}")
    assert a in store.list_workstreams(project)["items"][1]["scope"]
    store.set_scope(inherited, 2, f"none +{parent['id']} -{parent['id']}")
    assert store.list_workstreams(project)["items"][1]["scope"] == []


def test_prerequisites_and_observer_gate_proposals(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    first = task(store, project, ws, "First")
    second = task(store, project, ws, "Second")
    observer = store.add_unresolved(first["id"], 1, "Possible issue", handling="observer")
    assert store.get_next_task(ws)["task"]["id"] == first["id"]
    activated = store.accept_gate_proposal(observer["id"], 1)
    assert activated["unresolved_items"]
    blocked = store.add_prerequisite(second["id"], 1, first["id"])
    assert blocked["blocked_by"] == [first["id"]]
    assert store.get_next_task(ws)["diagnostics"]["prerequisites"] == 1
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.add_prerequisite(first["id"], 2, second["id"])


def test_cyclic_gate_proposal_can_be_dismissed_before_decomposition(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    first = task(store, project, ws, "First")
    second = task(store, project, ws, "Second")
    store.add_prerequisite(second["id"], 1, first["id"])
    proposal = store.add_prerequisite(first["id"], 1, second["id"], handling="observer")
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.accept_gate_proposal(proposal["id"], 1)
    with pytest.raises(TaskError, match="gate_proposals"):
        store.decompose_task(first["id"], 1, [{"title": "Member"}])
    with pytest.raises(TaskError, match="decision_note_required"):
        store.dismiss_gate_proposal(proposal["id"], 1, " ")
    with pytest.raises(TaskError, match="revision_conflict"):
        store.dismiss_gate_proposal(proposal["id"], 0, "Stale decision")
    dismissed = store.dismiss_gate_proposal(
        proposal["id"], 1, "Cyclic dependency cannot be activated"
    )
    assert dismissed["revision"] == 2
    assert dismissed["gate_proposals"] == [] and dismissed["blocked_by"] == []
    with pytest.raises(TaskError, match="unknown_gate_proposal"):
        store.dismiss_gate_proposal(proposal["id"], 2, "Already resolved")
    events = store.list_events(project, first["id"], include_details=True)["items"]
    decision = next(
        event
        for event in events
        if event["action"] == "gate.proposal_dismissed" and event["outcome"] == "ok"
    )
    assert decision["actor"] == "test-coordinator"
    assert decision["request"]["note"] == "Cyclic dependency cannot be activated"
    assert decision["before"]["proposal"]["id"] == proposal["id"]
    assert decision["before"]["proposal"]["detail"] == second["id"]
    assert decision["after"]["task"]["revision"] == 2
    assert store.decompose_task(first["id"], 2, [{"title": "Member"}])["members"]


def test_gate_proposal_can_be_dismissed_after_target_is_dropped(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    owner = task(store, project, ws, "Owner")
    target = task(store, project, ws, "Target")
    proposal = store.add_prerequisite(owner["id"], 1, target["id"], handling="observer")
    store.set_disposition(target["id"], 1, "dropped", "No longer needed")
    with pytest.raises(TaskError, match="invalid_prerequisite"):
        store.accept_gate_proposal(proposal["id"], 1)
    dismissed = store.dismiss_gate_proposal(proposal["id"], 1, "Target was dropped")
    assert dismissed["revision"] == 2 and dismissed["gate_proposals"] == []
    assert store.get_tasks([target["id"]])["items"][0]["status"] == "dropped"


def test_completed_task_gate_proposals_cannot_be_dismissed(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    owner = task(store, project, ws, "Owner")
    proposal = store.add_unresolved(owner["id"], 1, "Possible issue", handling="observer")
    completed = complete(store, owner["id"], ws, 1)
    with pytest.raises(TaskError, match="completed_task_immutable"):
        store.dismiss_gate_proposal(proposal["id"], completed["revision"], "Too late")


def test_pending_prerequisite_proposal_is_atomic_and_disposition_preserves_acceptance(
    store, tmp_path
):
    project, ws, _ = setup(store, tmp_path)
    parent = task(store, project, ws)
    added = store.propose_prerequisite(
        parent["id"], 1, "Research dependency", "Scope stays pending", workstream_id=ws
    )
    proposal = added["proposal"]
    assert not proposal["accepted"]
    assert proposal["id"] in added["task"]["blocked_by"]
    assert proposal["id"] in store.list_workstreams(project)["items"][0]["scope"]
    deferred = store.set_disposition(parent["id"], 2, "deferred", "Wait for research")
    assert deferred["accepted"] and deferred["status"] == "deferred"
    resumed = store.set_disposition(parent["id"], 3, "open", "Research resumed")
    assert resumed["accepted"] and resumed["body"] == parent["body"]


def test_group_dependency_unblocks_when_all_members_complete(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    parent = task(store, project, ws, "Grouped work")
    downstream = task(store, project, ws, "Downstream")
    store.add_prerequisite(downstream["id"], 1, parent["id"])
    group = store.decompose_task(parent["id"], 1, [{"title": "One"}, {"title": "Two"}])
    assert downstream["id"] in {
        item["id"] for item in store.list_tasks(project, ws, state="prerequisites")["items"]
    }
    for member in group["members"]:
        accepted = store.accept_task(member, 1, "User accepted member")
        attempt = store.record_result(
            member, ws, accepted["revision"], "worker", "Done", "Verified"
        )
        store.record_review(attempt["id"], 1, "reviewer", "pass", "Reviewed")
        store.signoff_task(member, accepted["revision"] + 1, "approve", "Approved", attempt["id"])
    assert store.get_tasks([parent["id"]])["items"][0]["complete"]
    completed_group = store.get_tasks([parent["id"]])["items"][0]
    assert completed_group["accepted"] is None
    with pytest.raises(TaskError, match="completed_task_immutable"):
        store.update_task(parent["id"], completed_group["revision"], {"body": "Changed"})
    with pytest.raises(TaskError, match="completed_task_immutable"):
        store.create_task(project, "Late required member", group_id=parent["id"])
    assert store.get_next_task(ws)["task"]["id"] == downstream["id"]


def test_group_has_no_execution_gates_and_requires_resolved_decomposition(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    parent = task(store, project, ws, "Parent")
    gated = store.add_unresolved(parent["id"], 1, "Choose the split")
    with pytest.raises(TaskError, match="unresolved_items"):
        store.decompose_task(parent["id"], 2, [{"title": "A"}])
    resolved = store.resolve_unresolved(
        parent["id"], 2, gated["unresolved_items"][0]["id"], "Split chosen"
    )
    group = store.decompose_task(parent["id"], resolved["revision"], [{"title": "A"}])
    assert group["unresolved_items"] == []
    assert group["blocked_by"] == [] and group["attempts"] == []
    for action in (
        lambda: store.accept_task(parent["id"], group["revision"], "Accept"),
        lambda: store.set_disposition(parent["id"], group["revision"], "deferred", "Wait"),
        lambda: store.add_unresolved(parent["id"], group["revision"], "Gate"),
        lambda: store.propose_prerequisite(parent["id"], group["revision"], "New task"),
        lambda: store.record_result(parent["id"], ws, group["revision"], "worker", "Done", "OK"),
    ):
        with pytest.raises(TaskError, match="group_not_executable|task_not_eligible"):
            action()


def test_effective_cycle_detection_includes_group_member_edges(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    parent = task(store, project, ws, "Parent")
    member = store.decompose_task(parent["id"], 1, [{"title": "Member"}])["members"][0]
    other = task(store, project, ws, "Other")
    store.add_prerequisite(member, 1, other["id"])
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.add_prerequisite(other["id"], 1, parent["id"])
    proposed = store.add_prerequisite(other["id"], 1, parent["id"], handling="observer")
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.accept_gate_proposal(proposed["id"], 1)
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.add_prerequisite(member, 2, parent["id"])


def test_group_prerequisites_before_and_after_decomposition(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    foundation = task(store, project, ws, "Foundation")
    parent = task(store, project, ws, "Parent")
    downstream = task(store, project, ws, "Downstream")
    store.add_prerequisite(parent["id"], 1, foundation["id"])
    store.add_prerequisite(downstream["id"], 1, parent["id"])
    group = store.decompose_task(parent["id"], 2, [{"title": "A"}, {"title": "B"}])
    assert group["blocked_by"] == []
    for member in group["members"]:
        assert store.get_tasks([member])["items"][0]["blocked_by"] == [foundation["id"]]
    complete(store, foundation["id"], ws, 1)
    for member in group["members"]:
        accepted = store.accept_task(member, 1, "User accepted member")
        complete(store, member, ws, accepted["revision"])
    assert store.get_next_task(ws)["task"]["id"] == downstream["id"]
    later = task(store, project, ws, "Later")
    linked = store.add_prerequisite(later["id"], 1, parent["id"])
    assert linked["blocked_by"] == [parent["id"]]
    assert store.list_tasks(project, ws, state="ready")["items"][0]["id"] == downstream["id"]


def test_every_explicit_scope_write_bumps_revision_atomically(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    assert store.list_workstreams(project)["items"][0]["revision"] == 1
    included = task(store, project, ws, "Included")
    assert store.list_workstreams(project)["items"][0]["revision"] == 2
    store.create_task(project, "Inbox")
    assert store.list_workstreams(project)["items"][0]["revision"] == 2
    store.set_scope(ws, 2, f"none +{included['id']}")
    assert store.list_workstreams(project)["items"][0]["revision"] == 3
    store.decompose_task(included["id"], 1, [{"title": "Child"}])
    assert store.list_workstreams(project)["items"][0]["revision"] == 4
    child = store.get_tasks([included["id"]])["items"][0]["members"][0]
    store.propose_prerequisite(child, 1, "Needed", workstream_id=ws)
    assert store.list_workstreams(project)["items"][0]["revision"] == 5
    with pytest.raises(TaskError, match="revision_conflict"):
        store.set_scope(ws, 4, "none")


def test_scope_revision_and_membership_roll_back_with_failed_audit(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("""CREATE TRIGGER reject_task_event BEFORE INSERT ON events
            WHEN NEW.action='task.created'
            BEGIN SELECT RAISE(ABORT, 'audit rejected'); END""")
        db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="audit rejected"):
        task(store, project, ws, "Never committed")
    workstream = store.list_workstreams(project)["items"][0]
    assert workstream["revision"] == 1 and workstream["scope"] == []
    assert store.list_tasks(project)["items"] == []


def test_completed_tasks_and_attempts_are_immutable(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    created = task(store, project, ws, "Completed")
    done = complete(store, created["id"], ws, 1)
    attempt_id = done["selected_attempt_id"]
    other = task(store, project, ws, "Other")
    actions = (
        lambda: store.update_task(done["id"], done["revision"], {"body": "Changed"}),
        lambda: store.accept_task(done["id"], done["revision"], "Again"),
        lambda: store.set_disposition(done["id"], done["revision"], "open", "Reopen"),
        lambda: store.add_unresolved(done["id"], done["revision"], "New gate"),
        lambda: store.add_prerequisite(done["id"], done["revision"], other["id"]),
        lambda: store.propose_prerequisite(done["id"], done["revision"], "New task"),
        lambda: store.record_result(done["id"], ws, done["revision"], "worker", "Again", "OK"),
        lambda: store.record_review(attempt_id, 2, "another", "pass", "Again"),
        lambda: store.human_review(attempt_id, 2, "Again"),
        lambda: store.reorder_tasks(project, [other["id"], done["id"]], [done["id"], other["id"]]),
    )
    for action in actions:
        with pytest.raises(TaskError, match="completed_task_immutable|task_not_eligible"):
            action()
    assert store.get_tasks([done["id"]])["items"][0]["body"] == created["body"]


def test_attempt_review_human_review_and_signoff_rework_vs_revise(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    created = task(store, project, ws)
    assert store.get_tasks([created["id"]])["items"][0]["attempts"] == []
    result = store.record_result(created["id"], ws, 1, "worker", "Done", "pytest passed")
    with pytest.raises(TaskError, match="independent_review_required"):
        store.record_review(result["id"], 1, "worker", "pass", "Looks good")
    with pytest.raises(TaskError, match="review_required"):
        store.signoff_task(created["id"], 2, "approve", "Approved", result["id"])
    reviewed = store.record_review(result["id"], 1, "reviewer", "pass", "Checked diff")
    assert reviewed["state"] == "passed"
    rejected = store.signoff_task(created["id"], 2, "reject", "Fix input loss", result["id"])
    assert rejected["accepted"] and rejected["status"] == "rework"
    retry = store.record_result(created["id"], ws, 3, "worker", "Fixed", "repro passed")
    human = store.human_review(retry["id"], 1, "User reviewed and waived another reviewer")
    assert human["state"] == "human_review"
    revised = store.signoff_task(
        created["id"], 4, "reject", "Change requirements", retry["id"], rejection="revise"
    )
    assert not revised["accepted"] and revised["unresolved_items"]
    resolved = store.resolve_unresolved(
        created["id"], 5, revised["unresolved_items"][0]["id"], "Settled"
    )
    accepted = store.accept_task(created["id"], resolved["revision"], "Accepted revised spec")
    final = store.record_result(
        created["id"], ws, accepted["revision"], "worker", "New result", "verified"
    )
    store.record_review(final["id"], 1, "reviewer", "pass", "Reviewed new result")
    complete = store.signoff_task(
        created["id"], accepted["revision"] + 1, "approve", "User approved", final["id"]
    )
    assert complete["status"] == "done"
    assert complete["selected_attempt_id"] == final["id"]


def test_parallel_workstream_attempts_and_group_completion(store, tmp_path):
    project, ws, path = setup(store, tmp_path)
    parent = task(store, project, ws, "Group")
    group = store.decompose_task(parent["id"], 1, [{"title": "A"}, {"title": "B"}])
    a, b = group["members"]
    assert not group["complete"]
    branch = store.init_workstream(
        project,
        path,
        branch="alternative",
        scope_expression=f"none +{parent['id']}",
        confirmed=True,
    )
    other = branch["workstream"]["id"]
    for member in (a, b):
        accepted = store.accept_task(member, 1, "User accepted member")
        result = store.record_result(
            member, ws, accepted["revision"], "worker-1", "Done", "tests pass"
        )
        alternate = store.record_result(
            member, other, accepted["revision"] + 1, "worker-2", "Alternative", "tests pass"
        )
        store.record_review(result["id"], 1, "reviewer", "pass", "Reviewed")
        store.record_review(alternate["id"], 1, "reviewer", "pass", "Reviewed")
        store.signoff_task(
            member, accepted["revision"] + 2, "approve", "Winner selected", alternate["id"]
        )
    assert store.get_tasks([parent["id"]])["items"][0]["complete"]


def test_order_and_deterministic_export(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    first = task(store, project, ws, "First")
    second = task(store, project, ws, "Second")
    store.reorder_tasks(
        project, [second["id"], first["id"]], expected_order=[first["id"], second["id"]]
    )
    with pytest.raises(TaskError, match="revision_conflict"):
        store.reorder_tasks(
            project, [first["id"], second["id"]], expected_order=[first["id"], second["id"]]
        )
    assert store.get_next_task(ws)["task"]["id"] == second["id"]
    one = store.export_workstream(ws)
    two = store.export_workstream(ws)
    assert one == two
    assert one["content"].index(second["id"]) < one["content"].index(first["id"])
    assert one["format"] == "task-mcp/v1"


def test_current_schema_reopens_without_changing_task_or_audit(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    created = task(store, project, ws)
    with sqlite3.connect(store.path) as db:
        before_events = db.execute("SELECT count(*) FROM events").fetchone()[0]
    reopened = Store(store.path)
    assert reopened.get_tasks([created["id"]])["items"][0]["id"] == created["id"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] >= before_events


def test_same_revision_concurrent_updates_allow_one_writer(store, tmp_path):
    project, ws, _ = setup(store, tmp_path)
    created = task(store, project, ws)
    barrier = Barrier(2)

    def write(title):
        other = Store(store.path, actor=title)
        barrier.wait(timeout=5)
        try:
            return other.update_task(created["id"], 1, {"title": title})
        except TaskError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["Agent A", "Agent B"]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum("revision_conflict" in result for result in results if isinstance(result, str)) == 1
    events = store.list_events(task_id=created["id"], include_details=True)["items"]
    assert sorted(e["outcome"] for e in events if e["action"] == "task.updated") == ["error", "ok"]


def test_default_data_location_is_private(monkeypatch, tmp_path):
    monkeypatch.delenv("TASK_MCP_DB", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert default_database() == Path(tmp_path / "data/task-mcp/tasks.sqlite3")
