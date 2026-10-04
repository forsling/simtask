"""The trimmed MCP catalog keeps every removed capability reachable through fewer tools."""

import asyncio
import json
import sqlite3

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from task_mcp.server import create_server
from task_mcp.store import DATABASE_SCHEMA_REVISION, Store

REMOVED = {
    "init_project",
    "init_workstream",
    "attach_checkout",
    "rebind_workstream",
    "propose_prerequisite",
    "accept_gate_proposal",
    "dismiss_gate_proposal",
    "runtime_info",
    "preflight",
    "export_workstream",
    "set_scope",
    "human_review",
    "create_group",
    "add_group_member",
    "list_groups",
    "list_group_members",
}


@pytest.fixture
def mcp(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    server = create_server(store, tracing=False)

    async def call(name, **arguments):
        result = await server.call_tool(name, arguments)
        assert not result.is_error, result.content
        return result.structured_content

    async def fail(name, **arguments):
        with pytest.raises(ToolError) as error:
            await server.call_tool(name, arguments)
        return str(error.value)

    return store, server, call, fail


def run(coroutine):
    return asyncio.run(coroutine)


def test_removed_tools_are_gone_and_replacement_inputs_are_published(mcp):
    _, server, _, _ = mcp
    tools = {tool.name: tool for tool in run(server.list_tools())}
    assert len(tools) == 26 and not REMOVED & tools.keys()
    init = tools["init"].input_schema["properties"]
    assert set(init["action"]["anyOf"][0]["enum"]) == {
        "create_project",
        "new_workstream",
        "attach_workstream",
        "rebind_workstream",
    }
    assert tools["create_task"].input_schema["properties"]["kind"]["enum"] == ["task", "group"]
    update = tools["update_task"].input_schema
    assert {"group_id", "group_expected_revision"} <= update["properties"].keys()
    assert "changes" not in update["required"]
    listing = tools["list_tasks"].input_schema
    assert "group_id" in listing["properties"] and "project" not in listing.get("required", [])
    for name in ("add_unresolved", "add_prerequisite"):
        handling = tools[name].input_schema["properties"]["handling"]
        assert handling["enum"] == ["active", "user"]
    described = json.dumps([{"name": t.name, "description": t.description} for t in tools.values()])
    # rebind_workstream remains an init action; human_review remains an attempt state.
    for name in REMOVED - {"rebind_workstream", "human_review"}:
        assert name not in described, name


def test_init_actions_and_checkout_branch_match_check(mcp, tmp_path):
    _, _, call, fail = mcp
    repo, other = str(tmp_path / "repo"), str(tmp_path / "other")

    async def exercise():
        created = await call(
            "init", path=repo, branch="main", action="create_project", confirmed=True
        )
        assert created["state"] == "ready" and created["runtime"]["database_schema_revision"]
        project, main = created["project"]["id"], created["workstream"]["id"]
        feature = await call(
            "init", path=repo, branch="feature", action="new_workstream", confirmed=True
        )
        attached = await call(
            "init",
            path=other,
            branch="release",
            action="attach_workstream",
            project=project,
            confirmed=True,
        )
        assert attached["project"]["id"] == project
        assert (await call("init", path=repo, branch="main", workstream_id=main))[
            "state"
        ] == "ready"
        # A known workstream bound elsewhere is a clear mismatch, never a silent switch.
        # The checkout/branch is already bound here, but another workstream is named: report
        # the requested workstream's actual binding plus the local one, with choices.
        feature_id = feature["workstream"]["id"]
        wrong = await call("init", path=repo, branch="main", workstream_id=feature_id)
        assert wrong["state"] == "mismatch" and "queue" not in wrong
        assert wrong["workstream"]["id"] == feature_id
        assert wrong["workstream"]["branch"] == "feature"
        assert wrong["bound_workstream"]["id"] == main
        assert "'feature'" in wrong["message"] and main in wrong["message"]
        assert wrong["choices"] == ["use_bound_workstream", "init_requested_binding"]
        # An unknown workstream ID is an unknown-workstream error, not a binding mismatch.
        assert "unknown_workstream" in await fail(
            "init", path=repo, branch="main", workstream_id="wst_nope"
        )
        # A workstream of another project is named as such, never swapped for the local one.
        foreign = await call(
            "init",
            path=str(tmp_path / "foreign"),
            branch="main",
            action="create_project",
            confirmed=True,
        )
        foreign_id = foreign["workstream"]["id"]
        cross = await call("init", path=repo, branch="main", workstream_id=foreign_id)
        assert cross["state"] == "mismatch" and cross["workstream"]["id"] == foreign_id
        assert "another project" in cross["message"]
        assert foreign["project"]["id"] in cross["message"]
        assert cross["bound_workstream"]["id"] == main and cross["choices"]
        # The same checks hold on an attached checkout whose branch is not yet bound.
        assert "unknown_workstream" in await fail(
            "init", path=repo, branch="topic", workstream_id="wst_nope"
        )
        cross_new = await call("init", path=repo, branch="topic", workstream_id=foreign_id)
        assert cross_new["state"] == "mismatch" and cross_new["workstream"]["id"] == foreign_id
        assert "another project" in cross_new["message"] and cross_new["choices"]
        moved = await call("init", path=other, branch="hotfix", workstream_id=main)
        assert moved["state"] == "mismatch" and moved["workstream"]["id"] == main
        assert "'main'" in moved["message"] and other in moved["message"]
        assert moved["choices"] == ["rebind_workstream", "new_workstream"]
        rebound = await call(
            "init",
            path=other,
            branch="hotfix",
            action="rebind_workstream",
            workstream_id=main,
            expected_revision=moved["workstream"]["revision"],
            confirmed=True,
        )
        assert rebound["state"] == "ready" and rebound["changed"]
        assert rebound["workstream"]["id"] == main
        assert rebound["workstream"]["checkout_path"] == other
        assert (await call("init", path=other, branch="hotfix"))["workstream"]["id"] == main

    run(exercise())


def test_group_kind_membership_listing_and_scope_through_everyday_tools(mcp, tmp_path):
    store, _, call, fail = mcp

    async def exercise():
        a = await call(
            "init", path=str(tmp_path / "a"), branch="main", action="create_project", confirmed=True
        )
        b = await call(
            "init", path=str(tmp_path / "b"), branch="main", action="create_project", confirmed=True
        )
        project_a, ws_a = a["project"]["id"], a["workstream"]["id"]
        project_b, ws_b = b["project"]["id"], b["workstream"]["id"]
        assert "workstream_required" in await fail(
            "create_task", project=project_a, title="Loose group", kind="group"
        )
        assert "nested groups" in await fail(
            "create_task",
            project=project_a,
            title="Nested",
            kind="group",
            workstream_id=ws_a,
            group_id="tsk_any",
            group_expected_revision=1,
        )
        group = await call(
            "create_task",
            project=project_a,
            title="Shared feature",
            body="Group context",
            workstream_id=ws_a,
            kind="group",
            source="user",
            user_request="Synthetic request",
        )
        assert group["object_type"] == "group" and group["project_id"] is None
        assert store.get_tasks([group["id"]])["items"][0]["source"] == "user"
        first = await call("create_task", project=project_a, title="First", workstream_id=ws_a)
        joined = await call(
            "update_task",
            task_id=first["id"],
            expected_revision=first["revision"],
            group_id=group["id"],
            group_expected_revision=group["revision"],
        )
        assert joined["parent_group_id"] == group["id"] and joined["group_changed"]
        assert joined["revision"] == 2 and joined["group_revision"] == 2
        again = await call(
            "update_task",
            task_id=first["id"],
            expected_revision=joined["revision"],
            group_id=group["id"],
            group_expected_revision=joined["group_revision"],
        )
        assert not again["changed"] and not again["group_changed"]
        assert "revision_conflict" in await fail(
            "update_task",
            task_id=first["id"],
            expected_revision=2,
            group_id=group["id"],
            group_expected_revision=1,
        )
        other_group = await call(
            "create_task", project=project_a, title="Other", workstream_id=ws_a, kind="group"
        )
        assert "invalid_member" in await fail(
            "update_task",
            task_id=first["id"],
            expected_revision=2,
            group_id=other_group["id"],
            group_expected_revision=other_group["revision"],
        )
        remote = await call("create_task", project=project_b, title="Remote member")
        remote_joined = await call(
            "update_task",
            task_id=remote["id"],
            expected_revision=remote["revision"],
            group_id=group["id"],
            group_expected_revision=2,
            changes={"summary": "Remote half"},
        )
        assert remote_joined["summary_changed"] and remote_joined["group_revision"] == 3

        # Group IDs in the membership tools replace set_scope inclusion and exclusion.
        assert remote_joined["workstream_ids"] == []
        included = await call(
            "add_to_workstream", task_id=group["id"], workstream_id=ws_b, expected_revision=3
        )
        assert included["changed"] and included["included"] and included["revision"] == 4
        board_b = await call("list_tasks", project=project_b, workstream_id=ws_b)
        assert [card["id"] for card in board_b["items"]] == [remote["id"]]
        same = await call(
            "add_to_workstream", task_id=group["id"], workstream_id=ws_b, expected_revision=4
        )
        assert not same["changed"] and same["revision"] == 4
        member = (await call("get_tasks", ids=[remote["id"]]))["items"][0]
        excluded = await call(
            "remove_from_workstream",
            task_id=remote["id"],
            workstream_id=ws_b,
            expected_revision=member["revision"],
        )
        assert excluded["changed"] and excluded["workstream_ids"] == []
        group_card = (await call("get_tasks", ids=[group["id"]], include=["workstreams"]))["items"][
            0
        ]
        assert {w["id"] for w in group_card["workstreams"]} == {ws_a, ws_b}
        later = await call("create_task", project=project_b, title="Later member")
        later = await call(
            "update_task",
            task_id=later["id"],
            expected_revision=later["revision"],
            group_id=group["id"],
            group_expected_revision=group_card["revision"],
        )
        assert later["workstream_ids"] == [ws_b]
        board_b = await call("list_tasks", project=project_b, workstream_id=ws_b)
        assert [card["id"] for card in board_b["items"]] == [later["id"]]
        removed = await call(
            "remove_from_workstream",
            task_id=group["id"],
            workstream_id=ws_b,
            expected_revision=later["group_revision"],
        )
        assert removed["changed"] and not removed["included"]
        assert (await call("list_tasks", project=project_b, workstream_id=ws_b))["items"] == []
        with sqlite3.connect(store.path) as db:
            assert db.execute(
                "SELECT task_id FROM scope_exclusions WHERE workstream_id=? ORDER BY task_id",
                (ws_b,),
            ).fetchall() == sorted([(remote["id"],), (group["id"],)])

        # list_tasks filters replace list_groups and list_group_members.
        groups_a = await call("list_tasks", project=project_a, state="group")
        assert [card["id"] for card in groups_a["items"]] == [group["id"], other_group["id"]]
        assert groups_a["items"][0]["state"] == "group"
        assert groups_a["items"][0]["progress"]["total"] == 3
        groups_b = await call("list_tasks", project=project_b, state="group")
        assert [card["id"] for card in groups_b["items"]] == [group["id"]]
        assert (await call("list_tasks", project=project_b, workstream_id=ws_b, state="group"))[
            "items"
        ] == []
        everywhere = await call("list_tasks", state="group", limit=1)
        assert everywhere["total"] == 2 and everywhere["next_offset"] == 1
        members = await call("list_tasks", group_id=group["id"])
        assert [card["id"] for card in members["items"]] == [first["id"], remote["id"], later["id"]]
        local = await call("list_tasks", project=project_b, group_id=group["id"])
        assert [card["id"] for card in local["items"]] == [remote["id"], later["id"]]
        scoped = await call(
            "list_tasks", project=project_a, workstream_id=ws_a, group_id=group["id"]
        )
        assert [card["id"] for card in scoped["items"]] == [first["id"]]
        assert scoped["items"][0]["position"] == 1
        assert "project_required" in await fail("list_tasks")
        assert "invalid_group" in await fail("list_tasks", group_id=first["id"])

    run(exercise())


def test_signoff_approve_without_independent_review_over_mcp(mcp, tmp_path):
    store, _, call, fail = mcp

    async def exercise():
        setup = await call(
            "init", path=str(tmp_path / "r"), branch="main", action="create_project", confirmed=True
        )
        project, ws = setup["project"]["id"], setup["workstream"]["id"]
        task = await call("create_task", project=project, title="Small fix", workstream_id=ws)
        result = await call(
            "record_result",
            task_id=task["id"],
            workstream_id=ws,
            expected_revision=task["revision"],
            implementer="worker",
            summary="Done",
            evidence="Checked",
            artifacts=[{"kind": "artifact", "reference": "proof"}],
            verification="Checked",
            specification_etag=task["specification_etag"],
        )
        arguments = dict(
            task_id=task["id"],
            expected_revision=result["task_revision"],
            attempt_id=result["attempt_id"],
            expected_attempt_revision=result["attempt_revision"],
        )
        for decision in ("rework", "revise", "drop"):
            message = await fail("signoff_task", decision=decision, reasons="No", **arguments)
            assert "review_required" in message
        assert "reasons_required" in await fail("signoff_task", decision="approve", **arguments)
        done = await call(
            "signoff_task",
            decision="approve",
            reasons="User explicitly approved without independent review",
            **arguments,
        )
        assert done["status"] == "done" and done["attempt_state"] == "human_review"
        assert done["independent_review"] is False
        proof = store.get_attempt(result["attempt_id"])
        assert proof["human_review_note"].startswith("User explicitly approved")

    run(exercise())


def test_existing_groups_memberships_exclusions_and_history_survive_unchanged(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    store = Store(database)
    setup = store.init(str(tmp_path / "r"), "main", action="create_project", confirmed=True)
    project, ws = setup["project"]["id"], setup["workstream"]["id"]
    other = store.init_workstream(project, str(tmp_path / "r"), "other", confirmed=True)
    other_ws = other["workstream"]["id"]
    # Data the removed tools wrote stays exactly as an older server stored it.
    group = store.create_group(ws, "Legacy group")
    member = store.create_task(project, "Member", workstream_id=ws)
    store.add_group_member(group["id"], 1, member["id"], member["revision"])
    kept = store.create_task(
        project, "Kept member", group_id=group["id"], group_expected_revision=2
    )
    store.set_scope(other_ws, 1, f"none +{group['id']} -{member['id']}")
    attempt = store.record_result(
        kept["id"],
        other_ws,
        kept["revision"],
        "worker",
        "Done",
        "Proof",
        [{"kind": "artifact", "reference": "proof"}],
        "Checked",
        kept["specification_etag"],
    )
    store.human_review(attempt["id"], 1, "Legacy explicit human review")
    tables = ("tasks", "scope_members", "scope_groups", "scope_exclusions", "attempts", "events")
    with sqlite3.connect(database) as db:
        before = {t: db.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
    server = create_server(Store(database), tracing=False)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_REVISION
        after = {t: db.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
    assert after == before

    async def read(name, **arguments):
        result = await server.call_tool(name, arguments)
        assert not result.is_error, result.content
        return result.structured_content

    async def exercise():
        board = await read("list_tasks", project=project, workstream_id=other_ws)
        assert [card["id"] for card in board["items"]] == [kept["id"]]
        assert board["items"][0]["state"] == "signoff"
        groups = await read("list_tasks", project=project, workstream_id=other_ws, state="group")
        assert [card["id"] for card in groups["items"]] == [group["id"]]
        members = await read("list_tasks", group_id=group["id"])
        assert [card["id"] for card in members["items"]] == [member["id"], kept["id"]]
        history = await read("list_events", task_id=kept["id"], limit=100)
        assert {"attempt.recorded", "attempt.human_reviewed"} <= {
            event["action"] for event in history["items"]
        }

    run(exercise())


def test_completed_group_scope_change_matches_set_scope_and_keeps_group_row(mcp, tmp_path):
    store, _, call, fail = mcp
    repo = str(tmp_path / "r")
    setup = store.init(repo, "main", action="create_project", confirmed=True)
    project, ws = setup["project"]["id"], setup["workstream"]["id"]
    via_tools = store.init_workstream(project, repo, "tools", confirmed=True)["workstream"]["id"]
    via_scope = store.init_workstream(project, repo, "scope", confirmed=True)["workstream"]["id"]
    group = store.create_task(project, "Finished feature", workstream_id=ws, kind="group")
    member = store.create_task(
        project,
        "Only member",
        workstream_id=ws,
        group_id=group["id"],
        group_expected_revision=group["revision"],
    )
    attempt = store.record_result(
        member["id"],
        ws,
        member["revision"],
        "worker",
        "Done",
        "Proof",
        [{"kind": "artifact", "reference": "proof"}],
        "Checked",
        member["specification_etag"],
    )
    store.signoff_task(
        member["id"],
        store.get_tasks([member["id"]])["items"][0]["revision"],
        "approve",
        "User approved",
        attempt["id"],
        expected_attempt_revision=attempt["revision"],
    )
    detail = store.get_tasks([group["id"]])["items"][0]
    assert detail["complete"]

    def snapshot(workstream_id):
        with sqlite3.connect(store.path) as db:
            group_row = db.execute("SELECT * FROM tasks WHERE id=?", (group["id"],)).fetchone()
            return group_row, {
                table: db.execute(
                    f"SELECT * FROM {table} WHERE workstream_id=? ORDER BY 2", (workstream_id,)
                ).fetchall()
                for table in ("scope_members", "scope_groups", "scope_exclusions")
            }

    group_row, _ = snapshot(via_tools)

    def revision(workstream_id):
        rows = store.list_workstreams(project)["items"]
        return next(row["revision"] for row in rows if row["id"] == workstream_id)

    async def exercise():
        included = await call(
            "add_to_workstream",
            task_id=group["id"],
            workstream_id=via_tools,
            expected_revision=detail["revision"],
        )
        assert included["changed"] and included["included"]
        assert included["revision"] == detail["revision"]
        store.set_scope(via_scope, revision(via_scope), f"none +{group['id']}")
        tools_row, tools_tables = snapshot(via_tools)
        _, scope_tables = snapshot(via_scope)
        assert tools_row == group_row and tools_tables["scope_groups"]
        assert [row[1:] for t in tools_tables.values() for row in t] == [
            row[1:] for t in scope_tables.values() for row in t
        ]
        board = await call("list_tasks", project=project, workstream_id=via_tools, state="done")
        assert member["id"] in {card["id"] for card in board["items"]}

        removed = await call(
            "remove_from_workstream",
            task_id=group["id"],
            workstream_id=via_tools,
            expected_revision=detail["revision"],
        )
        assert removed["changed"] and not removed["included"]
        assert removed["revision"] == detail["revision"]
        store.set_scope(via_scope, revision(via_scope), f"{via_scope} -{group['id']}")
        tools_row, tools_tables = snapshot(via_tools)
        _, scope_tables = snapshot(via_scope)
        assert tools_row == group_row and tools_tables["scope_exclusions"]
        assert not tools_tables["scope_groups"]
        assert [row[1:] for t in tools_tables.values() for row in t] == [
            row[1:] for t in scope_tables.values() for row in t
        ]
        # The live workstream that included the group from creation can drop it too.
        dropped = await call(
            "remove_from_workstream",
            task_id=group["id"],
            workstream_id=ws,
            expected_revision=detail["revision"],
        )
        assert dropped["changed"] and not dropped["included"]
        assert snapshot(ws)[0] == group_row
        # Completed groups stay immutable for real changes.
        assert "completed_task_immutable" in await fail(
            "update_task",
            task_id=group["id"],
            expected_revision=detail["revision"],
            changes={"body": "Changed"},
        )

    run(exercise())
