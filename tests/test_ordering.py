"""Independent workstream lists preserve shared specifications and local proof."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from task_mcp.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="actual coordinator label")
    setup = store.init(str(tmp_path / "repo"), branch="a", action="create_project", confirmed=True)
    project, a = setup["project"]["id"], setup["workstream"]["id"]
    b = store.init_workstream(project, str(tmp_path / "repo"), branch="b", confirmed=True)[
        "workstream"
    ]["id"]
    return store, project, a, b, tmp_path


def create(context, title, shared=True, **kwargs):
    store, project, a, b, _ = context
    task = store.create_task(
        project, title, f"Full {title} requirements", f"Verify {title}", workstream_id=a, **kwargs
    )
    if shared:
        task = store.add_to_workstream(task["id"], b, task["revision"])
    return task


def board(context, ws=None, **kwargs):
    return context[0].list_tasks(context[1], workstream_id=ws or context[2], **kwargs)


def order(context, ws=None):
    return [task["id"] for task in board(context, ws)["items"]]


def move(context, task, anchor, position="before", revision=None, ws=None):
    identity = ws or context[2]
    current = order(context, identity)
    requested = [i for i in current if i != task["id"]]
    if task["id"] not in current or anchor["id"] not in requested:
        requested = [task["id"], anchor["id"]]
    else:
        requested.insert(requested.index(anchor["id"]) + (position == "after"), task["id"])
    return context[0].reorder_tasks(
        identity,
        requested,
        board(context, identity)["workstream_order_revision"] if revision is None else revision,
    )


def business_rows(store):
    with closing(sqlite3.connect(store.path)) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in (
                "projects",
                "tasks",
                "attempts",
                "workstreams",
                "scope_members",
                "scope_groups",
                "scope_exclusions",
                "workstream_task_order",
            )
        }


def test_independent_lists_completed_metadata_and_consistent_consumers(context):
    store, project, a, b, _ = context
    done, first, last = [create(context, title) for title in ("Done", "First", "Last")]
    attempt = store.record_result(
        done["id"],
        a,
        done["revision"],
        "builder",
        "Actual result",
        "Exact proof",
        artifacts=[{"kind": "artifact", "reference": __file__}],
        verification="Exact proof",
        specification_etag=store.get_tasks([done["id"]])["items"][0]["specification_etag"],
    )
    store.record_review(attempt["id"], 1, "independent reviewer", "pass", "Reviewed proof")
    store.signoff_task(
        done["id"],
        attempt["task_revision"],
        "approve",
        "Synthetic human approval",
        attempt["id"],
        2,
    )
    b_attempt = store.record_result(
        first["id"],
        b,
        first["revision"],
        "B builder",
        "B result",
        "B proof",
        artifacts=[{"kind": "artifact", "reference": __file__}],
        verification="B proof",
        specification_etag=store.get_tasks([first["id"]])["items"][0]["specification_etag"],
    )
    before = business_rows(store)
    b_board = board(context, b)
    revision = board(context)["workstream_order_revision"]
    ack = move(context, last, done)
    assert ack == {
        "project_id": project,
        "workstream_id": a,
        "supplied_count": 3,
        "total": 3,
        "workstream_order_revision": revision + 1,
        "changed": True,
    }
    assert order(context) == [last["id"], done["id"], first["id"]]
    assert board(context, b) == b_board
    assert store.get_next_action(a)["task"]["id"] == last["id"]
    assert store.get_next_action(b)["action"] == "review"
    assert store.get_next_action(b)["attempt"]["id"] == b_attempt["id"]
    status = store.workstream_status(a, limit=1)
    assert status["items"][0]["id"] == last["id"]
    assert status["workstream_order_revision"] == ack["workstream_order_revision"]
    assert board(context, limit=1, offset=1)["items"][0]["id"] == done["id"]
    for format in ("markdown", "legacy"):
        export = store.export_workstream(a, format=format)
        assert export["workstream_order_revision"] == ack["workstream_order_revision"]
        content = export["content"]
        assert (
            content.index("Last")
            < content.index("Done", content.index("Last"))
            < content.index("First", content.index("Done", content.index("Last")))
        )
    baseline = store.list_tasks(project)
    assert baseline["ordering"] == "project_baseline"
    assert "workstream_order_revision" not in baseline and "project_order_revision" not in baseline
    assert [t["id"] for t in baseline["items"]] == [done["id"], first["id"], last["id"]]
    after = business_rows(store)
    for table in (
        "projects",
        "tasks",
        "attempts",
        "scope_members",
        "scope_groups",
        "scope_exclusions",
    ):
        assert after[table] == before[table]
    move(context, done, first, "after")
    assert order(context) == [last["id"], first["id"], done["id"]]
    assert business_rows(store)["tasks"] == before["tasks"]
    event = next(
        e
        for e in store.list_events(project, include_details=True, limit=100)["items"]
        if e["action"] == "tasks.reordered"
    )
    assert event["actor"] == "actual coordinator label"
    assert event["request"]["workstream_id"] == a
    assert event["request"]["task_ids"] == [last["id"], done["id"], first["id"]]


def test_noop_reads_and_tokens_do_not_normalize_or_advance(context):
    store, project, a, _, tmp_path = context
    first, second = create(context, "First"), create(context, "Second")
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("UPDATE workstream_task_order SET order_key=order_key*10")
        db.commit()
    before = business_rows(store)
    revision = board(context)["workstream_order_revision"]
    assert not move(context, first, second)["changed"]
    assert not move(context, second, first, "after")["changed"]
    for response in (
        board(context),
        store.workstream_status(a),
        store.init(str(tmp_path / "repo"), branch="a"),
        store.get_next_action(a),
        store.export_workstream(a),
    ):
        assert response["workstream_order_revision"] == revision
    assert business_rows(store) == before
    assert Store(store.path).list_tasks(project, a)["workstream_order_revision"] == revision


def test_membership_remove_readd_and_dynamic_groups_append_without_snapshot(context):
    store, project, a, b, tmp_path = context
    first, second, third = [create(context, title) for title in ("First", "Second", "Third")]
    move(context, third, first)
    rev_b = board(context, b)["workstream_order_revision"]
    removed = store.remove_from_workstream(first["id"], a, first["revision"])
    assert order(context) == [third["id"], second["id"]]
    store.add_to_workstream(first["id"], a, removed["revision"])
    assert order(context) == [third["id"], second["id"], first["id"]]
    assert board(context, b)["workstream_order_revision"] == rev_b
    group = store.create_group(a, "Dynamic group")
    fourth = store.create_task(
        project, "Fourth", group_id=group["id"], group_expected_revision=group["revision"]
    )
    assert order(context)[-1] == fourth["id"] and fourth["id"] not in order(context, b)
    # Copy the expression, not effective members or source order.
    c = store.init_workstream(
        project, str(tmp_path / "repo"), branch="c", scope_expression=f"{a}", confirmed=True
    )["workstream"]["id"]
    assert order(context, c) == [first["id"], second["id"], third["id"], fourth["id"]]
    fifth = store.create_task(project, "Fifth")
    current_group = store.get_tasks([group["id"]])["items"][0]
    store.add_group_member(group["id"], current_group["revision"], fifth["id"], fifth["revision"])
    assert order(context)[-1] == fifth["id"] and order(context, c)[-1] == fifth["id"]
    assert fifth["id"] not in order(context, b)
    current_a = store.workstream_status(a)["workstream"]
    # Two old baseline tasks reenter together: deterministic baseline append.
    store.set_scope(a, current_a["revision"], f"none +{group['id']}")
    assert order(context) == [fourth["id"], fifth["id"]]
    current_a = store.workstream_status(a)["workstream"]
    store.set_scope(a, current_a["revision"], f"{a} +{third['id']} +{first['id']}")
    assert order(context) == [fourth["id"], fifth["id"], first["id"], third["id"]]
    current_a = store.workstream_status(a)["workstream"]
    store.set_scope(a, current_a["revision"], f"{a} -{group['id']}")
    assert order(context) == [first["id"], third["id"]]
    assert order(context, c)[-2:] == [fourth["id"], fifth["id"]]


def test_insertion_decomposition_and_ordered_action_gates(context):
    store, project, a, b, _ = context
    first, second = create(context, "First"), create(context, "Second")
    move(context, second, first)
    revision = board(context)["workstream_order_revision"]
    third = create(context, "Third")
    assert order(context) == [second["id"], first["id"], third["id"]]
    assert board(context)["workstream_order_revision"] == revision + 1
    with pytest.raises(TaskError, match="revision_conflict"):
        move(context, first, second, revision=revision)
    first = store.get_tasks([first["id"]])["items"][0]
    revision = board(context)["workstream_order_revision"]
    group = store.decompose_task(
        first["id"], first["revision"], [{"title": "Member A"}, {"title": "Member B"}]
    )
    assert order(context) == [second["id"], third["id"], *group["members"]]
    assert board(context)["workstream_order_revision"] == revision + 1
    assert order(context, b) == [second["id"], third["id"], *group["members"]]
    second = store.get_tasks([second["id"]])["items"][0]
    store.add_unresolved(second["id"], second["revision"], "Unresolved decision")
    assert store.get_next_action(a)["task"]["id"] == third["id"]
    third = store.get_tasks([third["id"]])["items"][0]
    store.add_prerequisite(third["id"], third["revision"], second["id"])
    assert store.get_next_action(a)["task"]["id"] == group["members"][0]
    pending = store.create_task(project, "Inbox")
    with pytest.raises(TaskError, match="included concrete"):
        move(context, pending, third)


def test_invalid_stale_cross_scope_and_audit_failures_are_atomic(context, monkeypatch):
    store, project, a, b, tmp_path = context
    first, second = create(context, "First"), create(context, "Second")
    only_b = store.create_task(project, "Only B", workstream_id=b)
    remote = store.init(
        str(tmp_path / "remote"), branch="main", action="create_project", confirmed=True
    )
    remote_task = store.create_task(remote["project"]["id"], "Remote")
    group = store.create_group(a, "Group")
    before = business_rows(store)
    revision = board(context)["workstream_order_revision"]
    invalid = [
        ([first["id"], first["id"]], revision),
        (["missing"], revision),
        ([remote_task["id"]], revision),
        ([group["id"]], revision),
        ([only_b["id"]], revision),
        ([second["id"]], revision - 1),
        ([second["id"]], True),
        (None, revision),
        ("not a list", revision),
        ([None], revision),
        ([""], revision),
        ([[]], revision),
    ]
    for task_ids, token in invalid:
        with pytest.raises(TaskError):
            store.reorder_tasks(a, task_ids, token)
        assert business_rows(store) == before

    def fail(*args, **kwargs):
        raise RuntimeError("Injected audit failure")

    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError, match="Injected audit failure"):
        move(context, second, first)
    assert business_rows(store) == before
    with pytest.raises(RuntimeError, match="Injected audit failure"):
        store.remove_from_workstream(first["id"], a, first["revision"])
    assert business_rows(store) == before


def test_concurrent_moves_one_winner_and_other_workstream_independent(context):
    store, _, a, b, _ = context
    first, second, third = [create(context, title) for title in ("First", "Second", "Third")]
    revision = board(context)["workstream_order_revision"]

    def run(identity):
        try:
            return Store(store.path).reorder_tasks(a, [identity], revision)
        except TaskError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [second["id"], third["id"]]))
    assert sum(isinstance(r, dict) and r["changed"] for r in results) == 1
    assert sum(isinstance(r, str) and "revision_conflict" in r for r in results) == 1
    assert board(context)["workstream_order_revision"] == revision + 1
    assert board(context, b)["workstream_order_revision"] == revision
    assert move(context, third, first, ws=b, revision=revision)["changed"]


def test_partial_prefix_retains_all_unlisted_members_and_empty_noops(context):
    store, _, a, b, _ = context
    tasks = [create(context, title) for title in ("First", "Second", "Third", "Fourth")]
    first, second, third, fourth = [t["id"] for t in tasks]
    token = board(context)["workstream_order_revision"]
    ack = store.compact_call("reorder_tasks", a, [fourth, second], token)
    assert ack["changed"] and ack["supplied_count"] == 2 and ack["total"] == 4
    assert not {"task_ids", "items", "ordered_ids", "task_id", "anchor_id"} & ack.keys()
    assert order(context) == [fourth, second, first, third]
    assert order(context, b) == [first, second, third, fourth]
    before = business_rows(store)
    for prefix in ([], [fourth], [fourth, second], [fourth, second, first, third]):
        noop = store.reorder_tasks(a, prefix, ack["workstream_order_revision"])
        assert (
            not noop["changed"]
            and noop["workstream_order_revision"] == ack["workstream_order_revision"]
        )
        assert business_rows(store) == before


def test_own_membership_writes_return_direct_reorder_continuation_tokens(context):
    store, project, a, b, _ = context
    first = store.compact_call("create_task", project, "First", workstream_id=a)
    assert not store.reorder_tasks(a, [first["id"]], first["workstream_order_revision"])["changed"]
    second = store.compact_call("create_task", project, "Second", workstream_id=a)
    moved = store.reorder_tasks(a, [second["id"]], second["workstream_order_revision"])
    assert moved["changed"]
    added = store.compact_call("add_to_workstream", second["id"], b, second["revision"])
    assert not store.reorder_tasks(b, [second["id"]], added["workstream_order_revision"])["changed"]
    removed = store.compact_call("remove_from_workstream", second["id"], a, added["revision"])
    assert not store.reorder_tasks(a, [first["id"]], removed["workstream_order_revision"])[
        "changed"
    ]
    scoped = store.compact_call(
        "set_scope", a, removed["workstream_revision"], f"none +{first['id']} +{second['id']}"
    )
    assert store.reorder_tasks(a, [second["id"]], scoped["workstream_order_revision"])["changed"]
