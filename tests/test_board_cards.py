"""Boards list active work as slim cards; include groups restore the dropped detail."""

import asyncio
import json

import pytest

from task_mcp.server import create_server
from task_mcp.store import CARD_INCLUDE_GROUPS, Store, TaskError
from task_mcp.viewer import dispatch

SLIM_KEYS = {
    "id",
    "title",
    "summary",
    "state",
    "revision",
    "position",
    "blockers",
    "question_count",
    "concern_count",
    "rejected",
}
GROUP_KEYS = {"progress", "complete", "project_count"}
GROUP_FIELDS = {
    "blockers": {"prerequisites", "gate_diagnostics", "pending_proposal_count"},
    "attempt": {
        "attempt",
        "attempt_counts",
        "alternative_attempt_count",
        "attempt_scope",
        "selected_attempt_id",
        "latest_rejection",
    },
    "concerns": {
        "concern_count",
        "concerns",
        "concern_attempt_total",
        "concern_attempt_references",
        "concern_attempts_has_more",
    },
    "workstreams": {"workstreams", "adopted", "in_scope"},
    "ids": {
        "project_id",
        "object_type",
        "status",
        "spec_revision",
        "summary_spec_revision",
        "summary_stale",
        "order_key",
        "parent_group_id",
        "specification_complete",
        "view",
        "workstream_id",
    },
}
# Old card fields that now carry a clearer name or a complete list.
RENAMED = {
    "workstream_ids": "workstreams",
    "attempt_reference": "attempt",
    "aggregate_attempt_counts": "attempt_counts",
    "unresolved_count": "question_count",
    "prerequisite_count": "prerequisites",
    "prerequisites_has_more": "prerequisites",
    "workstream_order_key": "position",
}


def etag(store, task_id):
    return store.get_tasks([task_id])["items"][0]["specification_etag"]


def record(store, task, ws, concerns=None):
    return store.record_result(
        task["id"],
        ws,
        store.get_tasks([task["id"]])["items"][0]["revision"],
        "synthetic worker",
        "Synthetic result",
        "Synthetic evidence",
        [{"kind": "artifact", "reference": "synthetic.txt"}],
        "Synthetic checks",
        etag(store, task["id"]),
        concerns,
    )


@pytest.fixture
def board(tmp_path):
    store = Store(tmp_path / "board.sqlite3")
    setup = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    project, ws = setup["project"]["id"], setup["workstream"]["id"]
    path = setup["workstream"]["checkout_path"]

    def create(title, **kwargs):
        return store.create_task(project, title, workstream_id=ws, **kwargs)

    tasks = {"ready": create("Ready", summary="Short intent")}
    question = create("Question")
    tasks["question"] = store.add_unresolved(question["id"], 1, "Which format?")
    tasks["blocker"] = create("Blocker")
    blocked = create("Blocked")
    tasks["blocked"] = store.add_prerequisite(blocked["id"], 1, tasks["blocker"]["id"])
    tasks["review"] = create("Review")
    record(store, tasks["review"], ws, [{"kind": "design", "text": "Synthetic doubt"}])
    tasks["signoff"] = create("Signoff")
    attempt = record(store, tasks["signoff"], ws)
    store.record_review(attempt["id"], 1, "synthetic reviewer", "pass", "Checked")
    tasks["rework"] = create("Rework")
    attempt = record(store, tasks["rework"], ws)
    store.record_review(attempt["id"], 1, "synthetic reviewer", "rework", "Fix it")
    tasks["done"] = create("Done")
    attempt = record(store, tasks["done"], ws)
    reviewed = store.record_review(attempt["id"], 1, "synthetic reviewer", "pass", "Checked")
    store.signoff_task(
        tasks["done"]["id"],
        store.get_tasks([tasks["done"]["id"]])["items"][0]["revision"],
        "approve",
        "Synthetic verdict",
        attempt["id"],
        reviewed["revision"],
    )
    for status in ("deferred", "dropped"):
        task = create(status.title())
        tasks[status] = store.set_disposition(task["id"], 1, status, "Synthetic decision")
    return store, project, ws, path, tasks


def test_boards_hide_closed_work_and_count_it_per_status(board):
    store, project, ws, path, tasks = board
    closed = {tasks[s]["id"] for s in ("done", "deferred", "dropped")}
    hidden = {"done": 1, "deferred": 1, "dropped": 1}
    listed = store.list_tasks(project, ws)
    assert closed.isdisjoint(card["id"] for card in listed["items"])
    assert listed["total"] == len(listed["items"]) == 7 and listed["hidden"] == hidden
    status = store.workstream_status(ws)
    assert [c["id"] for c in status["items"]] == [c["id"] for c in listed["items"]]
    assert status["total"] == 7 and status["hidden"] == hidden
    assert status["status"]["counts"]["done"] == 1  # Summary counts stay complete.
    ready = store.init(path, branch="main")
    assert ready["queue"] == listed["items"][:10] and ready["queue_total"] == 7
    assert ready["queue_hidden"] == hidden and ready["queue_next_offset"] is None
    baseline = store.list_tasks(project)
    assert baseline["hidden"] == hidden and baseline["total"] == 7
    # One argument lists everything again, in workstream order with stable positions.
    everything = store.list_tasks(project, ws, include_inactive=True)
    assert "hidden" not in everything and everything["total"] == 10
    assert [c["position"] for c in everything["items"]] == list(range(1, 11))
    assert {c["id"]: c["state"] for c in everything["items"] if c["id"] in closed} == {
        tasks[s]["id"]: s for s in ("done", "deferred", "dropped")
    }
    positions = {c["id"]: c["position"] for c in everything["items"]}
    assert all(c["position"] == positions[c["id"]] for c in listed["items"])
    assert store.workstream_status(ws, include_inactive=True)["total"] == 10
    assert len(store.init(path, branch="main", include_inactive=True)["queue"]) == 10
    assert "queue_hidden" not in store.init(path, branch="main", include_inactive=True)
    # An explicit closed state is itself a request for that history.
    for status_name in ("done", "deferred", "dropped"):
        only = store.list_tasks(project, ws, state=status_name)
        assert [c["id"] for c in only["items"]] == [tasks[status_name]["id"]]
        assert "hidden" not in only
    with pytest.raises(TaskError, match="invalid_include_inactive"):
        store.list_tasks(project, ws, include_inactive="yes")


def test_state_words_and_existing_filters(board):
    store, project, ws, _, tasks = board
    cards = {c["id"]: c for c in store.list_tasks(project, ws)["items"]}
    expected = {
        "ready": "ready",
        "question": "question",
        "blocker": "ready",
        "blocked": "blocked",
        "review": "review",
        "signoff": "signoff",
        "rework": "rework",
    }
    assert {name: cards[tasks[name]["id"]]["state"] for name in expected} == expected
    assert cards[tasks["blocked"]["id"]]["blockers"] == [tasks["blocker"]["id"]]
    assert cards[tasks["question"]["id"]]["question_count"] == 1
    assert cards[tasks["review"]["id"]]["concern_count"] == 1
    assert cards[tasks["rework"]["id"]]["rejected"] is True
    assert "rejected" not in cards[tasks["signoff"]["id"]]

    def filtered(state):
        return {c["id"] for c in store.list_tasks(project, ws, state=state)["items"]}

    assert filtered("question") == filtered("unresolved_items") == {tasks["question"]["id"]}
    assert filtered("blocked") == filtered("prerequisites") == {tasks["blocked"]["id"]}
    assert filtered("rework") == {tasks["rework"]["id"]}
    # The older ready view still includes tasks returned for rework.
    assert filtered("ready") == {tasks[n]["id"] for n in ("ready", "blocker", "rework")}
    assert filtered("signoff") == {tasks["signoff"]["id"]}
    assert filtered("review") == {tasks["review"]["id"]}


def test_slim_cards_are_small_and_omit_empty_defaults(board):
    store, project, ws, _, tasks = board
    for card in store.list_tasks(project, ws, include_inactive=True)["items"]:
        assert set(card) <= SLIM_KEYS
        assert {"id", "title", "state", "revision", "position"} <= card.keys()
        assert len(json.dumps(card, separators=(",", ":"))) < 250
    plain = store.list_tasks(project, ws, state="review")["items"][0]
    assert set(plain) == {"id", "title", "state", "revision", "position", "concern_count"}
    ready = next(
        c for c in store.list_tasks(project, ws)["items"] if c["id"] == tasks["ready"]["id"]
    )
    assert ready["summary"] == "Short intent"
    unscoped = store.read_tasks([tasks["ready"]["id"]])["items"][0]
    assert "position" not in unscoped and unscoped["state"] == "ready"


@pytest.mark.parametrize("group", CARD_INCLUDE_GROUPS)
def test_each_include_group_returns_its_fields_on_both_reads(board, group):
    store, project, ws, _, tasks = board
    listed = store.list_tasks(project, ws, include=[group])["items"]
    read = store.read_tasks([c["id"] for c in listed], workstream_id=ws, include=[group])
    assert read["items"] == listed
    for card in listed:
        assert GROUP_FIELDS[group] <= card.keys() <= SLIM_KEYS | GROUP_FIELDS[group]
    cards = {c["id"]: c for c in listed}
    if group == "blockers":
        reference = cards[tasks["blocked"]["id"]]["prerequisites"][0]
        assert reference["title"] == "Blocker" and reference["state"] == "open"
        assert reference["satisfied"] is False
    if group == "attempt":
        attempt = cards[tasks["signoff"]["id"]]["attempt"]
        assert attempt["state"] == "passed" and attempt["implementer"] == "synthetic worker"
        assert attempt["summary"] == "Synthetic result"
        assert cards[tasks["rework"]["id"]]["latest_rejection"]["verdict"] == "rework"
        assert cards[tasks["ready"]["id"]]["attempt"] is None
    if group == "concerns":
        (concern,) = cards[tasks["review"]["id"]]["concerns"]
        assert concern["text"] == "Synthetic doubt" and concern["kind"] == "design"
        assert concern["author"] == "synthetic worker"
    if group == "workstreams":
        card = cards[tasks["ready"]["id"]]
        assert card["workstreams"] == [{"id": ws, "position": card["position"]}]
        assert card["adopted"] and card["in_scope"]
    if group == "ids":
        card = cards[tasks["ready"]["id"]]
        assert card["project_id"] == project and card["spec_revision"] == 1
        assert card["status"] == "open" and card["view"] == "ready"


def test_every_old_card_field_is_reachable(board):
    store, project, ws, _, tasks = board
    with store._connect() as db:
        for name in ("signoff", "blocked", "done"):
            task = store._task(db, tasks[name]["id"], {})
            old = set(store._card(db, task, ws)) | {"workstream_order_key"}
            old_unscoped = set(store._card(db, task))
            card = store.read_tasks(
                [task["id"]], workstream_id=ws, include=list(CARD_INCLUDE_GROUPS)
            )
            unscoped = store.read_tasks([task["id"]], include=list(CARD_INCLUDE_GROUPS))
            reachable = set(card["items"][0]) | set(unscoped["items"][0]) | SLIM_KEYS
            missing = {RENAMED.get(k, k) for k in old | old_unscoped} - reachable
            assert missing == set()


def test_groups_are_recognisable_and_unknown_groups_are_rejected(board):
    store, project, ws, _, tasks = board
    group = store.create_group(ws, "Shared group", summary="Group intent")
    store.add_group_member(group["id"], group["revision"], tasks["ready"]["id"], 1)
    card = store.read_tasks([group["id"]], include=["ids", "workstreams"])["items"][0]
    assert card["state"] == "group" and card["progress"]["total"] == 1
    assert card["complete"] is False and card["project_count"] == 1
    assert card["object_type"] == "group" and "origin_project_id" in card
    assert card["project_id"] is None and card["workstreams"] == [{"id": ws, "position": None}]
    assert set(store.read_tasks([group["id"]])["items"][0]) <= SLIM_KEYS | GROUP_KEYS
    for include in (["history"], "ids", [1]):
        with pytest.raises(TaskError, match="invalid_include"):
            store.list_tasks(project, ws, include=include)
        with pytest.raises(TaskError, match="invalid_include"):
            store.read_tasks([group["id"]], include=include)


def test_full_specification_reads_stay_complete(board):
    store, _, ws, _, tasks = board
    plain = store.read_tasks([tasks["review"]["id"]], True, ws)["items"][0]
    extended = store.read_tasks(
        [tasks["review"]["id"]], True, ws, include=list(CARD_INCLUDE_GROUPS)
    )["items"][0]
    # Include groups only add fields a specification lacks; its own fields win.
    assert {k: extended[k] for k in plain} == plain
    assert extended["workstreams"][0]["id"] == ws and extended["attempt"]["state"] == "review"
    assert "specification_etag" in plain and "body" in plain


def test_viewer_board_keeps_full_cards_and_closed_tasks(board):
    store, project, ws, _, tasks = board
    page = dispatch(store, "tasks", {"project": project, "workstream_id": ws, "limit": 100})
    assert page["total"] == 10 and "hidden" not in page
    done = next(c for c in page["items"] if c["id"] == tasks["done"]["id"])
    assert done["view"] == "done" and done["workstream_order_key"] == 8
    assert {"gate_diagnostics", "attempt_reference", "latest_rejection"} <= done.keys()


def test_mcp_tools_expose_defaults_and_include_groups(board):
    store, project, ws, path, tasks = board
    server = create_server(store, tracing=False)

    async def exercise():
        async def call(name, **args):
            result = await server.call_tool(name, args)
            assert not result.is_error, result.content
            return result.structured_content

        listed = await call("list_tasks", project=project, workstream_id=ws)
        assert listed["hidden"] == {"done": 1, "deferred": 1, "dropped": 1}
        assert listed["items"] == store.list_tasks(project, ws)["items"]
        everything = await call(
            "list_tasks", project=project, workstream_id=ws, include_inactive=True
        )
        assert everything["total"] == 10
        detailed = await call(
            "get_tasks", ids=[tasks["signoff"]["id"]], workstream_id=ws, include=["attempt"]
        )
        assert detailed["items"][0]["attempt"]["state"] == "passed"
        status = await call("workstream_status", workstream_id=ws, include_inactive=True)
        assert status["total"] == 10
        ready = await call("init", path=path, branch="main")
        assert ready["queue_hidden"] == listed["hidden"]
        with pytest.raises(Exception, match="blockers.*attempt.*concerns.*workstreams.*ids"):
            await server.call_tool(
                "list_tasks", {"project": project, "workstream_id": ws, "include": ["history"]}
            )

    asyncio.run(exercise())
