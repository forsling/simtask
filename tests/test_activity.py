"""Activity: a task's or group's meaningful history, newest first, in stable pages."""

import ast
import asyncio
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import task_mcp.store as store_module
from task_mcp.server import create_server
from task_mcp.store import (
    ACTIVITY_HIDDEN,
    ACTIVITY_KINDS,
    ACTIVITY_PAGE,
    Store,
    TaskError,
)
from task_mcp.viewer import dispatch

SOURCE = Path(store_module.__file__).parent
ACTION_SHAPE = r"[a-z_]+\.[a-z_]+"


def emitted_actions(sources):
    """Every audit action the code can write, read from the code itself.

    Store._run(action, ...) is the audited entry point: its action must be a string
    literal (or a conditional between literals) so this scan sees it. A raw
    INSERT INTO events (as a migration may write) contributes the action-shaped
    literals among its parameters.
    """
    found, opaque = set(), []

    def literals(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return literals(node.body) | literals(node.orelse)
        return None

    for path, text in sources:
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", None))
            if name == "_run":
                values = literals(node.args[0])
                if values is None:
                    opaque.append(f"{path}:{node.lineno}")
                else:
                    found |= values
            first = node.args[0]
            if (
                name == "execute"
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and "INSERT INTO events" in first.value
                and len(node.args) > 1
            ):
                for inner in ast.walk(node.args[1]):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        if re.fullmatch(ACTION_SHAPE, inner.value):
                            found.add(inner.value)
    return found, opaque


def source_files():
    return [(path.name, path.read_text()) for path in sorted(SOURCE.glob("*.py"))]


def test_every_action_the_store_can_emit_is_classified():
    actions, opaque = emitted_actions(source_files())
    assert not opaque, f"_run actions must be literals so they can be classified: {opaque}"
    # The scan really sees the store's writes and reads.
    assert {"attempt.recorded", "task.signoff", "tasks.read", "activity.read"} <= actions
    unclassified = sorted(actions - ACTIVITY_KINDS.keys() - ACTIVITY_HIDDEN.keys())
    assert not unclassified, (
        f"classify {unclassified} in ACTIVITY_KINDS (shown in Activity) or ACTIVITY_HIDDEN"
    )
    assert not ACTIVITY_KINDS.keys() & ACTIVITY_HIDDEN.keys()


def test_the_scan_flags_new_and_migration_actions():
    sample = """
def create(self):
    return self._run("note.created", request, operation)
def update(self, archived):
    return self._run("note.archived" if archived else "note.updated", request, op)
def migrate(db):
    db.execute(
        "INSERT INTO events (timestamp,actor,action,outcome) VALUES (?,?,?,?)",
        (timestamp(), "schema-12-migration", "note.migrated", "ok"),
    )
def hidden(self, action):
    return self._run(action, request, operation)
"""
    actions, opaque = emitted_actions([("sample.py", sample)])
    assert actions == {"note.created", "note.archived", "note.updated", "note.migrated"}
    assert opaque == ["sample.py:12"]


@pytest.fixture
def ready(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="simon")
    setup = store.init(str(tmp_path), "main", action="create_project", confirmed=True)
    return store, setup["project"]["id"], setup["workstream"]["id"]


def full(store, task_id):
    return store.get_tasks([task_id])["items"][0]


def record(store, task_id, ws, summary="Delivered"):
    current = full(store, task_id)
    return store.record_result(
        task_id,
        ws,
        current["revision"],
        "worker",
        summary,
        "Actual proof",
        artifacts=[{"kind": "artifact", "reference": "tests/test_activity.py"}],
        verification="Actual proof",
        specification_etag=current["specification_etag"],
    )


def activity(store, task_id, **arguments):
    return dispatch(store, "activity", {"task_id": task_id, **arguments})


def walk(store, task_id):
    """Every page from the newest, by cursor."""
    pages, cursor = [], None
    while True:
        page = activity(store, task_id, **({"cursor": cursor} if cursor else {}))
        pages.append(page)
        cursor = page.get("next_cursor")
        if cursor is None:
            return pages


def churn(store, task_id, count):
    """Meaningful edits interleaved with reads and failed requests."""
    for index in range(count):
        current = full(store, task_id)
        store.get_tasks([task_id])
        store.list_tasks(store.get_tasks([task_id])["items"][0]["project_id"])
        with pytest.raises(TaskError):
            store.add_unresolved(task_id, current["revision"] + 99, "stale")
        store.add_unresolved(task_id, current["revision"], f"Question {index}")


def test_a_task_shows_meaningful_entries_newest_first(ready):
    store, project, ws = ready
    task = store.create_task(project, "Scope", body="Exact", acceptance_criteria="Proof")
    store.add_to_workstream(task["id"], ws, task["revision"])
    current = full(store, task["id"])
    store.add_unresolved(task["id"], current["revision"], "Which colour?")
    current = full(store, task["id"])
    item = current["unresolved_items"][0]["id"]
    store.resolve_unresolved(task["id"], current["revision"], item, "Blue")
    current = full(store, task["id"])
    store.update_task(
        task["id"],
        current["revision"],
        {"body": "Exact, blue"},
        specification_etag=current["specification_etag"],
    )
    attempt = record(store, task["id"], ws)
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Checked  the\nproof")
    current = full(store, task["id"])
    store.signoff_task(task["id"], current["revision"], "approve", "Looks right", attempt["id"], 2)
    with pytest.raises(TaskError):
        store.signoff_task(task["id"], 1, "approve", "stale", attempt["id"], 2)
    store.get_attempt(attempt["id"])

    page = activity(store, task["id"])
    kinds = [entry["kind"] for entry in page["items"]]
    assert kinds == [
        "signoff",
        "review",
        "result",
        "updated",
        "question_resolved",
        "question_added",
        "workstream_added",
        "created",
    ]
    sequences = [entry["sequence"] for entry in page["items"]]
    assert sequences == sorted(sequences, reverse=True)
    assert page["newest_sequence"] == sequences[0] and "next_cursor" not in page
    signoff, review, result, updated, resolved, added, joined, created = page["items"]
    assert signoff | {"decision": "approve", "reasons": "Looks right"} == signoff
    assert signoff["attempt_id"] == attempt["id"] and signoff["disposition"] == "done"
    assert review | {"verdict": "pass", "reviewer": "reviewer"} == review
    assert review["reasons"] == "Checked the proof"
    assert result == result | {
        "attempt_id": attempt["id"],
        "workstream_id": ws,
        "workstream_name": "main",
        "spec_revision": 2,
        "state": "passed",  # The attempt's current state, not the state when recorded.
        "implementer": "worker",
        "summary": "Delivered",
        "actor": "simon",
    }
    assert "evidence" not in result and "verification" not in result
    assert updated["changed"] == ["body"] and updated["spec_revision"] == 2
    assert resolved | {"text": "Which colour?", "note": "Blue", "item_id": item} == resolved
    assert added["text"] == "Which colour?"
    assert joined["workstream_id"] == ws and joined["workstream_name"] == "main"
    assert created["title"] == "Scope"
    # Reads, including Activity's own, and failed requests are recorded but never shown.
    with closing(sqlite3.connect(store.path)) as db:
        hidden = db.execute(
            "SELECT count(*) FROM events WHERE task_id=? AND (outcome='error' OR action IN "
            "('tasks.read','attempt.read','activity.read'))",
            (task["id"],),
        ).fetchone()[0]
    assert hidden >= 3


def test_pages_are_full_until_the_last_and_cover_everything_once(ready):
    store, project, ws = ready
    task = store.create_task(project, "Busy", workstream_id=ws)
    churn(store, task["id"], 45)
    pages = walk(store, task["id"])
    sizes = [len(page["items"]) for page in pages]
    assert sizes == [20, 20, 6]  # 45 questions, the workstream and the creation.
    entries = [entry for page in pages for entry in page["items"]]
    sequences = [entry["sequence"] for entry in entries]
    assert len(set(sequences)) == len(sequences) == 46
    assert sequences == sorted(sequences, reverse=True)
    assert [entry["kind"] for entry in entries][-1] == "created"
    assert {page["newest_sequence"] for page in pages} == {sequences[0]}
    assert all(page["next_cursor"] == page["items"][-1]["sequence"] for page in pages[:-1])


def test_writes_between_page_loads_cause_no_duplicates_or_gaps(ready):
    store, project, ws = ready
    task = store.create_task(project, "Moving", workstream_id=ws)
    churn(store, task["id"], 45)
    before = [e["sequence"] for page in walk(store, task["id"]) for e in page["items"]]
    first = activity(store, task["id"])
    churn(store, task["id"], 25)  # Newer entries arrive between page loads.
    second = activity(store, task["id"], cursor=first["next_cursor"])
    third = activity(store, task["id"], cursor=second["next_cursor"])
    loaded = [e["sequence"] for page in (first, second, third) for e in page["items"]]
    assert loaded == before and "next_cursor" not in third
    assert second["newest_sequence"] > first["newest_sequence"]
    # A fresh read from the top shows the 25 newer entries above the loaded ones.
    fresh = [e["sequence"] for page in walk(store, task["id"]) for e in page["items"]]
    assert fresh[25:] == before and len(fresh) == len(before) + 25


def test_scanning_is_bounded_and_resumes_without_gaps(ready, monkeypatch):
    store, project, ws = ready
    task = store.create_task(project, "Read often", workstream_id=ws)
    for index in range(3):
        for _ in range(12):
            store.get_tasks([task["id"]])
        current = full(store, task["id"])
        store.add_unresolved(task["id"], current["revision"], f"Question {index}")
    complete = [e["sequence"] for page in walk(store, task["id"]) for e in page["items"]]
    monkeypatch.setattr(store_module, "ACTIVITY_SCAN_LIMIT", 10)
    pages = walk(store, task["id"])
    assert any(page.get("scan_limited") for page in pages)
    assert [e["sequence"] for page in pages for e in page["items"]] == complete


def legacy_import(store, task_id, ws, attempts):
    """What the 2026-09-26 TASKS.md migration left: one event and bare attempt rows."""
    with closing(sqlite3.connect(store.path)) as db:
        sequence = db.execute(
            "INSERT INTO events (timestamp,actor,action,outcome,project_id,task_id,"
            "request_json,after_json) VALUES ('2026-09-26T08:24:52.141186Z',"
            "'legacy-migration','legacy.task_imported','ok',NULL,?,'{}',?)",
            (task_id, json.dumps({"legacy_status": "H"})),
        ).lastrowid
        for index in range(attempts):
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,'unknown (legacy)',?,'{}',1,'passed',"
                "'unknown (legacy)','Historical review',NULL,1,?,?,'[]')",
                (
                    f"att_legacy{index}",
                    task_id,
                    ws,
                    f"Historical result {index}",
                    f"2026-09-26T08:24:52.16810{index}Z",
                    f"2026-09-26T08:24:52.16810{index}Z",
                ),
            )
        db.commit()
    return sequence


def test_imported_results_sit_with_the_import_and_live_results_appear_once(ready):
    store, project, ws = ready
    task = store.create_task(project, "Imported", workstream_id=ws)
    imported_at = legacy_import(store, task["id"], ws, 2)
    live = record(store, task["id"], ws, "Live result")
    page = activity(store, task["id"])
    rows = [(e["sequence"], e["kind"], e.get("attempt_id")) for e in page["items"]]
    created_at = page["items"][-1]["sequence"]
    assert rows == [
        (rows[0][0], "result", live["id"]),
        (imported_at, "result", "att_legacy1"),
        (imported_at, "result", "att_legacy0"),
        (imported_at, "imported", None),
        (created_at, "created", None),
    ]
    assert rows[0][0] > imported_at > created_at
    legacy = next(e for e in page["items"] if e.get("attempt_id") == "att_legacy0")
    assert legacy == legacy | {
        "imported": True,
        "actor": "legacy-migration",
        "state": "passed",
        "reviewer": "unknown (legacy)",
        "summary": "Historical result 0",
        "workstream_name": "main",
    }
    importing = next(e for e in page["items"] if e["kind"] == "imported")
    assert importing["imported_results"] == 2 and importing["summary"]


def test_an_import_unit_is_never_split_across_pages(ready):
    store, project, ws = ready
    task = store.create_task(project, "Imported later", workstream_id=ws)
    imported_at = legacy_import(store, task["id"], ws, 3)
    churn(store, task["id"], ACTIVITY_PAGE - 2)  # 18 newer entries leave room for 2.
    pages = walk(store, task["id"])
    first = [e["sequence"] for e in pages[0]["items"]]
    assert imported_at not in first and len(first) == ACTIVITY_PAGE - 2
    second = [(e["sequence"], e["kind"]) for e in pages[1]["items"]]
    assert second[:4] == [(imported_at, "result")] * 3 + [(imported_at, "imported")]
    assert pages[0]["next_cursor"] == first[-1] and "next_cursor" not in pages[1]


def test_a_target_result_returns_its_page_without_newer_history(ready):
    store, project, ws = ready
    task = store.create_task(project, "Targeted", workstream_id=ws)
    early = record(store, task["id"], ws, "Early")
    store.record_review(early["id"], 1, "reviewer", "rework", "Missing proof")
    churn(store, task["id"], 30)
    page = activity(store, task["id"], target=early["id"])
    assert page["items"][0]["attempt_id"] == early["id"]
    assert page["items"][0]["kind"] == "result" and page["items"][0]["state"] == "rework"
    assert page["newest_sequence"] > page["items"][0]["sequence"]
    assert [e["kind"] for e in page["items"]] == ["result", "created"]
    # Its page continues by the same cursor rules as any other.
    assert "next_cursor" not in page

    imported = store.create_task(project, "Imported target", workstream_id=ws)
    imported_at = legacy_import(store, imported["id"], ws, 1)
    churn(store, imported["id"], 25)
    page = activity(store, imported["id"], target="att_legacy0")
    assert [(e["sequence"], e["kind"]) for e in page["items"][:2]] == [
        (imported_at, "result"),
        (imported_at, "imported"),
    ]
    with pytest.raises(TaskError, match="unknown_attempt"):
        activity(store, task["id"], target="att_legacy0")  # Another task's result.
    with pytest.raises(TaskError, match="invalid_activity_request"):
        activity(store, task["id"], target=early["id"], cursor=5)
    with pytest.raises(TaskError, match="invalid_cursor"):
        activity(store, task["id"], cursor="5")


def test_groups_show_their_own_changes_and_members_their_decomposition(ready):
    store, project, ws = ready
    group = store.create_group(ws, "Initiative")
    member = store.create_task(project, "Loose member", workstream_id=ws)
    store.add_group_member(group["id"], group["revision"], member["id"], member["revision"])
    current = full(store, member["id"])
    store.add_unresolved(member["id"], current["revision"], "Member question")
    page = activity(store, group["id"])
    assert page["object_type"] == "group"
    assert [e["kind"] for e in page["items"]] == ["member_added", "created"]
    assert page["items"][0]["member_id"] == member["id"]

    parent = store.create_task(project, "To split", workstream_id=ws)
    store.decompose_task(parent["id"], parent["revision"], [{"title": "Part one"}])
    with closing(sqlite3.connect(store.path)) as db:
        (part,) = db.execute(
            "SELECT id FROM tasks WHERE parent_group_id=?", (parent["id"],)
        ).fetchone()
    feed = activity(store, part)["items"]
    assert feed[-1] | {"kind": "created", "via": "decomposition"} == feed[-1]
    assert feed[-1]["group_id"] == parent["id"]
    group_feed = activity(store, parent["id"])["items"]
    assert group_feed[0]["kind"] == "decomposed" and group_feed[0]["member_ids"] == [part]
    assert group_feed[0]["sequence"] == feed[-1]["sequence"]


def test_no_mcp_tool_reads_activity(tmp_path):
    server = create_server(Store(tmp_path / "tasks.sqlite3"), tracing=False)
    tools = asyncio.run(server.list_tools())
    assert not [tool.name for tool in tools if "activity" in tool.name]


def test_older_request_shapes_still_render(ready):
    store, project, ws = ready
    task = store.create_task(project, "Older", workstream_id=ws)
    older = [
        ("task.signoff", {"verdict": "approve", "user_note": "Signed off", "attempt_id": None}),
        ("task.signoff", {"verdict": "reject", "rejection": "revise", "user_note": "Rethink"}),
        ("gate.prerequisite_removed", {"blocked_by_ids": ["a", "b"], "note": "Done"}),
        ("task.accepted", {"user_note": "Accepted then"}),
    ]
    with closing(sqlite3.connect(store.path)) as db:
        for action, request in older:
            db.execute(
                "INSERT INTO events (timestamp,actor,action,outcome,task_id,request_json,"
                "after_json) VALUES ('2026-09-28T00:00:00Z','simon',?,'ok',?,?,?)",
                (action, task["id"], json.dumps(request), json.dumps({"status": "done"})),
            )
        db.commit()
    accepted, removed, revise, approve, _ = activity(store, task["id"])["items"]
    assert approve | {"decision": "approve", "reasons": "Signed off"} == approve
    assert approve["disposition"] == "done"
    assert revise["decision"] == "revise" and revise["reasons"] == "Rethink"
    assert removed["blocked_by_id"] == "a, b" and removed["note"] == "Done"
    assert accepted["summary"] == "Specification accepted: Accepted then"
