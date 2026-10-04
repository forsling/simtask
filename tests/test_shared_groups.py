"""Shared groups retain local task ownership and a global aggregate view."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from task_mcp.store import SCHEMA, Store, TaskError, timestamp


def contexts(store, tmp_path):
    return [
        store.init(str(tmp_path / name), branch=branch, action="create_project", confirmed=True)
        for name, branch in (("alpha", "main"), ("beta", "feature"), ("gamma", "release"))
    ]


def local_task(store, context, title, group_id=None, group_revision=None):
    created = store.create_task(
        context["project"]["id"],
        title,
        source="user",
        user_request="Requested",
        workstream_id=context["workstream"]["id"],
        group_id=group_id,
        group_expected_revision=group_revision,
    )
    return store.get_tasks([created["id"]])["items"][0]


def finish(store, item, workstream_id):
    result = store.record_result(
        item["id"],
        workstream_id,
        item["revision"],
        "implementer",
        "Done",
        "Verified",
        artifacts=[{"kind": "artifact", "reference": "tests/test_shared_groups.py"}],
        verification="Verified",
        specification_etag=store.get_tasks([item["id"]])["items"][0]["specification_etag"],
    )
    store.record_review(result["id"], 1, "reviewer", "pass", "Checked")
    store.signoff_task(
        item["id"],
        item["revision"] + 1,
        "approve",
        "Approved",
        result["id"],
        expected_attempt_revision=2,
    )


def test_shared_group_three_projects_scope_and_independent_completion(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    a, b, c = contexts(store, tmp_path)
    group = store.create_group(a["workstream"]["id"], "Shared feature", "Whole feature context")
    group_id = group["id"]
    assert group["project_id"] is None and group["progress"]["total"] == 0
    assert not group["complete"]
    assert store.list_groups(a["project"]["id"])["items"][0]["id"] == group_id
    assert store.list_groups()["items"][0]["id"] == group_id
    for context in (b, c):
        store.set_scope(context["workstream"]["id"], 1, f"none +{group_id}")
        status = store.workstream_status(context["workstream"]["id"])["status"]
        assert status["scoped_count"] == 0
        assert status["referenced_groups"][0]["global_progress"]["total"] == 0
        assert not status["referenced_groups"][0]["complete"]
    members = []
    for context, title in ((a, "Alpha"), (b, "Beta"), (c, "Gamma")):
        revision = store.get_tasks([group_id])["items"][0]["revision"]
        members.append(local_task(store, context, title, group_id, revision))
    detail = store.get_tasks([group_id])["items"][0]
    assert detail["progress"] == {
        "total": 3,
        "done": 0,
        "remaining": 3,
        "by_project": {context["project"]["id"]: {"total": 1, "done": 0} for context in (a, b, c)},
    }
    assert {member["project_id"] for member in detail["member_details"]} == {
        context["project"]["id"] for context in (a, b, c)
    }
    for context, member in zip((a, b, c), members, strict=True):
        ws = context["workstream"]["id"]
        status = store.workstream_status(ws)
        assert status["status"]["scoped_count"] == 1
        assert sum(status["status"]["counts"].values()) == 1
        assert status["items"][0]["id"] == member["id"]
        assert status["status"]["referenced_groups"][0]["global_progress"]["total"] == 3
        assert store.get_next_action(ws)["task"]["id"] == member["id"]
        assert store.list_groups(context["project"]["id"])["items"][0]["id"] == group_id
    with pytest.raises(TaskError, match="unknown_workstream"):
        store.record_result(
            members[1]["id"],
            a["workstream"]["id"],
            1,
            "wrong",
            "Wrong",
            "Wrong",
            artifacts=[{"kind": "artifact", "reference": "tests/test_shared_groups.py"}],
            verification="Wrong",
            specification_etag=store.get_tasks([members[1]["id"]])["items"][0][
                "specification_etag"
            ],
        )
    dependent = local_task(store, a, "Wait for whole group")
    blocked = store.add_prerequisite(dependent["id"], 1, group_id)
    assert blocked["blocked_by"] == [group_id]
    current_ws_revision = store.list_workstreams(a["project"]["id"])["items"][0]["revision"]
    store.set_scope(
        a["workstream"]["id"], current_ws_revision, f"none +{group_id} +{dependent['id']}"
    )
    assert store.get_next_action(a["workstream"]["id"])["task"]["id"] == members[0]["id"]
    for context, member in zip((a, b), members[:2], strict=True):
        finish(store, member, context["workstream"]["id"])
    assert store.get_tasks([group_id])["items"][0]["progress"]["done"] == 2
    assert not store.get_tasks([group_id])["items"][0]["complete"]
    finish(store, members[2], c["workstream"]["id"])
    complete = store.get_tasks([group_id])["items"][0]
    assert complete["complete"] and complete["progress"]["done"] == 3
    with pytest.raises(TaskError, match="completed_task_immutable"):
        local_task(store, b, "Too late", group_id, complete["revision"])
    with pytest.raises(TaskError, match="completed_task_immutable"):
        store.update_task(group_id, complete["revision"], {"body": "Changed"})
    assert store.get_next_action(a["workstream"]["id"])["task"]["id"] == dependent["id"]


def test_shared_group_live_exclusion_membership_revision_and_cycle(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    a, b, _ = contexts(store, tmp_path)
    group = store.create_group(a["workstream"]["id"], "Shared")
    group_id = group["id"]
    ws_b = b["workstream"]["id"]
    store.set_scope(ws_b, 1, f"none +{group_id}")
    existing = local_task(store, b, "Existing")
    added = store.add_group_member(group_id, group["revision"], existing["id"], 1)
    assert added["member"]["parent_group_id"] == group_id
    assert store.get_next_action(ws_b)["task"]["id"] == existing["id"]
    other = local_task(store, b, "Other")
    with pytest.raises(TaskError, match="revision_conflict"):
        store.add_group_member(group_id, group["revision"], other["id"], 1)
    assert store.get_tasks([other["id"]])["items"][0]["parent_group_id"] is None
    store.set_scope(
        ws_b,
        store.workstream_status(ws_b)["workstream"]["revision"],
        f"none +{group_id} -{existing['id']}",
    )
    assert store.workstream_status(ws_b)["status"]["scoped_count"] == 0
    current = store.get_tasks([group_id])["items"][0]
    next_member = local_task(store, b, "Next", group_id, current["revision"])
    assert store.workstream_status(ws_b)["items"][0]["id"] == next_member["id"]
    assert (
        existing["id"]
        not in store.preflight(b["project"]["id"], str(tmp_path / "beta"), branch="feature")[
            "scope"
        ]
    )
    blocked = store.add_prerequisite(
        other["id"], store.get_tasks([other["id"]])["items"][0]["revision"], group_id
    )
    with pytest.raises(TaskError, match="prerequisite_cycle"):
        store.add_group_member(
            group_id,
            store.get_tasks([group_id])["items"][0]["revision"],
            other["id"],
            blocked["revision"],
        )
    assert store.get_tasks([other["id"]])["items"][0]["parent_group_id"] is None
    assert store.get_tasks([group_id])["items"][0]["progress"]["total"] == 2


def test_empty_group_blocks_prerequisite_and_remains_mutable(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    a, _, _ = contexts(store, tmp_path)
    group = store.create_group(a["workstream"]["id"], "Empty")
    task = local_task(store, a, "Blocked")
    store.add_prerequisite(task["id"], 1, group["id"])
    current_ws_revision = store.list_workstreams(a["project"]["id"])["items"][0]["revision"]
    store.set_scope(
        a["workstream"]["id"], current_ws_revision, f"none +{group['id']} +{task['id']}"
    )
    assert store.get_next_action(a["workstream"]["id"])["diagnostics"]["prerequisites"] == 1
    changed = store.update_task(
        group["id"], 1, {"body": "Still open"}, specification_etag=group["specification_etag"]
    )
    changed = store.get_tasks([changed["id"]])["items"][0]
    assert changed["body"] == "Still open" and not changed["complete"]


def test_group_membership_and_last_signoff_are_serialized(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    a, b, _ = contexts(store, tmp_path)
    group = store.create_group(a["workstream"]["id"], "Race")
    first = local_task(store, a, "First", group["id"], group["revision"])
    candidate = local_task(store, b, "Candidate")
    result = store.record_result(
        first["id"],
        a["workstream"]["id"],
        1,
        "implementer",
        "Done",
        "Verified",
        artifacts=[{"kind": "artifact", "reference": "tests/test_shared_groups.py"}],
        verification="Verified",
        specification_etag=store.get_tasks([first["id"]])["items"][0]["specification_etag"],
    )
    store.record_review(result["id"], 1, "reviewer", "pass", "Checked")
    group_revision = store.get_tasks([group["id"]])["items"][0]["revision"]
    barrier = Barrier(2)

    def signoff():
        barrier.wait()
        return store.signoff_task(
            first["id"], 2, "approve", "Approved", result["id"], expected_attempt_revision=2
        )

    def attach():
        barrier.wait()
        return store.add_group_member(group["id"], group_revision, candidate["id"], 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(signoff), pool.submit(attach)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except TaskError as exc:
                assert "completed_task_immutable" in str(exc)
    assert outcomes
    detail = store.get_tasks([group["id"]])["items"][0]
    attached = candidate["id"] in detail["members"]
    assert detail["complete"] is not attached
    assert detail["progress"]["done"] == 1


def test_group_prerequisite_proposal_and_nested_group_rejection(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    a, b, _ = contexts(store, tmp_path)
    group = store.create_group(a["workstream"]["id"], "Shared gate")
    target = local_task(store, b, "Target")
    proposal = store.add_prerequisite(target["id"], 1, group["id"], handling="observer")
    accepted = store.accept_gate_proposal(proposal["id"], 1)
    assert accepted["blocked_by"] == [group["id"]]
    with pytest.raises(TaskError, match="invalid_member"):
        store.add_group_member(group["id"], group["revision"], group["id"], group["revision"])
    assert store.get_tasks([group["id"]])["items"][0]["members"] == []


def test_decomposed_and_new_groups_share_membership_and_scope_rules(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    a, b, _ = contexts(store, tmp_path)
    new_group = store.create_group(a["workstream"]["id"], "New group")
    old_parent = local_task(store, a, "Decompose")
    legacy_group = store.decompose_task(old_parent["id"], 1, [{"title": "Original member"}])
    assert legacy_group["project_id"] is None
    assert legacy_group["origin_project_id"] == a["project"]["id"]
    assert new_group["origin_project_id"] is None
    ws_b = b["workstream"]["id"]
    store.set_scope(ws_b, 1, f"none +{new_group['id']} +{legacy_group['id']}")
    first = local_task(store, b, "New member", new_group["id"], new_group["revision"])
    second = local_task(store, b, "Legacy member", legacy_group["id"], legacy_group["revision"])
    status = store.workstream_status(ws_b)
    assert {item["id"] for item in status["items"]} == {first["id"], second["id"]}
    assert status["status"]["scoped_count"] == 2
    assert len(status["status"]["referenced_groups"]) == 2
    assert sum(status["status"]["counts"].values()) == 2
    assert {item["id"] for item in store.list_groups(b["project"]["id"])["items"]} == {
        new_group["id"],
        legacy_group["id"],
    }


def test_legacy_populated_database_migrates_without_losing_ids(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    for statement in SCHEMA:
        db.execute(
            statement.replace(
                "project_id TEXT REFERENCES projects(id)",
                "project_id TEXT NOT NULL REFERENCES projects(id)",
                1,
            )
            if "CREATE TABLE IF NOT EXISTS tasks (" in statement
            else statement
        )
    now = timestamp()
    db.execute(
        "INSERT INTO projects (id,name,canonical_path,created_at) VALUES (?,?,?,?)",
        ("prj_legacy", "legacy", "/legacy", now),
    )
    db.execute("INSERT INTO project_paths VALUES (?,?)", ("/legacy", "prj_legacy"))
    db.execute(
        "INSERT INTO workstreams VALUES (?,?,?,?,?,?,?)",
        ("wst_legacy", "prj_legacy", "main", "main", "/legacy", 3, now),
    )
    group_id = Store._insert_task(db, "prj_legacy", "Group", "Context", "Criteria")
    db.execute("UPDATE tasks SET object_type='group' WHERE id=?", (group_id,))
    child_id = Store._insert_task(
        db,
        "prj_legacy",
        "Child",
        "Body",
        "Checks",
        group_id,
    )
    db.execute("INSERT INTO scope_groups VALUES (?,?)", ("wst_legacy", group_id))
    db.execute("INSERT INTO scope_exclusions VALUES (?,?)", ("wst_legacy", child_id))
    blocker_id = Store._insert_task(db, "prj_legacy", "Blocker", "", "")
    db.execute(
        "INSERT INTO prerequisites (task_id,blocked_by_id) VALUES (?,?)", (blocker_id, group_id)
    )
    db.execute(
        "INSERT INTO attempts (id,task_id,workstream_id,implementer,summary,evidence,"
        "spec_revision,state,reviewer,review_note,human_review_note,revision,"
        "created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "att_legacy",
            child_id,
            "wst_legacy",
            "worker",
            "Done",
            "Evidence",
            1,
            "passed",
            "reviewer",
            "Pass",
            None,
            2,
            now,
            now,
        ),
    )
    db.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, now, "legacy", "test", "ok", "prj_legacy", child_id, "{}", None, None, None),
    )
    db.commit()
    db.close()
    store = Store(database)
    detail = store.get_tasks([group_id, child_id])["items"]
    assert detail[0]["members"] == [child_id]
    assert detail[1]["attempts"][0]["id"] == "att_legacy"
    assert detail[1]["queue_workstream_id"] is None
    assert child_id not in store.preflight("prj_legacy", "/legacy", branch="main")["scope"]
    assert store.get_tasks([blocker_id])["items"][0]["blocked_by"] == [group_id]
    assert store.list_events("prj_legacy", child_id)["items"][0]["action"] == "test"
    with sqlite3.connect(database) as check:
        assert check.execute("PRAGMA table_info(tasks)").fetchall()[1][3] == 0
        assert check.execute("PRAGMA foreign_key_check").fetchall() == []
    Store(database)
