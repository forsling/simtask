"""The trimmed MCP catalog keeps every removed capability reachable through fewer tools."""

import asyncio
import json
import sqlite3

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from task_mcp.server import create_server
from task_mcp.store import (
    DATABASE_SCHEMA_REVISION,
    INIT_MESSAGES,
    INIT_NAME_TAKEN,
    INIT_NEXT,
    Store,
    TaskError,
)

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
    assert len(tools) == 28 and not REMOVED & tools.keys()
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
    # init states the scope_expression syntax that seeds a new workstream.
    assert "scope_expression: none or a base workstream, then +/-task/group" in (
        tools["init"].description
    )
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
        # the requested workstream's actual binding plus the local one, with the next calls.
        feature_id = feature["workstream"]["id"]
        wrong = await call("init", path=repo, branch="main", workstream_id=feature_id)
        assert wrong["state"] == "mismatch" and "queue" not in wrong
        roles = {ws["id"]: ws for ws in wrong["workstreams"]}
        assert roles[feature_id]["roles"] == ["requested"]
        assert roles[feature_id]["branch"] == "feature"
        assert roles[main]["roles"] == ["bound"]
        # Resume the workstream bound here, or the requested one at its own binding.
        assert [item["arguments"] for item in wrong["next"]] == [
            {"path": repo, "branch": "main", "workstream_id": main},
            {"path": repo, "branch": "feature", "workstream_id": feature_id},
        ]
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
        roles = {ws["id"]: ws for ws in cross["workstreams"]}
        assert cross["state"] == "mismatch" and roles[foreign_id]["roles"] == ["requested"]
        assert roles[foreign_id]["project_id"] == foreign["project"]["id"]
        assert roles[main]["roles"] == ["bound"] and cross["project"]["id"] == project
        # The same checks hold on an attached checkout whose branch is not yet bound.
        assert "unknown_workstream" in await fail(
            "init", path=repo, branch="topic", workstream_id="wst_nope"
        )
        cross_new = await call("init", path=repo, branch="topic", workstream_id=foreign_id)
        assert cross_new["state"] == "mismatch"
        assert [ws["id"] for ws in cross_new["workstreams"]] == [foreign_id]
        # Rebinding never crosses projects, so a foreign workstream is not offered to move.
        assert [item["choice"] for item in cross_new["next"]] == ["resume", "create"]
        assert "workstream_project_mismatch" in await fail(
            "init",
            path=repo,
            branch="topic",
            action="rebind_workstream",
            workstream_id=foreign_id,
            expected_revision=foreign["workstream"]["revision"],
            confirmed=True,
        )
        moved = await call("init", path=other, branch="hotfix", workstream_id=main)
        assert moved["state"] == "mismatch"
        assert moved["workstreams"][0]["id"] == main
        assert moved["workstreams"][0]["checkout_path"] == repo
        assert [item["choice"] for item in moved["next"]] == ["resume", "create", "rebind"]
        assert moved["next"][2]["arguments"] == {
            "path": other,
            "branch": "hotfix",
            "action": "rebind_workstream",
            "workstream_id": main,
            "expected_revision": moved["workstreams"][0]["revision"],
            "confirmed": True,
        }
        rebound = await call(
            "init",
            path=other,
            branch="hotfix",
            action="rebind_workstream",
            workstream_id=main,
            expected_revision=moved["workstreams"][0]["revision"],
            confirmed=True,
        )
        assert rebound["state"] == "ready" and rebound["changed"]
        assert rebound["workstream"]["id"] == main
        assert rebound["workstream"]["checkout_path"] == other
        assert (await call("init", path=other, branch="hotfix"))["workstream"]["id"] == main

    run(exercise())


def test_every_offered_init_mismatch_choice_works_when_followed(tmp_path):
    repo, other = str(tmp_path / "repo"), str(tmp_path / "other")
    foreign_path, loose = str(tmp_path / "foreign"), str(tmp_path / "loose")
    attic = str(tmp_path / "attic")
    databases = iter(range(10_000))

    def archived(ws):
        return bool(ws and (ws.get("archive") or {}).get("archived"))

    def tools():
        server = create_server(Store(tmp_path / f"{next(databases)}.sqlite3"), tracing=False)

        async def call(name, **arguments):
            result = await server.call_tool(name, arguments)
            assert not result.is_error, result.content
            return result.structured_content

        async def fail(name, **arguments):
            with pytest.raises(ToolError) as error:
                await server.call_tool(name, arguments)
            return str(error.value)

        call.fail = fail
        return call

    async def setup(call):
        made = await call("init", path=repo, branch="main", action="create_project", confirmed=True)
        project = made["project"]["id"]
        ids = {"main": made["workstream"]["id"], "project": project}
        for key, extra in (
            ("feature", {"path": repo, "branch": "feature", "action": "new_workstream"}),
            ("notes", {"path": repo, "workstream_name": "notes", "action": "new_workstream"}),
            (
                "release",
                {"path": other, "branch": "release", "action": "attach_workstream"}
                | {"project": project},
            ),
            ("foreign", {"path": foreign_path, "branch": "main", "action": "create_project"}),
            # Names that differ from branches, so a rebind or new workstream can collide on
            # UNIQUE(project_id, name) although the branch itself is free.
            (
                "wip",
                {"path": repo, "branch": "wip-branch", "workstream_name": "wip"}
                | {"action": "new_workstream"},
            ),
            (
                "rel_two",
                {"path": other, "branch": "rel2", "workstream_name": "rel-two"}
                | {"action": "new_workstream"},
            ),
            ("rel2_name", {"path": repo, "workstream_name": "rel2", "action": "new_workstream"}),
            # An archived snapshot checkout whose name differs from its branch, so it can
            # hold a branch, a checkout and a name.
            (
                "old",
                {"path": attic, "branch": "old-branch", "workstream_name": "old"}
                | {"action": "attach_workstream", "project": project},
            ),
        ):
            made = await call("init", confirmed=True, **extra)
            ids[key], ids[f"{key}_project"] = made["workstream"]["id"], made["project"]["id"]
        archived_ack = await call(
            "archive_workstream", workstream_id=ids["old"], expected_revision=0, reason="snapshot"
        )
        assert archived_ack["archive"]["archived"]
        return ids

    rebinds = ["rebind"] * 7  # every unarchived workstream of the project can move here
    # label: (request built from the setup IDs, state, the choices of next in order, and
    # calls left out because they would fail: an action, or the setup key of a workstream
    # whose rebind collides on its name; each is attempted and must fail as reported).
    scenarios = {
        "bound_here_same_project": (
            lambda ids: {"path": repo, "branch": "main", "workstream_id": ids["feature"]},
            "mismatch",
            ["resume", "resume"],
            [],
        ),
        "bound_here_name_bound_target": (
            lambda ids: {"path": repo, "branch": "main", "workstream_id": ids["notes"]},
            "mismatch",
            ["resume", "resume"],
            [],
        ),
        "bound_here_foreign": (
            lambda ids: {"path": repo, "branch": "main", "workstream_id": ids["foreign"]},
            "mismatch",
            ["resume", "resume"],
            [],
        ),
        # Rebinding never crosses projects, so a foreign workstream is only resumed.
        "unbound_branch_foreign": (
            lambda ids: {"path": repo, "branch": "topic", "workstream_id": ids["foreign"]},
            "mismatch",
            ["resume", "create"],
            ["rebind:foreign"],
        ),
        "unregistered_with_project_foreign": (
            lambda ids: (
                {"path": loose, "branch": "topic", "project": ids["project"]}
                | {"workstream_id": ids["foreign"]}
            ),
            "mismatch",
            ["resume", "create"],
            [],
        ),
        "unbound_branch_same_project": (
            lambda ids: {"path": other, "branch": "hotfix", "workstream_id": ids["feature"]},
            "mismatch",
            ["resume", "create", "rebind"],
            [],
        ),
        "unbound_branch_name_bound_target": (
            lambda ids: {"path": other, "branch": "hotfix", "workstream_id": ids["notes"]},
            "mismatch",
            ["resume", "create", "rebind"],
            [],
        ),
        "unbound_name_same_project": (
            lambda ids: (
                {"path": repo, "workstream_name": "scratch"} | {"workstream_id": ids["feature"]}
            ),
            "mismatch",
            ["resume", "create", "rebind"],
            [],
        ),
        "unregistered_without_project": (
            lambda ids: {"path": loose, "branch": "topic", "workstream_id": ids["feature"]},
            "mismatch",
            ["resume", "create", "rebind"],
            [],
        ),
        # Another workstream named while this branch is bound at another checkout.
        "branch_bound_elsewhere_same_project": (
            lambda ids: {"path": repo, "branch": "release", "workstream_id": ids["feature"]},
            "mismatch",
            ["resume", "rebind"],
            [],
        ),
        "branch_bound_elsewhere_name_bound_target": (
            lambda ids: {"path": repo, "branch": "release", "workstream_id": ids["notes"]},
            "mismatch",
            ["resume", "rebind"],
            [],
        ),
        "branch_bound_elsewhere_foreign": (
            lambda ids: {"path": repo, "branch": "release", "workstream_id": ids["foreign"]},
            "mismatch",
            ["resume", "rebind"],
            [],
        ),
        # An unregistered checkout naming a workstream is checked within that workstream's
        # project, so its bound branch is reported rather than an attach that would collide.
        "unregistered_without_project_branch_bound_elsewhere": (
            lambda ids: {"path": loose, "branch": "release", "workstream_id": ids["feature"]},
            "mismatch",
            ["resume", "rebind"],
            [],
        ),
        "unregistered_without_project_branch_bound_to_requested": (
            lambda ids: {"path": loose, "branch": "release", "workstream_id": ids["release"]},
            "mismatch",
            ["resume", "rebind"],
            [],
        ),
        # The branch is bound at another checkout and no other workstream is named.
        "branch_bound_to_other_checkout": (
            lambda ids: {"path": repo, "branch": "release"},
            "mismatch",
            ["resume", "rebind"],
            [],
        ),
        "unregistered_with_project_branch_bound_to_other_checkout": (
            lambda ids: {"path": loose, "branch": "release", "project": ids["project"]},
            "mismatch",
            ["resume", "rebind"],
            [],
        ),
        # project= names another project than the one this checkout is attached to.
        "attached_to_another_project": (
            lambda ids: {"path": repo, "branch": "main", "project": ids["foreign_project"]},
            "mismatch",
            ["resume"],
            [],
        ),
        "attached_to_another_project_with_workstream": (
            lambda ids: (
                {"path": repo, "branch": "main", "project": ids["foreign_project"]}
                | {"workstream_id": ids["foreign"]}
            ),
            "mismatch",
            ["resume", "resume"],
            [],
        ),
        "attached_to_another_project_unbound_branch": (
            lambda ids: {"path": repo, "branch": "topic", "project": ids["foreign_project"]},
            "mismatch",
            ["check"],
            [],
        ),
        # Workstream-name collisions: a call that would violate UNIQUE(project_id, name) is
        # left out and the workstream holding the name is listed.
        "unbound_branch_name_taken_by_other": (
            lambda ids: {"path": other, "branch": "wip", "workstream_id": ids["feature"]},
            "mismatch",
            ["resume"],
            ["new_workstream", "rebind:feature"],
        ),
        "unbound_branch_name_taken_by_target": (
            lambda ids: {"path": other, "branch": "wip", "workstream_id": ids["wip"]},
            "mismatch",
            ["resume", "rebind"],
            ["new_workstream"],
        ),
        "unregistered_without_project_name_taken": (
            lambda ids: {"path": loose, "branch": "wip", "workstream_id": ids["feature"]},
            "mismatch",
            ["resume"],
            ["attach_workstream", "rebind:feature"],
        ),
        "unregistered_with_project_foreign_name_taken": (
            lambda ids: (
                {"path": loose, "branch": "wip", "project": ids["project"]}
                | {"workstream_id": ids["foreign"]}
            ),
            "mismatch",
            ["resume"],
            ["attach_workstream"],
        ),
        "unbound_branch_foreign_name_taken": (
            lambda ids: {"path": repo, "branch": "wip", "workstream_id": ids["foreign"]},
            "mismatch",
            ["resume"],
            ["new_workstream"],
        ),
        "branch_bound_elsewhere_rebind_name_taken": (
            lambda ids: {"path": repo, "branch": "rel2", "workstream_id": ids["feature"]},
            "mismatch",
            ["resume"],
            ["rebind:rel_two"],
        ),
        "branch_bound_to_other_checkout_rebind_name_taken": (
            lambda ids: {"path": repo, "branch": "rel2"},
            "mismatch",
            ["resume"],
            ["rebind:rel_two"],
        ),
        # Discovery: a new workstream here, or any listed workstream of the project moved
        # here; only the name's holder can move when the name is taken.
        "new_branch": (
            lambda ids: {"path": repo, "branch": "topic"},
            "new_branch",
            ["create"] + rebinds,
            [],
        ),
        "new_branch_name_taken": (
            lambda ids: {"path": repo, "branch": "wip"},
            "new_branch",
            ["rebind"],
            ["new_workstream", "rebind:feature"],
        ),
        "unregistered_checkout_with_project": (
            lambda ids: {"path": loose, "branch": "topic", "project": ids["project"]},
            "unregistered_checkout",
            ["create"] + rebinds,
            [],
        ),
        "unregistered_checkout_with_project_name_taken": (
            lambda ids: {"path": loose, "branch": "wip", "project": ids["project"]},
            "unregistered_checkout",
            ["rebind"],
            ["attach_workstream", "rebind:feature"],
        ),
        # Without project=, a new project, or a check of one project, which then lists the
        # calls that work within it (name and branch checks included).
        "unregistered_checkout": (
            lambda ids: {"path": loose, "branch": "topic"},
            "unregistered_checkout",
            ["create", "check", "check"],
            [],
        ),
        "unregistered_checkout_name_taken": (
            lambda ids: {"path": loose, "branch": "wip"},
            "unregistered_checkout",
            ["create", "check", "check"],
            [],
        ),
        "unregistered_checkout_branch_bound": (
            lambda ids: {"path": loose, "branch": "release"},
            "unregistered_checkout",
            ["create", "check", "check"],
            [],
        ),
        # Archived workstreams: an archived binding is reported, never silently resumed, and
        # every call that resumes or moves one has include_archived=true.
        "archived_binding_here": (
            lambda ids: {"path": attic, "branch": "old-branch"},
            "archived",
            ["resume", "unarchive"],
            [],
        ),
        "archived_binding_here_named": (
            lambda ids: {"path": attic, "branch": "old-branch", "workstream_id": ids["old"]},
            "archived",
            ["resume", "unarchive"],
            [],
        ),
        "bound_here_requested_archived": (
            lambda ids: {"path": repo, "branch": "main", "workstream_id": ids["old"]},
            "mismatch",
            ["resume", "resume"],
            [],
        ),
        "archived_bound_here_other_requested": (
            lambda ids: {"path": attic, "branch": "old-branch", "workstream_id": ids["feature"]},
            "mismatch",
            ["resume", "resume"],
            [],
        ),
        "branch_bound_to_archived_elsewhere": (
            lambda ids: {"path": repo, "branch": "old-branch"},
            "mismatch",
            ["resume", "rebind"],
            [],
        ),
        "branch_bound_to_archived_elsewhere_other_requested": (
            lambda ids: {"path": repo, "branch": "old-branch", "workstream_id": ids["feature"]},
            "mismatch",
            ["resume", "rebind"],
            [],
        ),
        "unbound_branch_requested_archived": (
            lambda ids: {"path": other, "branch": "hotfix", "workstream_id": ids["old"]},
            "mismatch",
            ["resume", "create", "rebind"],
            [],
        ),
        "unbound_branch_name_held_by_archived_target": (
            lambda ids: {"path": other, "branch": "old", "workstream_id": ids["old"]},
            "mismatch",
            ["resume", "rebind"],
            ["new_workstream"],
        ),
        "unregistered_checkout_with_project_name_held_by_archived": (
            lambda ids: {"path": loose, "branch": "old", "project": ids["project"]},
            "unregistered_checkout",
            ["rebind"],
            ["attach_workstream"],
        ),
    }

    def here(request):
        keys = ("path", "branch", "workstream_name")
        return {key: request[key] for key in keys if key in request}

    def left_out(request, response, ids, call):
        # The arguments of a call init left out: a new workstream here, or a rebind.
        if call.startswith("rebind:"):
            return here(request) | {
                "action": "rebind_workstream",
                "workstream_id": ids[call.split(":")[1]],
                "expected_revision": 1,
                "confirmed": True,
            }
        into = {"project": ids["project"]} if call == "attach_workstream" else {}
        return here(request) | {"action": call, "confirmed": True} | into

    def check_shape(request, response):
        # One shape: fixed message, what exists (workstreams with roles) and next.
        assert response["state"] in INIT_MESSAGES and "queue" not in response
        taken = any("name_holder" in ws["roles"] for ws in response["workstreams"])
        assert response["message"] == (
            INIT_MESSAGES[response["state"]] + (INIT_NAME_TAKEN if taken else "") + INIT_NEXT
        )
        assert response["path"] == request["path"]
        assert response["branch"] == request.get("branch")
        roles = {ws["id"]: ws["roles"] for ws in response["workstreams"]}
        if "workstream_id" in request:
            # A requested workstream is reported as itself, never swapped for another.
            assert "requested" in roles[request["workstream_id"]]
        for item in response["next"]:
            assert set(item) == {"choice", "tool", "arguments"}
            assert item["choice"] in {"resume", "check", "create", "rebind", "unarchive"}
            arguments = item["arguments"]
            if item["tool"] == "init":
                # A resume or rebind of an archived workstream says include_archived=true.
                moved = arguments.get("workstream_id")
                listed = next((w for w in response["workstreams"] if w["id"] == moved), None)
                if listed and item["choice"] in {"resume", "rebind"}:
                    assert bool(arguments.get("include_archived")) == archived(listed)
            else:
                assert item == {
                    "choice": "unarchive",
                    "tool": "archive_workstream",
                    "arguments": {
                        "workstream_id": arguments["workstream_id"],
                        "archived": False,
                        "expected_revision": arguments["expected_revision"],
                    },
                }

    async def follow(call, request, response, item):
        # Make one offered call exactly as given; it must reach what its choice says.
        arguments = item["arguments"]
        listed = {ws["id"]: ws for ws in response["workstreams"]}
        if item["choice"] == "unarchive":
            restored = await call("archive_workstream", reason="back in use", **arguments)
            assert restored["changed"] and not restored["archive"]["archived"]
            followed = await call("init", **here(request))
            assert followed["workstream"]["id"] == arguments["workstream_id"]
        else:
            followed = await call("init", **arguments)
        if item["choice"] == "check":
            # A check is discovery: it changes nothing and returns its own calls.
            assert followed["state"] != "ready" and followed["next"]
            check_shape(arguments, followed)
            return followed
        assert followed["state"] == "ready", (item, followed)
        ws = followed["workstream"]
        # The followed call keeps an archived workstream archived unless it unarchives it.
        assert archived(ws) == bool(arguments.get("include_archived"))
        if archived(ws):
            assert "Archived workstream resumed on request" in followed["message"]
        if item["choice"] == "resume":
            assert ws["id"] == arguments["workstream_id"] and not followed["changed"]
            assert ws["checkout_path"] == arguments["path"]
            return followed
        if item["choice"] == "rebind":
            assert ws["id"] == arguments["workstream_id"] and followed["changed"]
        if item["choice"] == "create":
            assert followed["changed"] and ws["id"] not in listed
            action = arguments["action"]
            if action == "attach_workstream":
                assert followed["project"]["id"] == arguments["project"]
            elif action == "new_workstream":
                assert followed["project"]["id"] == response["project"]["id"]
            else:
                assert response["project"] is None
                assert followed["project"]["id"] not in {p["id"] for p in response["projects"]}
        assert ws["checkout_path"] == response["path"]
        assert ws["branch"] == request.get("branch")
        # The followed call is now a stable binding: a plain resume returns it unchanged
        # (with include_archived=true while the followed workstream is archived).
        plain = here(request)
        if archived(ws):
            assert (await call("init", **plain))["state"] == "archived"
            plain["include_archived"] = True
        resumed = await call("init", **plain)
        assert resumed["state"] == "ready" and not resumed["changed"]
        assert resumed["workstream"]["id"] == ws["id"]
        return followed

    async def reach(label, path=()):
        # A fresh database at the scenario's response, then the checks along path.
        call = tools()
        ids = await setup(call)
        build, state, _, _ = scenarios[label]
        request = build(ids)
        response = await call("init", **request)
        assert response["state"] == state, (label, response)
        check_shape(request, response)
        for index in path:
            request = response["next"][index]["arguments"]
            response = await call("init", **request)
        return call, ids, request, response

    async def follow_all(label, path=()):
        # Follow every offered call, each from the same starting state in its own database.
        count = 0
        _, _, _, response = await reach(label, path)
        for index, offered in enumerate(response["next"]):
            # IDs differ per database, so the call is taken from the fresh response.
            call, _, request, response = await reach(label, path)
            item = response["next"][index]
            assert item["choice"] == offered["choice"], label
            await follow(call, request, response, item)
            count += 1
            if item["choice"] == "check" and not path:
                count += await follow_all(label, (*path, index))
        return count

    async def exercise():
        followed = withheld_count = 0
        for label, (_, _, choices, withheld) in scenarios.items():
            _, _, _, response = await reach(label)
            assert [item["choice"] for item in response["next"]] == choices, label
            # A rebind across projects fails on the project; every other left-out call
            # fails on the name, which the message then mentions.
            codes = {
                left: "workstream_project_mismatch"
                if left == "rebind:foreign"
                else "workstream_exists"
                for left in withheld
            }
            taken = "workstream_exists" in codes.values()
            assert (INIT_NAME_TAKEN in response["message"]) == taken, label
            followed += await follow_all(label)
            for left in withheld:
                # A left-out call really would fail as reported, and changes nothing.
                call, ids, request, response = await reach(label)
                error = await call.fail("init", **left_out(request, response, ids, left))
                assert codes[left] in error, (label, left, error)
                again = await call("init", **request)
                assert again == response | {"runtime": again["runtime"]}
                withheld_count += 1
        assert (followed, withheld_count) == (118, 16)

        # The branch-bound-elsewhere report lists both workstreams, not one for the other.
        call = tools()
        ids = await setup(call)
        report = await call("init", path=repo, branch="release", workstream_id=ids["feature"])
        roles = {ws["id"]: ws for ws in report["workstreams"]}
        assert roles[ids["feature"]]["roles"] == ["requested"]
        assert roles[ids["release"]]["roles"] == ["bound"]
        assert roles[ids["release"]]["checkout_path"] == other
        # A confirmed rebind of the requested workstream onto the occupied branch reports the
        # same mismatch and changes nothing.
        refused = await call(
            "init",
            path=repo,
            branch="release",
            action="rebind_workstream",
            workstream_id=ids["feature"],
            expected_revision=roles[ids["feature"]]["revision"],
            confirmed=True,
        )
        assert refused["state"] == "mismatch" and refused["next"] == report["next"]
        assert refused["workstreams"] == report["workstreams"]
        kept = await call("init", path=other, branch="release")
        assert kept["workstream"]["id"] == ids["release"] and not kept["changed"]
        # An unknown requested ID is an unknown-workstream error, not a binding report.
        assert "unknown_workstream" in await call.fail(
            "init", path=repo, branch="release", workstream_id="wst_nope"
        )
        assert "unknown_workstream" in await call.fail(
            "init",
            path=repo,
            branch="release",
            action="rebind_workstream",
            workstream_id="wst_nope",
            expected_revision=1,
            confirmed=True,
        )

    run(exercise())


def test_creating_init_actions_reject_an_unused_workstream_id(mcp, tmp_path):
    _, _, call, fail = mcp
    repo, loose = str(tmp_path / "repo"), str(tmp_path / "loose")

    async def exercise():
        made = await call("init", path=repo, branch="main", action="create_project", confirmed=True)
        project, main = made["project"]["id"], made["workstream"]["id"]
        foreign = await call(
            "init", path=str(tmp_path / "f"), branch="main", action="create_project", confirmed=True
        )
        attempts = (
            {"path": repo, "branch": "topic", "action": "new_workstream"},
            {"path": loose, "branch": "topic", "action": "attach_workstream", "project": project},
            {"path": loose, "branch": "topic", "action": "create_project"},
        )
        for attempt in attempts:
            for confirmed in (True, False):
                assert "unknown_workstream" in await fail(
                    "init", **attempt, workstream_id="wst_nope", confirmed=confirmed
                )
                for workstream_id in (main, foreign["workstream"]["id"]):
                    error = await fail(
                        "init", **attempt, workstream_id=workstream_id, confirmed=confirmed
                    )
                    assert "workstream_id_not_used" in error and attempt["action"] in error
        # Nothing was created or attached by the rejected calls.
        assert (await call("init", path=repo, branch="topic"))["state"] == "new_branch"
        assert (await call("init", path=loose, branch="topic"))["state"] == (
            "unregistered_checkout"
        )
        assert len((await call("list_workstreams", project=project))["items"]) == 1
        # An ID that already is exactly this binding is an idempotent retry, not a rejection.
        for action in ("new_workstream", "create_project"):
            again = await call(
                "init", path=repo, branch="main", action=action, workstream_id=main, confirmed=True
            )
            assert again["state"] == "ready" and not again["changed"]
            assert again["workstream"]["id"] == main

    run(exercise())


def test_new_branch_withholds_a_new_workstream_whose_name_is_taken(mcp, tmp_path):
    _, _, call, fail = mcp
    repo = str(tmp_path / "repo")

    async def exercise():
        await call("init", path=repo, branch="main", action="create_project", confirmed=True)
        wip = await call(
            "init",
            path=repo,
            branch="wip-branch",
            workstream_name="wip",
            action="new_workstream",
            confirmed=True,
        )
        fresh = await call("init", path=repo, branch="topic")
        assert [item["choice"] for item in fresh["next"]] == ["create", "rebind", "rebind"]
        assert fresh["next"][0]["arguments"] == {
            "path": repo,
            "branch": "topic",
            "action": "new_workstream",
            "confirmed": True,
        }
        assert all(ws["roles"] == ["candidate"] for ws in fresh["workstreams"])
        taken = await call("init", path=repo, branch="wip")
        assert taken["state"] == "new_branch"
        assert taken["next"] == [
            {
                "choice": "rebind",
                "tool": "init",
                "arguments": {
                    "path": repo,
                    "branch": "wip",
                    "action": "rebind_workstream",
                    "workstream_id": wip["workstream"]["id"],
                    "expected_revision": wip["workstream"]["revision"],
                    "confirmed": True,
                },
            }
        ]
        holder = [ws for ws in taken["workstreams"] if "name_holder" in ws["roles"]]
        assert [ws["id"] for ws in holder] == [wip["workstream"]["id"]]
        assert "name_holder" in taken["message"]
        assert "workstream_exists" in await fail(
            "init", path=repo, branch="wip", action="new_workstream", confirmed=True
        )
        rebound = await call(
            "init",
            path=repo,
            branch="wip",
            action="rebind_workstream",
            workstream_id=wip["workstream"]["id"],
            expected_revision=wip["workstream"]["revision"],
            confirmed=True,
        )
        assert rebound["state"] == "ready" and rebound["workstream"]["branch"] == "wip"
        assert rebound["workstream"]["id"] == wip["workstream"]["id"]

    run(exercise())


def test_init_error_paths_raise_their_own_codes(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    repo, loose = str(tmp_path / "repo"), str(tmp_path / "loose")
    made = store.init(repo, branch="main", action="create_project", confirmed=True)
    project, main = made["project"]["id"], made["workstream"]
    feature = store.init(repo, branch="feature", action="new_workstream", confirmed=True)

    def raises(code, *args, **kwargs):
        with pytest.raises(TaskError, match=code):
            store.init(*args, **kwargs)

    raises("absolute_path_required", "relative/repo", branch="main")
    raises("workstream_name_required", repo)
    raises("invalid_branch", repo, branch=" ")
    raises("invalid_workstream_name", repo, workstream_name=" ")
    raises("invalid_include_inactive", repo, branch="main", include_inactive="yes")
    raises("project_not_initialized", loose, branch="main", project="nope")
    raises("unknown_workstream", loose, branch="main", workstream_id="wst_nope")
    raises("invalid_init_action", repo, branch="topic", action="adopt", confirmed=True)
    raises(
        "checkout_already_registered", repo, branch="topic", action="create_project", confirmed=True
    )
    # The loose checkout is not registered: project= is the unused argument.
    raises(
        "project_not_used",
        loose,
        branch="topic",
        action="create_project",
        project=project,
        confirmed=True,
    )
    raises("project_required", loose, branch="topic", action="attach_workstream", confirmed=True)
    raises(
        "checkout_not_attached",
        loose,
        branch="topic",
        action="new_workstream",
        project=project,
        confirmed=True,
    )
    raises(
        "checkout_already_attached",
        repo,
        branch="topic",
        action="attach_workstream",
        project=project,
        confirmed=True,
    )
    raises(
        "revision_conflict: re-read the scope",
        repo,
        branch="topic",
        action="new_workstream",
        scope_expression="main",
        expected_revision=99,
        confirmed=True,
    )
    raises(
        "workstream_id_required", repo, branch="topic", action="rebind_workstream", confirmed=True
    )
    raises(
        "revision_conflict: re-read the workstream",
        repo,
        branch="topic",
        action="rebind_workstream",
        workstream_id=main["id"],
        expected_revision=99,
        confirmed=True,
    )
    raises(
        "workstream_exists",
        repo,
        branch="topic",
        workstream_name="feature",
        action="rebind_workstream",
        workstream_id=main["id"],
        expected_revision=main["revision"],
        confirmed=True,
    )
    raises(
        "workstream_exists",
        repo,
        branch="topic",
        workstream_name="feature",
        action="new_workstream",
        confirmed=True,
    )
    raises(
        "workstream_id_not_used",
        repo,
        branch="topic",
        action="new_workstream",
        workstream_id=feature["workstream"]["id"],
        confirmed=True,
    )
    # None of the rejected calls changed the recorded bindings.
    assert store.init(repo, branch="topic")["state"] == "new_branch"
    assert store.init(loose, branch="topic")["state"] == "unregistered_checkout"
    assert store.init(repo, branch="main")["workstream"] == main


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
