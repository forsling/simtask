"""Reject accidental serialization damage; preserve intentional technical text."""

import json
import sqlite3
from pathlib import Path

import pytest

from task_mcp.store import Store, TaskError

SOURCE = json.loads(
    (Path(__file__).parent / "fixtures" / "double_escaped_specification.json").read_text()
)


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    setup = store.init_project(str(tmp_path / "checkout"), branch="dev", confirmed=True)
    return store, setup["project"]["id"], setup["workstream"]["id"]


def business_state(store):
    # Failed writes still append their normal error audit event. Every other
    # table must remain byte-for-byte equivalent, including private identities.
    with sqlite3.connect(store.path) as db:
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {
            name: db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            for (name,) in tables
            if name not in {"events", "sqlite_sequence"}
        }


def assert_rejected(store, before, field, outcome, action):
    with pytest.raises(TaskError) as error:
        action()
    message = str(error.value)
    assert message.startswith("likely_double_escaped_specification:")
    assert field in message
    assert "actual line breaks" in message
    assert "JSON serializer without pre-escaping" in message
    assert outcome in message
    assert business_state(store) == before
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT outcome FROM events ORDER BY rowid DESC LIMIT 1").fetchone() == (
            "error",
        )


@pytest.mark.parametrize("field", ["body", "acceptance_criteria"])
@pytest.mark.parametrize("kind", ["task", "group", "direct_group"])
def test_source_create_rejected_without_identity_or_membership(context, field, kind):
    store, project, ws = context
    before = business_state(store)
    args = {field: SOURCE[field]}

    def action():
        if kind == "direct_group":
            return store.create_group(ws, "Malformed group", **args)
        return store.create_task(project, "Malformed task", workstream_id=ws, kind=kind, **args)

    assert_rejected(store, before, field, "No task was created", action)


@pytest.mark.parametrize("field", ["body", "acceptance_criteria"])
@pytest.mark.parametrize("kind", ["task", "group"])
def test_source_update_preserves_spec_etag_revision_and_membership(context, field, kind):
    store, project, ws = context
    created = store.create_task(project, "Original", "Original body", kind=kind, workstream_id=ws)
    spec = store.get_tasks([created["id"]])["items"][0]
    target = store.create_group(ws, "Destination") if kind == "task" else None
    before = business_state(store)
    assert_rejected(
        store,
        before,
        field,
        "No changes were saved",
        lambda: store.update_task(
            spec["id"],
            spec["revision"],
            {"title": "Would change", field: SOURCE[field]},
            spec["specification_etag"],
            group_id=target["id"] if target else None,
            group_expected_revision=target["revision"] if target else None,
        ),
    )
    after = store.get_tasks([created["id"]])["items"][0]
    for key in ("body", "acceptance_criteria", "revision", "spec_revision", "specification_etag"):
        assert after[key] == spec[key]


@pytest.mark.parametrize("field", ["body", "acceptance_criteria"])
def test_decomposition_rolls_back_first_child_parent_and_workstream(context, field):
    store, project, ws = context
    parent = store.create_task(project, "Parent", "Original", workstream_id=ws)
    before = business_state(store)
    assert_rejected(
        store,
        before,
        f"members[1].{field}",
        "No changes were saved",
        lambda: store.decompose_task(
            parent["id"],
            parent["revision"],
            [{"title": "Valid first child"}, {"title": "Malformed second", field: SOURCE[field]}],
        ),
    )


ACCEPTED = [
    r"A single prose mention of \n is useful.",
    r"Inline examples `first\n\nsecond` and `\n- first\n- second` remain literal.",
    "Code example:\n```python\n" + r'example = "first\n\nsecond\n- third\n- fourth"' + "\n```",
    "Code example:\n~~~text\n" + r"First paragraph.\nSecond paragraph.\nThird paragraph." + "\n~~~",
    "Open fence:\n```text\n" + r"First paragraph.\nSecond paragraph.\nThird paragraph.",
    r"Use regex \n+ or [\n\r] and paths C:\new\notes or /tmp/new/notes.",
    r"Quotes \"quoted\" and other backslashes \\ remain unchanged.",
    "Goal\n\n- First outcome\n- Second outcome",
    r"Use \n\n to denote a paragraph.",
    r"Use the regular expression \n\n to match blank lines in a file.",
    r"Adjacent code spans ``a`\n\ntext`` and `\n- another\n- example`.",
]


@pytest.mark.parametrize("text", ACCEPTED)
def test_legitimate_technical_text_accepted_unchanged(context, text):
    store, project, ws = context
    created = store.create_task(project, "Technical", text, text, workstream_id=ws)
    spec = store.get_tasks([created["id"]])["items"][0]
    assert spec["body"] == spec["acceptance_criteria"] == text
    changed = store.update_task(
        spec["id"], spec["revision"], {"body": text + " More."}, spec["specification_etag"]
    )
    assert store.get_tasks([changed["id"]])["items"][0]["body"] == text + " More."


@pytest.mark.parametrize("field", ["body", "acceptance_criteria"])
@pytest.mark.parametrize("marker", ["```", "~~~", "````", "~~~~"])
@pytest.mark.parametrize("prefix", ["> ", "> > ", "    ", "- ", "> - ", "  1. "])
@pytest.mark.parametrize("closing", ["same", "longer", "open"])
def test_container_fenced_examples_accepted_unchanged(context, field, marker, prefix, closing):
    store, project, ws = context
    text = (
        prefix
        + marker
        + "text\n"
        + prefix
        + (r"First paragraph.\nSecond paragraph.\nThird paragraph.")
    )
    if closing != "open":
        text += "\n" + prefix + marker + (marker[0] if closing == "longer" else "")
    created = store.create_task(project, "Container example", workstream_id=ws, **{field: text})
    spec = store.get_tasks([created["id"]])["items"][0]
    assert spec[field] == text
    updated = text + "\nOrdinary following prose."
    store.update_task(spec["id"], spec["revision"], {field: updated}, spec["specification_etag"])
    assert store.get_tasks([created["id"]])["items"][0][field] == updated


@pytest.mark.parametrize("field", ["body", "acceptance_criteria"])
@pytest.mark.parametrize(
    "text",
    [
        r"Use the regular expression \n- first\n- second to match the two "
        "newline-prefixed list entries.",
        r"Use regex \n- first\n- second to match entries.",
        r"The regexp First paragraph.\nSecond paragraph.\nThird paragraph. matches this text.",
    ],
)
def test_explicit_prose_regex_examples_accepted_unchanged(context, field, text):
    store, project, ws = context
    created = store.create_task(project, "Regex example", workstream_id=ws, **{field: text})
    spec = store.get_tasks([created["id"]])["items"][0]
    assert spec[field] == text
    updated = text + " More explanation."
    store.update_task(spec["id"], spec["revision"], {field: updated}, spec["specification_etag"])
    assert store.get_tasks([created["id"]])["items"][0][field] == updated


@pytest.mark.parametrize("field", ["body", "acceptance_criteria"])
@pytest.mark.parametrize("closer", ["~~~", "```", "```` extra", "````` extra"])
def test_nonmatching_fence_does_not_expose_code_to_guard(context, field, closer):
    store, project, ws = context
    text = "> ````text\n> " + closer + "\n> " + SOURCE[field] + "\n> `````"
    created = store.create_task(project, "Fence content", workstream_id=ws, **{field: text})
    assert store.get_tasks([created["id"]])["items"][0][field] == text


@pytest.mark.parametrize("field", ["body", "acceptance_criteria"])
@pytest.mark.parametrize("marker", ["```", "~~~"])
def test_prose_after_container_fence_is_still_validated(context, field, marker):
    store, project, ws = context
    text = "> " + marker + "text\n> " + r"Intentional\n- first\n- second"
    text += "\n> " + marker + marker[0] + "\n" + SOURCE[field]
    assert_rejected(
        store,
        business_state(store),
        field,
        "No task was created",
        lambda: store.create_task(
            project, "Escaped after example", workstream_id=ws, **{field: text}
        ),
    )


def test_corrected_source_all_write_paths_accept_unchanged(context):
    store, project, ws = context
    corrected = {
        field: SOURCE[field].replace(r"\n", "\n") for field in ("body", "acceptance_criteria")
    }
    created = store.create_task(project, "Corrected", workstream_id=ws, **corrected)
    spec = store.get_tasks([created["id"]])["items"][0]
    group = store.create_group(ws, "Corrected group", **corrected)
    store.update_task(spec["id"], spec["revision"], corrected, spec["specification_etag"])
    parent = store.create_task(project, "Decompose", workstream_id=ws)
    store.decompose_task(
        parent["id"], parent["revision"], [{"title": "Corrected child", **corrected}]
    )
    with sqlite3.connect(store.path) as db:
        rows = db.execute(
            "SELECT body,acceptance_criteria FROM tasks WHERE id IN (?,?) OR parent_group_id=?",
            (created["id"], group["id"], parent["id"]),
        ).fetchall()
    assert len(rows) == 3
    assert all(row == (corrected["body"], corrected["acceptance_criteria"]) for row in rows)


@pytest.mark.parametrize(
    "text",
    [
        r"First paragraph.\nSecond paragraph.\nThird paragraph.",
        r"Requirements\n- First item\n- Second item",
        "Real heading\n" + r"First paragraph.\nSecond paragraph.\nThird paragraph.",
    ],
)
def test_repeated_prose_separators_rejected(context, text):
    store, project, ws = context
    assert_rejected(
        store,
        business_state(store),
        "body",
        "No task was created",
        lambda: store.create_task(project, "Escaped prose", text, workstream_id=ws),
    )


def test_existing_text_untouched_and_revision_etag_checks_still_precede_guard(context):
    store, project, ws = context
    created = store.create_task(project, "Legacy", workstream_id=ws)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE tasks SET body=? WHERE id=?", (SOURCE["body"], created["id"]))
    spec = store.get_tasks([created["id"]])["items"][0]
    store.update_task(spec["id"], spec["revision"], {"title": "Renamed"})
    current = store.get_tasks([created["id"]])["items"][0]
    assert current["body"] == SOURCE["body"]
    with pytest.raises(TaskError, match="revision_conflict"):
        store.update_task(
            spec["id"], spec["revision"], {"body": SOURCE["body"]}, spec["specification_etag"]
        )
    with pytest.raises(TaskError, match="specification_read_required"):
        store.update_task(current["id"], current["revision"], {"body": SOURCE["body"]}, "stale")


def test_mcp_reports_guard_error_without_creating_task(context):
    import asyncio

    from mcp.server.mcpserver.exceptions import ToolError

    from task_mcp.server import create_server

    store, project, ws = context
    before = business_state(store)

    async def check():
        server = create_server(store, tracing=False)
        with pytest.raises(ToolError, match="likely_double_escaped_specification: body") as error:
            await server.call_tool(
                "create_task",
                {
                    "project": project,
                    "workstream_id": ws,
                    "title": "Malformed MCP task",
                    "body": SOURCE["body"],
                },
            )
        assert "No task was created" in str(error.value)

    asyncio.run(check())
    assert business_state(store) == before
