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
EVENTS_INSERT = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+events\b", re.IGNORECASE)
COLUMNS = re.compile(
    r"INTO\s+events\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", re.IGNORECASE | re.DOTALL
)


def emitted_actions(sources):
    """Every audit action the code can write, read from the code itself.

    Store._run(action, ...) is the audited entry point and Store._event(db, action, ...)
    the writer: the action must be a string literal (or a conditional between
    literals) so this scan sees it; only _run may forward its own action to _event. A
    raw INSERT INTO events must be the literal SQL of an execute or executemany call
    whose action column is an SQL literal or a literal parameter. Any other text that
    inserts into events (SQL held in a variable, built at run time, or parameters that
    cannot be read) is reported as opaque, so the classification test fails closed.
    """
    found, opaque = set(), []

    def literals(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            body, orelse = literals(node.body), literals(node.orelse)
            return None if body is None or orelse is None else body | orelse
        return None

    def argument(call, index, keyword):
        for item in call.keywords:
            if item.arg == keyword:
                return item.value
        return call.args[index] if len(call.args) > index else None

    def insert_actions(sql, call, function):
        """Actions a literal INSERT INTO events writes, or None when unreadable."""
        match = COLUMNS.search(sql)
        if match is None:
            return None
        columns = [c.strip().lower() for c in match.group(1).split(",")]
        values = [v.strip() for v in match.group(2).split(",")]
        if "action" not in columns or len(columns) != len(values):
            return None
        value = values[columns.index("action")]
        sql_literal = re.fullmatch(r"'([^']*)'", value)
        if sql_literal:
            return {sql_literal.group(1)}
        if value != "?":
            return None
        position = values[: columns.index("action")].count("?")
        rows = argument(call, 1, "parameters")
        if call.func.attr == "executemany":
            if not isinstance(rows, (ast.List, ast.Tuple)) or not rows.elts:
                return None
            rows = rows.elts
        else:
            rows = [rows]
        actions = set()
        for row in rows:
            if not isinstance(row, (ast.List, ast.Tuple)) or len(row.elts) <= position:
                return None
            action = row.elts[position]
            if function == "_event" and isinstance(action, ast.Name) and action.id == "action":
                continue  # The writer itself: its callers' actions are scanned.
            values = literals(action)
            if values is None:
                return None
            actions |= values
        return actions

    for path, text in sources:
        tree = ast.parse(text)
        function_of, attributed = {}, set()
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(function):
                    function_of[node] = function.name  # Innermost wins (walked later).
            if isinstance(function, ast.Expr) and isinstance(function.value, ast.Constant):
                attributed.add(function.value)  # A docstring writes nothing.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", None))
            where = f"{path}:{node.lineno}"
            if name in {"_run", "_event"}:
                action = argument(node, 0 if name == "_run" else 1, "action")
                values = literals(action) if action is not None else None
                forwarded = (
                    name == "_event"
                    and function_of.get(node) == "_run"
                    and isinstance(action, ast.Name)
                    and action.id == "action"
                )
                if values is not None:
                    found |= values
                elif not forwarded:
                    opaque.append(where)
            sql = argument(node, 0, "sql")
            if (
                name in {"execute", "executemany"}
                and isinstance(node.func, ast.Attribute)
                and isinstance(sql, ast.Constant)
                and isinstance(sql.value, str)
                and EVENTS_INSERT.search(sql.value)
            ):
                actions = insert_actions(sql.value, node, function_of.get(node))
                if actions is None:
                    opaque.append(where)
                else:
                    found |= actions
                attributed.add(sql)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node not in attributed
                and EVENTS_INSERT.search(node.value)
            ):
                opaque.append(f"{path}:{node.lineno}")
    return found, sorted(set(opaque))


def source_files():
    return [
        (str(path.relative_to(SOURCE.parent)), path.read_text())
        for path in sorted(SOURCE.rglob("*.py"))
    ]


def test_every_action_the_store_can_emit_is_classified():
    actions, opaque = emitted_actions(source_files())
    assert not opaque, f"event actions must be literals so they can be classified: {opaque}"
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
def _run(self, action, request, operation):
    self._event(db, action, request, scope, "ok")
def joined(self, db, scope):
    self._event(db, "group.member_joined", {}, scope, "ok")
def forwarded(self, db, action, scope):
    self._event(db, action, {}, scope, "ok")
def many(db):
    db.executemany(
        "INSERT INTO events (timestamp,action,outcome) VALUES (?,?,'ok')",
        [(timestamp(), "foo.many"), (timestamp(), "foo.more")],
    )
def inline(db):
    db.execute("INSERT INTO events (action,outcome) VALUES ('foo.inline','ok')")
def held(db):
    sql = "INSERT INTO events (timestamp,action) VALUES (?,?)"
    db.execute(sql, (timestamp(), "foo.raw"))
def generated(db, rows):
    db.executemany("INSERT INTO events (timestamp,action) VALUES (?,?)", rows)
def _event(self, db, action, request, scope, outcome):
    db.execute("INSERT INTO events (timestamp, action) VALUES (?, ?)", (timestamp(), action))
"""
    actions, opaque = emitted_actions([("sample.py", sample)])
    assert actions == {
        "note.created",
        "note.archived",
        "note.updated",
        "note.migrated",
        "group.member_joined",
        "foo.many",
        "foo.more",
        "foo.inline",
    }
    # hidden: an opaque _run; forwarded: _event outside _run; held: SQL in a variable;
    # generated: executemany rows that cannot be read.
    assert opaque == ["sample.py:12", "sample.py:18", "sample.py:27", "sample.py:30"]


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
    assert (
        page["items"][0]
        | {
            "member_id": member["id"],
            "title": "Loose member",
            "via": "add_group_member",
        }
        == page["items"][0]
    )

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


def join(store, project, group_id, title):
    """create_task(group_id=...): the join is recorded on the new member, not the group."""
    group = full(store, group_id)
    return store.create_task(
        project, title, group_id=group_id, group_expected_revision=group["revision"]
    )


def test_a_group_shows_members_that_joined_it_from_their_own_task(ready):
    store, project, ws = ready
    group = store.create_group(ws, "Joined")
    born = join(store, project, group["id"], "Born inside")
    loose = store.create_task(project, "Moved in", workstream_id=ws)
    current = full(store, group["id"])
    store.update_task(
        loose["id"],
        loose["revision"],
        {},
        group_id=group["id"],
        group_expected_revision=current["revision"],
    )
    # Already a member: update_task with the same group changes no membership.
    current, member = full(store, group["id"]), full(store, loose["id"])
    store.update_task(
        loose["id"],
        member["revision"],
        {"summary": "Short"},
        group_id=group["id"],
        group_expected_revision=current["revision"],
    )
    # A member's other history, failed joins and another group's members stay off.
    store.add_unresolved(born["id"], full(store, born["id"])["revision"], "Member question")
    with pytest.raises(TaskError):
        store.create_task(project, "Stale", group_id=group["id"], group_expected_revision=1)
    other = store.create_group(ws, "Other")
    join(store, project, other["id"], "Elsewhere")

    page = activity(store, group["id"])
    rows = [(e["kind"], e.get("member_id"), e.get("via"), e.get("title")) for e in page["items"]]
    assert rows == [
        ("member_added", loose["id"], "update_task", "Moved in"),
        ("member_added", born["id"], "create_task", "Born inside"),
        ("created", None, None, "Joined"),
    ]
    assert page["newest_sequence"] == page["items"][0]["sequence"]
    # The members' own feeds still show the same events as their own.
    born_feed = activity(store, born["id"])["items"]
    assert born_feed[-1]["kind"] == "created" and born_feed[-1]["group_id"] == group["id"]
    assert born_feed[-1]["sequence"] == page["items"][1]["sequence"]
    moved = next(e for e in activity(store, loose["id"])["items"] if e.get("group_id"))
    assert moved["kind"] == "updated" and moved["sequence"] == page["items"][0]["sequence"]


def test_member_joins_page_stably_with_the_groups_own_events(ready, monkeypatch):
    store, project, ws = ready
    group = store.create_group(ws, "Large")
    members = []
    for index in range(30):
        member = join(store, project, group["id"], f"Member {index}")
        members.append(member["id"])
        store.get_tasks([member["id"]])  # Member reads share the window but never show.
        store.add_unresolved(member["id"], member["revision"], "Hidden from the group")
        if index % 3 == 0:
            current = full(store, group["id"])
            store.update_task(group["id"], current["revision"], {"title": f"Large {index}"})
    pages = walk(store, group["id"])
    assert [len(page["items"]) for page in pages] == [20, 20, 1]
    entries = [entry for page in pages for entry in page["items"]]
    sequences = [entry["sequence"] for entry in entries]
    assert sequences == sorted(set(sequences), reverse=True)
    joined = [e["member_id"] for e in entries if e["kind"] == "member_added"]
    assert joined == members[::-1]
    assert [e["kind"] for e in entries].count("updated") == 10

    # A small scan bound resumes without gaps.
    monkeypatch.setattr(store_module, "ACTIVITY_SCAN_LIMIT", 7)
    limited = walk(store, group["id"])
    assert any(page.get("scan_limited") for page in limited)
    assert [e["sequence"] for page in limited for e in page["items"]] == sequences
    monkeypatch.undo()

    # New members and member churn between page loads cause no duplicates or gaps.
    first = activity(store, group["id"])
    for index in range(5):
        member = join(store, project, group["id"], f"Late {index}")
        store.add_unresolved(member["id"], member["revision"], "Hidden")
    second = activity(store, group["id"], cursor=first["next_cursor"])
    third = activity(store, group["id"], cursor=second["next_cursor"])
    loaded = [e["sequence"] for page in (first, second, third) for e in page["items"]]
    assert loaded == sequences and "next_cursor" not in third
    assert second["newest_sequence"] > first["newest_sequence"]
    fresh = [e["sequence"] for page in walk(store, group["id"]) for e in page["items"]]
    assert fresh[5:] == sequences and len(fresh) == len(sequences) + 5


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
