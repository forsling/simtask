"""Canonical dependency edges span projects without sharing scope or delivery proof."""

import sqlite3

import pytest

from task_mcp.store import DATABASE_SCHEMA_REVISION, Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="cross-project-coordinator")
    projects = [
        store.init(str(tmp_path / name), branch="main", action="create_project", confirmed=True)
        for name in ("alpha", "beta", "gamma")
    ]
    return store, projects


def create(store, context, title):
    ack = store.create_task(
        context["project"]["id"],
        title,
        body=f"Full requirements for {title}",
        workstream_id=context["workstream"]["id"],
    )
    return detail(store, ack["id"])


def detail(store, identity):
    return store.get_tasks([identity])["items"][0]


def signoff(store, task, context):
    attempt = store.record_result(
        task["id"],
        context["workstream"]["id"],
        task["revision"],
        "builder",
        "Built",
        "Proof",
        artifacts=[{"kind": "artifact", "reference": "tests/test_cross_project_prerequisites.py"}],
        verification="Proof",
        specification_etag=store.get_tasks([task["id"]])["items"][0]["specification_etag"],
    )
    store.record_review(attempt["id"], 1, "independent reviewer", "pass", "Verified")
    store.signoff_task(
        task["id"],
        task["revision"] + 1,
        "approve",
        "Synthetic informed approval",
        attempt["id"],
        expected_attempt_revision=2,
    )


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
@pytest.mark.parametrize("milestone", ["review", "signoff"])
def test_link_milestone_clears_blocker_without_moving_proof_or_scope(context, remote, milestone):
    store, (a, b, _) = context
    owner = b if remote else a
    dependent = create(store, a, "Dependent")
    blocker = create(store, owner, "Canonical blocker")
    ws = a["workstream"]["id"]
    before_scope = store.workstream_status(ws)["workstream"]
    attempt = store.record_result(
        blocker["id"],
        owner["workstream"]["id"],
        1,
        "remote builder",
        "Delivery",
        "Remote evidence stays remote",
        artifacts=[{"kind": "artifact", "reference": "tests/test_cross_project_prerequisites.py"}],
        verification="Remote evidence stays remote",
        specification_etag=store.get_tasks([blocker["id"]])["items"][0]["specification_etag"],
    )
    linked = store.add_prerequisite(dependent["id"], 1, blocker["id"], milestone=milestone)
    ref = linked["prerequisites"][0]
    assert ref == {
        "id": blocker["id"],
        "title": blocker["title"],
        "object_type": "task",
        "project_id": owner["project"]["id"],
        "project_name": owner["project"]["name"],
        "state": "open",
        "complete": False,
        "milestone": milestone,
        "satisfied": False,
        "blocking": True,
    }
    assert linked["revision"] == 2
    assert linked["spec_revision"] == 1 and linked["queue_workstream_id"] == ws
    assert linked["attempts"] == [] and linked["blocked_by"] == [blocker["id"]]
    assert detail(store, blocker["id"])["revision"] == 2  # result only; linking did not touch it
    assert store.workstream_status(ws)["workstream"] == before_scope
    queue = store.list_tasks(a["project"]["id"], ws)["items"]
    row = next(row for row in queue if row["id"] == dependent["id"])
    assert row["prerequisites"] == [ref] and row["view"] == "prerequisites"
    selected = store.get_next_action(ws)
    if remote:
        assert selected["action"] is None
    else:
        assert selected["action"] == "review" and selected["task"]["id"] == blocker["id"]
    scoped = next(
        row
        for row in store.init(str(a["workstream"]["checkout_path"]), branch="main")["queue"]
        if row["id"] == dependent["id"]
    )
    assert scoped["prerequisites"] == [ref]
    assert scoped["gate_diagnostics"] == ["prerequisites"]
    assert "Remote evidence stays remote" not in str(linked)
    store.record_review(attempt["id"], 1, "fresh reviewer", "pass", "Checked remote artifact")
    reviewed = {**ref, "satisfied": milestone == "review", "blocking": milestone != "review"}
    assert detail(store, dependent["id"])["prerequisites"] == [reviewed]
    action = store.get_next_action(ws)
    if milestone == "review":
        assert action["action"] == "implement" and action["task"]["id"] == dependent["id"]
    else:
        assert action["task"] is None
    store.signoff_task(
        blocker["id"],
        2,
        "approve",
        "Synthetic human verdict",
        attempt["id"],
        expected_attempt_revision=2,
    )
    now = detail(store, dependent["id"])
    assert now["revision"] == 2 and now["queue_workstream_id"]
    assert now["prerequisites"] == [
        {**ref, "state": "done", "complete": True, "satisfied": True, "blocking": False}
    ]
    assert store.get_next_action(ws)["task"]["id"] == dependent["id"]
    assert now["attempts"] == []
    if remote:
        assert [row["id"] for row in queue] == [dependent["id"]]
        exported = store.export_workstream(ws)["content"]
        assert blocker["id"] in exported and "Satisfied: Canonical blocker" in exported
        assert blocker["body"] not in exported and "Remote evidence stays remote" not in exported


@pytest.mark.parametrize("gate", ["unaccepted", "unresolved", "deferred", "dropped"])
def test_remote_noncompletion_gates_remain_blocking(context, gate):
    store, (a, b, _) = context
    dependent = create(store, a, "Dependent")
    blocker = create(store, b, "Blocker")
    store.add_prerequisite(dependent["id"], 1, blocker["id"])
    if gate == "unaccepted":
        store.unqueue_task(blocker["id"], 1)
    elif gate == "unresolved":
        store.add_unresolved(blocker["id"], 1, "A material open question")
    else:
        store.set_disposition(blocker["id"], 1, gate, "Actual synthetic decision")
    ref = detail(store, dependent["id"])["prerequisites"][0]
    assert ref["state"] == (gate if gate in {"deferred", "dropped"} else "open")
    assert ref["blocking"] and not ref["complete"]
    assert store.get_next_action(a["workstream"]["id"])["task"] is None


def test_observer_proposal_remote_acceptance_authority_revisions_and_audit(context):
    store, (a, b, _) = context
    dependent = create(store, a, "Dependent")
    blocker = create(store, b, "Remote blocker")
    proposal = store.add_prerequisite(dependent["id"], 1, blocker["id"], handling="observer")
    pending = detail(store, dependent["id"])
    assert pending["revision"] == 1 and pending["prerequisites"] == []
    assert store.get_next_action(a["workstream"]["id"])["task"]["id"] == dependent["id"]
    store.add_unresolved(dependent["id"], 1, "Settled separately")
    with pytest.raises(TaskError, match="revision_conflict"):
        store.accept_gate_proposal(proposal["id"], 1)
    assert detail(store, dependent["id"])["gate_proposals"][0]["id"] == proposal["id"]
    accepted = store.accept_gate_proposal(proposal["id"], 2)
    assert accepted["revision"] == 3 and accepted["queue_workstream_id"]
    assert accepted["spec_revision"] == 1 and accepted["gate_proposals"] == []
    assert accepted["prerequisites"][0]["project_id"] == b["project"]["id"]
    with pytest.raises(TaskError, match="invalid_handling"):
        store.add_prerequisite(dependent["id"], 3, blocker["id"], handling="untrusted")
    assert detail(store, dependent["id"])["revision"] == 3
    with sqlite3.connect(store.path) as db:
        events = db.execute(
            "SELECT action,outcome,request_json FROM events WHERE task_id=?", (dependent["id"],)
        ).fetchall()
    assert any(
        action == "gate.proposal_accepted" and outcome == "ok" and proposal["id"] in data
        for action, outcome, data in events
    )
    other = create(store, a, "Other")
    dropped = store.add_prerequisite(other["id"], 1, blocker["id"], handling="observer")
    store.set_disposition(blocker["id"], 1, "dropped", "Stopped")
    with pytest.raises(TaskError, match="invalid_prerequisite"):
        store.accept_gate_proposal(dropped["id"], 1)
    assert detail(store, other["id"])["gate_proposals"][0]["id"] == dropped["id"]
    assert detail(store, other["id"])["prerequisites"] == []


def test_cross_project_cycles_self_edges_and_group_membership_fail_atomically(context):
    store, (a, b, c) = context
    alpha, beta, gamma = [create(store, p, p["project"]["name"]) for p in (a, b, c)]
    store.add_prerequisite(alpha["id"], 1, beta["id"], handling="user")
    store.add_prerequisite(beta["id"], 1, gamma["id"])
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.add_prerequisite(gamma["id"], 1, alpha["id"])
    assert detail(store, gamma["id"])["revision"] == 1
    assert detail(store, gamma["id"])["prerequisites"] == []
    with pytest.raises(TaskError, match="invalid_prerequisite"):
        store.add_prerequisite(alpha["id"], 2, alpha["id"])
    group = store.create_group(c["workstream"]["id"], "Global group")
    store.add_group_member(group["id"], 1, alpha["id"], 2)
    proposal = store.add_prerequisite(gamma["id"], 1, group["id"], handling="observer")
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.accept_gate_proposal(proposal["id"], 1)
    assert detail(store, gamma["id"])["gate_proposals"][0]["id"] == proposal["id"]
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.add_prerequisite(gamma["id"], 1, group["id"])
    outsider = create(store, b, "Would-be member")
    linked = store.add_prerequisite(outsider["id"], 1, group["id"])
    group_before = detail(store, group["id"])
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.add_group_member(group["id"], group_before["revision"], outsider["id"], 2)
    assert detail(store, group["id"])["revision"] == group_before["revision"]
    assert detail(store, outsider["id"])["parent_group_id"] is None
    assert detail(store, outsider["id"])["revision"] == linked["revision"]


def test_decomposition_inherits_remote_edges_and_checks_group_completion_graph(context):
    store, (a, b, c) = context
    parent = create(store, a, "Parent")
    blocker = create(store, b, "Remote foundation")
    downstream = create(store, c, "Downstream")
    linked = store.add_prerequisite(parent["id"], 1, blocker["id"])
    store.add_prerequisite(downstream["id"], 1, parent["id"])
    group = store.decompose_task(
        parent["id"], linked["revision"], [{"title": "First"}, {"title": "Second"}]
    )
    assert group["prerequisites"] == [] and not group["complete"]
    for member in group["members"]:
        child = detail(store, member)
        assert child["prerequisites"][0]["project_id"] == b["project"]["id"]
        assert (
            child["blocked_by"] == [blocker["id"]]
            and child["queue_workstream_id"] == a["workstream"]["id"]
        )
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.add_prerequisite(blocker["id"], 1, downstream["id"])
    assert detail(store, blocker["id"])["revision"] == 1
    signoff(store, blocker, b)
    assert not detail(store, downstream["id"])["prerequisites"][0]["complete"]
    for index, identity in enumerate(group["members"]):
        store.queue_task(identity, a["workstream"]["id"], 1)
        signoff(store, detail(store, identity), a)
        ref = detail(store, downstream["id"])["prerequisites"][0]
        assert ref["project_id"] is None and ref["project_name"] is None
        assert ref["complete"] == (index == 1)
        assert ref["blocking"] == (index == 0)


def test_decomposition_rejects_cyclic_legacy_edges_without_partial_members_or_scope(context):
    store, (a, b, _) = context
    parent = create(store, a, "Parent")
    blocker = create(store, b, "Blocker")
    store.add_prerequisite(parent["id"], 1, blocker["id"])
    # Simulate a preexisting invalid edge, unavailable through the guarded API.
    with sqlite3.connect(store.path) as db:
        db.execute(
            "INSERT INTO prerequisites (task_id,blocked_by_id) VALUES (?, ?)",
            (blocker["id"], parent["id"]),
        )
        before = {
            table: db.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("tasks", "prerequisites", "projects", "scope_groups", "workstreams")
        }
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.decompose_task(parent["id"], 2, [{"title": "New member"}])
    with sqlite3.connect(store.path) as db:
        assert before == {
            table: db.execute(f"SELECT * FROM {table}").fetchall() for table in before
        }


def test_global_edges_and_audit_survive_reopen_without_schema_change(context):
    store, (a, b, _) = context
    parent, blocker = create(store, a, "Parent"), create(store, b, "Blocker")
    store.add_prerequisite(parent["id"], 1, blocker["id"])
    with sqlite3.connect(store.path) as db:
        schema = db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
        prior = {
            table: db.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("tasks", "prerequisites", "events")
        }
        assert db.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_REVISION
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    restored = Store(store.path, actor="resumed coordinator")
    with sqlite3.connect(store.path) as db:
        assert schema == db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
        assert prior == {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in prior}
    assert detail(restored, parent["id"])["prerequisites"][0]["id"] == blocker["id"]


def review_result(store, task_id, owner, human=False):
    task = detail(store, task_id)
    attempt = store.record_result(
        task_id,
        owner["workstream"]["id"],
        task["revision"],
        "builder",
        "Durable result",
        "Actual synthetic proof",
        artifacts=[{"kind": "artifact", "reference": "tests/test_cross_project_prerequisites.py"}],
        verification="Checked synthetic fixture",
        specification_etag=task["specification_etag"],
    )
    if human:
        return store.human_review(attempt["id"], 1, "Actual synthetic human review")
    return store.record_review(attempt["id"], 1, "fresh reviewer", "pass", "Checked proof")


def prerequisite(store, task_id):
    return detail(store, task_id)["prerequisites"][0]


@pytest.mark.parametrize("human", [False, True], ids=["independent", "human"])
def test_current_spec_review_is_reversible_and_keeps_dependent_results(context, human):
    store, (a, b, _) = context
    dependent = create(store, a, "Dependent")
    blocker = create(store, b, "Remote blocker")
    store.add_prerequisite(dependent["id"], 1, blocker["id"])
    attempt = review_result(store, blocker["id"], b, human)
    assert prerequisite(store, dependent["id"])["satisfied"]
    assert not prerequisite(store, dependent["id"])["complete"]
    ws = a["workstream"]["id"]
    assert store.get_next_action(ws)["task"]["id"] == dependent["id"]
    saved = review_result(store, dependent["id"], a)
    # The real human rework verdict invalidates the previously satisfying attempt.
    store.signoff_task(
        blocker["id"],
        2,
        "rework",
        "Synthetic actual rework verdict",
        attempt["id"],
        expected_attempt_revision=2,
    )
    assert prerequisite(store, dependent["id"])["blocking"]
    assert store.get_next_action(ws)["action"] is None
    assert detail(store, dependent["id"])["attempts"][0]["id"] == saved["id"]
    assert store.workstream_status(ws)["items"][0]["gate_diagnostics"] == [
        "prerequisites",
        "signoff",
    ]
    fresh = review_result(store, blocker["id"], b)
    assert prerequisite(store, dependent["id"])["satisfied"]
    changed = store.update_task(
        blocker["id"],
        detail(store, blocker["id"])["revision"],
        {"body": "Changed complete requirements"},
        specification_etag=detail(store, blocker["id"])["specification_etag"],
    )
    assert changed["spec_revision"] == 2
    assert prerequisite(store, dependent["id"])["blocking"]
    assert store.get_next_action(ws)["action"] is None
    assert store.get_attempt(fresh["id"])["state"] == "passed"
    review_result(store, blocker["id"], b)
    assert prerequisite(store, dependent["id"])["satisfied"]
    assert detail(store, dependent["id"])["attempts"][0]["id"] == saved["id"]


@pytest.mark.parametrize("disposition", ["dropped", "deferred"])
def test_retained_passed_review_does_not_satisfy_inactive_blocker(context, disposition):
    store, (a, b, _) = context
    dependent, blocker = create(store, a, "Dependent"), create(store, b, "Blocker")
    store.add_prerequisite(dependent["id"], 1, blocker["id"])
    attempt = review_result(store, blocker["id"], b)
    assert prerequisite(store, dependent["id"])["satisfied"]
    store.set_disposition(blocker["id"], 2, disposition, "Actual synthetic pause/drop")
    assert prerequisite(store, dependent["id"])["blocking"]
    assert store.get_attempt(attempt["id"])["state"] == "passed"
    assert store.get_next_action(a["workstream"]["id"])["action"] is None


@pytest.mark.parametrize("milestone", ["review", "signoff"])
def test_nonempty_group_uses_each_members_required_milestone(context, milestone):
    store, (a, b, c) = context
    group = store.create_group(a["workstream"]["id"], "Shared milestone")
    first, second = create(store, b, "First"), create(store, c, "Second")
    store.add_group_member(group["id"], 1, first["id"], 1)
    store.add_group_member(group["id"], 2, second["id"], 1)
    dependent = create(store, a, "Dependent")
    store.add_prerequisite(dependent["id"], 1, group["id"], milestone=milestone)
    first_review = review_result(store, first["id"], b)
    assert prerequisite(store, dependent["id"])["blocking"]
    second_review = review_result(store, second["id"], c, human=True)
    ref = prerequisite(store, dependent["id"])
    assert ref["milestone"] == milestone and not ref["complete"]
    assert ref["satisfied"] == (milestone == "review")
    assert detail(store, group["id"])["complete"] is False
    if milestone == "review":
        assert store.get_next_action(a["workstream"]["id"])["task"]["id"] == dependent["id"]
        store.set_disposition(second["id"], 3, "deferred", "Synthetic pause")
        assert prerequisite(store, dependent["id"])["blocking"]
        store.set_disposition(second["id"], 4, "open", "Synthetic resume")
        assert prerequisite(store, dependent["id"])["satisfied"]
    for task, attempt in ((first, first_review), (second, second_review)):
        store.signoff_task(
            task["id"],
            detail(store, task["id"])["revision"],
            "approve",
            "Synthetic actual approval",
            attempt["id"],
            expected_attempt_revision=2,
        )
    assert prerequisite(store, dependent["id"])["satisfied"]
    assert prerequisite(store, dependent["id"])["complete"]


@pytest.mark.parametrize("milestone", ["review", "signoff"])
def test_proposals_and_decomposition_preserve_milestone(context, milestone):
    store, (a, b, _) = context
    blocker = create(store, b, "Remote blocker")
    parent = create(store, a, "Parent")
    observer = store.add_prerequisite(
        parent["id"], 1, blocker["id"], handling="observer", milestone=milestone
    )
    assert observer["milestone"] == milestone
    assert detail(store, parent["id"])["gate_proposals"][0]["milestone"] == milestone
    accepted = store.accept_gate_proposal(observer["id"], 1)
    assert prerequisite(store, parent["id"])["milestone"] == milestone
    assert not store.add_prerequisite(parent["id"], 2, blocker["id"], milestone=milestone)[
        "changed"
    ]
    opposite = "signoff" if milestone == "review" else "review"
    with pytest.raises(TaskError, match="prerequisite_milestone_conflict"):
        store.add_prerequisite(parent["id"], 2, blocker["id"], milestone=opposite)
    assert detail(store, parent["id"])["revision"] == 2
    group = store.decompose_task(parent["id"], accepted["revision"], [{"title": "Child"}])
    child = group["members"][0]
    assert prerequisite(store, child)["milestone"] == milestone
    proposed = store.propose_prerequisite(
        child, 1, "Needed work", workstream_id=a["workstream"]["id"], milestone=milestone
    )
    assert {ref["milestone"] for ref in proposed["task"]["prerequisites"]} == {milestone}
    assert proposed["proposal"]["queue_workstream_id"] == a["workstream"]["id"]


@pytest.mark.parametrize("value", ["done", None, [], 1])
def test_invalid_milestone_fails_before_creating_or_linking_work(context, value):
    store, (a, b, _) = context
    dependent, blocker = create(store, a, "Dependent"), create(store, b, "Blocker")
    before = store.list_tasks(a["project"]["id"])["total"]
    with pytest.raises(TaskError, match="invalid_prerequisite_milestone"):
        store.add_prerequisite(dependent["id"], 1, blocker["id"], milestone=value)
    with pytest.raises(TaskError, match="invalid_prerequisite_milestone"):
        store.propose_prerequisite(dependent["id"], 1, "Needed", milestone=value)
    assert detail(store, dependent["id"])["revision"] == 1
    assert store.list_tasks(a["project"]["id"])["total"] == before
