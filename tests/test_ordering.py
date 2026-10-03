"""Shared scheduling is independent of task requirements and branch-local delivery."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="actual coordinator label")
    setup = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    return store, setup["project"]["id"], setup["workstream"]["id"], tmp_path


def create(context, title, **kwargs):
    store, project, ws, _ = context
    return store.create_task(
        project,
        title,
        f"Full {title} requirements",
        f"Verify {title}",
        workstream_id=ws,
        **kwargs,
    )


def board(context, **kwargs):
    return context[0].list_tasks(context[1], **kwargs)


def order(context):
    return [task["id"] for task in board(context)["items"]]


def move(context, task, anchor, position="before", revision=None, **kwargs):
    return context[0].reorder_tasks(
        context[1],
        task["id"],
        anchor["id"],
        position,
        board(context)["project_order_revision"] if revision is None else revision,
        kwargs.pop("instruction", "User asked to schedule this task here"),
        **kwargs,
    )


def business_rows(store):
    with closing(sqlite3.connect(store.path)) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("projects", "tasks", "attempts", "workstreams", "scope_members")
        }


def test_moves_shift_completed_positions_without_mutating_requirements_or_proof(context):
    store, project, ws, _ = context
    done = create(context, "Done")
    attempt = store.record_result(
        done["id"],
        ws,
        1,
        "builder",
        "Actual result",
        "Exact proof",
        artifacts=[{"kind": "artifact", "reference": "tests/test_ordering.py"}],
        verification="Exact proof",
        specification_etag=store.get_tasks([done["id"]])["items"][0]["specification_etag"],
    )
    store.record_review(attempt["id"], 1, "independent reviewer", "pass", "Reviewed proof")
    store.signoff_task(done["id"], 2, "approve", "Synthetic human approval", attempt["id"], 2)
    first = create(context, "Older remaining work")
    prioritized = create(context, "Prioritize")
    before = store.get_tasks([done["id"], first["id"], prioritized["id"]])["items"]
    events = store.list_events(project, task_id=done["id"], include_details=True)["items"]
    revision = board(context)["project_order_revision"]
    ack = move(context, prioritized, done)
    assert ack == {
        "project_id": project,
        "task_id": prioritized["id"],
        "anchor_id": done["id"],
        "project_order_revision": revision + 1,
        "changed": True,
    }
    assert order(context) == [prioritized["id"], done["id"], first["id"]]
    after = store.get_tasks([done["id"], first["id"], prioritized["id"]])["items"]
    assert [{k: v for k, v in item.items() if k != "order_key"} for item in after] == [
        {k: v for k, v in item.items() if k != "order_key"} for item in before
    ]
    assert after[0]["order_key"] == 2
    assert store.get_next_action(ws)["task"]["id"] == prioritized["id"]
    later_events = store.list_events(project, task_id=done["id"], include_details=True)["items"]
    assert later_events[: len(events)] == events
    event = next(
        e
        for e in store.list_events(project, include_details=True)["items"]
        if e["action"] == "tasks.reordered"
    )
    assert event["actor"] == "actual coordinator label"
    assert event["request"]["instruction"] == "User asked to schedule this task here"
    assert not {"ordered_ids", "expected_order"} & event["request"].keys()
    # Completed rows can themselves be anchors or scheduling metadata subjects.
    move(context, done, first, "after")
    assert order(context) == [prioritized["id"], first["id"], done["id"]]
    assert store.get_tasks([done["id"]])["items"][0]["attempts"] == before[0]["attempts"]


def test_noop_and_reads_never_normalize_or_advance_order(context):
    store, _, ws, tmp_path = context
    first, second = create(context, "First"), create(context, "Second")
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("UPDATE tasks SET order_key=order_key*10")
        db.commit()
    before = business_rows(store)
    revision = board(context)["project_order_revision"]
    assert not move(context, first, second)["changed"]
    assert not move(context, second, first, "after")["changed"]
    assert board(context)["project_order_revision"] == revision
    assert store.workstream_status(ws)["project_order_revision"] == revision
    assert store.init(str(tmp_path / "repo"), branch="main")["project_order_revision"] == revision
    assert store.get_next_action(ws)["project_order_revision"] == revision
    store.export_workstream(ws)
    assert business_rows(store) == before
    assert Store(store.path).list_tasks(context[1])["project_order_revision"] == revision


def test_insertion_and_decomposition_change_order_revision_and_append(context):
    store, project, _, _ = context
    assert board(context)["project_order_revision"] == 0
    first = create(context, "First")
    assert board(context)["project_order_revision"] == 1
    second = create(context, "Second")
    move(context, second, first)
    revision = board(context)["project_order_revision"]
    third = create(context, "Third")
    assert order(context) == [second["id"], first["id"], third["id"]]
    assert board(context)["project_order_revision"] == revision + 1
    with pytest.raises(TaskError, match="revision_conflict"):
        move(context, first, second, revision=revision)
    revision = board(context)["project_order_revision"]
    group = store.decompose_task(first["id"], 1, [{"title": "Member A"}, {"title": "Member B"}])
    assert order(context) == [second["id"], third["id"], *group["members"]]
    assert board(context)["project_order_revision"] == revision + 3
    store.create_group(context[2], "Only context")
    assert board(context)["project_order_revision"] == revision + 3
    assert board(context)["project_id"] == project


def test_invalid_anchors_and_stale_moves_are_atomic(context):
    store, project, ws, tmp_path = context
    first, second = create(context, "First"), create(context, "Second")
    remote = store.init(
        str(tmp_path / "remote"), branch="main", action="create_project", confirmed=True
    )
    remote_task = store.create_task(remote["project"]["id"], "Remote")
    group = store.create_group(ws, "Group")
    before = business_rows(store)
    revision = board(context)["project_order_revision"]
    invalid = [
        (first["id"], first["id"], "before", revision, "Decision"),
        (first["id"], "missing", "before", revision, "Decision"),
        (first["id"], remote_task["id"], "before", revision, "Decision"),
        (remote_task["id"], first["id"], "before", revision, "Decision"),
        (first["id"], group["id"], "before", revision, "Decision"),
        (first["id"], second["id"], "sideways", revision, "Decision"),
        (first["id"], second["id"], "before", revision, "  "),
        (first["id"], second["id"], "before", revision - 1, "Decision"),
        (first["id"], second["id"], "before", True, "Decision"),
    ]
    for arguments in invalid:
        with pytest.raises(TaskError):
            store.reorder_tasks(project, *arguments)
        assert business_rows(store) == before


def test_concurrent_moves_have_one_winner(context):
    store, project, _, _ = context
    first, second, third = [create(context, title) for title in ("First", "Second", "Third")]
    revision = board(context)["project_order_revision"]

    def run(identity):
        try:
            return Store(store.path).reorder_tasks(
                project,
                identity,
                first["id"],
                "before",
                revision,
                "Actual synthetic scheduling decision",
            )
        except TaskError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [second["id"], third["id"]]))
    assert sum(isinstance(result, dict) and result["changed"] for result in results) == 1
    assert sum(isinstance(result, str) and "revision_conflict" in result for result in results) == 1
    assert board(context)["project_order_revision"] == revision + 1


def test_scope_filtering_and_branch_local_gates_follow_shared_order(context):
    store, project, ws, tmp_path = context
    first, second, third = [create(context, title) for title in ("First", "Second", "Third")]
    group = store.create_group(ws, "Shared context")
    store.add_group_member(group["id"], group["revision"], second["id"], 1)
    alt = store.init_workstream(
        project, str(tmp_path / "repo"), branch="alternative", confirmed=True
    )
    other_ws = alt["workstream"]["id"]
    store.set_scope(other_ws, alt["workstream"]["revision"], f"none +{group['id']} +{third['id']}")
    current_ws = next(row for row in store.list_workstreams(project)["items"] if row["id"] == ws)
    store.set_scope(
        ws,
        current_ws["revision"],
        f"none +{first['id']}",
    )
    move(context, third, first)
    assert [row["id"] for row in board(context, workstream_id=other_ws)["items"]] == [
        third["id"],
        second["id"],
    ]
    assert [row["id"] for row in board(context, workstream_id=ws)["items"]] == [first["id"]]
    store.record_result(
        third["id"],
        ws,
        store.get_tasks([third["id"]])["items"][0]["revision"],
        "main builder",
        "Durable",
        "Proof",
        artifacts=[{"kind": "artifact", "reference": "tests/test_ordering.py"}],
        verification="Proof",
        specification_etag=store.get_tasks([third["id"]])["items"][0]["specification_etag"],
    )
    local = store.get_next_action(ws)
    assert local["action"] == "implement" and local["task"]["id"] == first["id"]
    assert store.get_next_action(other_ws)["task"]["id"] == third["id"]
    third = store.get_tasks([third["id"]])["items"][0]
    store.add_unresolved(third["id"], third["revision"], "Actual unresolved question")
    assert store.get_next_action(other_ws)["task"]["id"] == second["id"]
    second = store.get_tasks([second["id"]])["items"][0]
    store.add_prerequisite(second["id"], second["revision"], first["id"])
    assert store.get_next_action(other_ws)["task"] is None
    # Inbox tasks remain excluded even when first in the shared order.
    pending = store.create_task(project, "Pending")
    move(context, pending, third)
    assert store.get_next_action(ws)["task"]["id"] == first["id"]


def test_audit_failure_rolls_back_order_keys_and_revision(context, monkeypatch):
    store, _, _, _ = context
    first, second = create(context, "First"), create(context, "Second")
    revision = board(context)["project_order_revision"]
    before = business_rows(store)

    def fail(*args, **kwargs):
        raise RuntimeError("Injected audit failure")

    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError, match="Injected audit failure"):
        move(context, second, first, revision=revision)
    assert business_rows(store) == before
