"""Published MCP safety hints match the state changes each tool can make."""

import asyncio
import sqlite3

from task_mcp.server import create_server
from task_mcp.store import Store

# These calls persist audit events or append domain entities, so they are writes
# even when their business effect is observational or append-only.
ADDITIVE = {
    "list_projects",
    "init_project",
    "attach_checkout",
    "init_workstream",
    "list_workstreams",
    "workstream_status",
    "list_groups",
    "create_group",
    "add_group_member",
    "preflight",
    "create_task",
    "list_tasks",
    "get_tasks",
    "list_task_attempts",
    "get_attempt",
    "list_group_members",
    "add_unresolved",
    "add_prerequisite",
    "propose_prerequisite",
    "get_next_action",
    "record_result",
    "list_events",
    "export_workstream",
}

# `init` can rebind an existing workstream, so its single descriptor must take
# the most conservative classification among its possible actions.
DESTRUCTIVE = {
    "init",
    "rebind_workstream",
    "set_scope",
    "update_task",
    "queue_task",
    "unqueue_task",
    "set_disposition",
    "resolve_unresolved",
    "remove_prerequisite",
    "accept_gate_proposal",
    "dismiss_gate_proposal",
    "decompose_task",
    "reorder_tasks",
    "record_review",
    "human_review",
    "signoff_task",
}

CATALOG = {"get_default_skills", "runtime_info"}
LAUNCHER = {"open_task_viewer"}


def test_every_published_tool_has_an_explicit_safety_policy(tmp_path):
    server = create_server(Store(tmp_path / "tasks.sqlite3"))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert set(tools) == ADDITIVE | DESTRUCTIVE | CATALOG | LAUNCHER
    assert not (ADDITIVE & DESTRUCTIVE or ADDITIVE & CATALOG or DESTRUCTIVE & CATALOG)
    for name in ADDITIVE:
        hints = tools[name].annotations
        assert hints is not None, name
        assert hints.read_only_hint is False, name
        assert hints.destructive_hint is False, name
        assert hints.idempotent_hint is False, name
        assert hints.open_world_hint is False, name
    for name in DESTRUCTIVE:
        hints = tools[name].annotations
        assert hints is not None, name
        assert hints.read_only_hint is False, name
        assert hints.destructive_hint is True, name
        assert hints.idempotent_hint is False, name
        assert hints.open_world_hint is False, name
    for name in CATALOG:
        hints = tools[name].annotations
        assert hints is not None, name
        assert hints.read_only_hint is True, name
        assert hints.destructive_hint is False, name
        assert hints.idempotent_hint is True, name
        assert hints.open_world_hint is False, name
    for name in LAUNCHER:
        hints = tools[name].annotations
        assert hints.read_only_hint is False
        assert hints.destructive_hint is False
        assert hints.idempotent_hint is True
        assert hints.open_world_hint is False


def _business_payload(database, task_id):
    with sqlite3.connect(database) as db:
        return db.execute(
            "SELECT title,body,acceptance_criteria,status,spec_revision,"
            "accepted_spec_revision,acceptance_note FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()


def _audit_rows(database):
    with sqlite3.connect(database) as db:
        return db.execute(
            "SELECT sequence,timestamp,action,request_json,before_json,after_json "
            "FROM events ORDER BY sequence"
        ).fetchall()


def test_append_only_tools_preserve_existing_payload_and_audit_history(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    store = Store(database)
    original_path = str(tmp_path / "original-checkout")
    setup = store.init_project(original_path, branch="main", confirmed=True)
    project = setup["project"]["id"]
    workstream = setup["workstream"]["id"]
    anchor = store.create_task(
        project,
        "Existing accepted task",
        "Original body",
        "Original criterion",
        source="user",
        user_request="Original user request",
        workstream_id=workstream,
    )
    anchor_payload = _business_payload(database, anchor["id"])
    original_audit = _audit_rows(database)

    second_path = str(tmp_path / "second-checkout")
    store.attach_checkout(project, second_path, confirmed=True)
    second = store.init_workstream(project, second_path, branch="feature", confirmed=True)
    assert second["workstream"]["id"] != workstream
    with sqlite3.connect(database) as db:
        assert db.execute(
            "SELECT branch,checkout_path FROM workstreams WHERE id=?", (workstream,)
        ).fetchone() == ("main", original_path)

    group = store.create_group(workstream, "New group")
    member = store.add_group_member(group["id"], group["revision"], anchor["id"], 1)
    assert member["member"]["parent_group_id"] == group["id"]
    assert _business_payload(database, anchor["id"]) == anchor_payload

    gated = store.create_task(project, "Gated task", "Keep this body", workstream_id=workstream)
    gated_payload = _business_payload(database, gated["id"])
    unresolved = store.add_unresolved(gated["id"], gated["revision"], "New question")
    linked = store.add_prerequisite(gated["id"], unresolved["revision"], anchor["id"])
    proposed = store.propose_prerequisite(
        gated["id"], linked["revision"], "New prerequisite", workstream_id=workstream
    )
    assert proposed["proposal"]["id"] != gated["id"]
    assert _business_payload(database, gated["id"]) == gated_payload

    delivered = store.create_task(
        project,
        "New deliverable",
        "Delivery body",
        source="user",
        user_request="Synthetic request",
        workstream_id=workstream,
    )
    delivered_payload = _business_payload(database, delivered["id"])
    attempt = store.record_result(
        delivered["id"],
        workstream,
        delivered["revision"],
        "worker",
        "Delivered",
        "Verified",
        artifacts=[{"kind": "artifact", "reference": "tests/test_tool_annotations.py"}],
        verification="Verified",
        specification_etag=store.get_tasks([delivered["id"]])["items"][0]["specification_etag"],
    )
    assert attempt["task_id"] == delivered["id"]
    assert _business_payload(database, delivered["id"]) == delivered_payload
    assert _business_payload(database, anchor["id"]) == anchor_payload
    assert _audit_rows(database)[: len(original_audit)] == original_audit
