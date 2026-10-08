"""Text exports preserve workflow meaning without becoming another task ledger."""

import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest

from simtask.store import Store, TaskError


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    initialized = store.init_project(str(tmp_path / "repo"), branch="main", confirmed=True)
    return store, initialized["project"]["id"], initialized["workstream"]["id"]


def create(context, title, queued=True, **kwargs):
    store, project, ws = context
    created = store.create_task(
        project,
        title,
        source="user",
        user_request="Synthetic export test",
        workstream_id=ws if queued else None,
        **kwargs,
    )
    return store.get_tasks([created["id"]])["items"][0]


def current(store, task):
    return store.get_tasks([task["id"]])["items"][0]


def result(context, task):
    store, _, ws = context
    return store.record_result(
        task["id"],
        ws,
        current(store, task)["revision"],
        "worker",
        "Implemented",
        "Tests pass",
        artifacts=[{"kind": "artifact", "reference": "tests/test_export.py"}],
        verification="Tests pass",
        specification_etag=store.get_tasks([task["id"]])["items"][0]["specification_etag"],
    )


def finish(context, task):
    store, _, _ = context
    attempt = result(context, task)
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Independently checked")
    store.signoff_task(
        task["id"],
        current(store, task)["revision"],
        "approve",
        "Synthetic approval",
        attempt["id"],
        expected_attempt_revision=2,
    )
    return attempt


def task_section(text, title):
    return text.split(f"### {title}\n", 1)[1].split("\n### ", 1)[0]


def test_export_workflow_views_match_scoped_queue(context):
    store, project, ws = context
    create(context, "Ready")
    pending = create(context, "Pending")
    store.update_task(
        pending["id"],
        1,
        {"body": "Changed specification"},
        specification_etag=pending["specification_etag"],
    )
    question = create(context, "Question")
    store.add_unresolved(question["id"], 1, "Which protocol?")
    blocked = create(context, "Blocked")
    store.add_prerequisite(blocked["id"], 1, question["id"])
    review = create(context, "Review")
    result(context, review)
    signoff = create(context, "Signoff")
    attempt = result(context, signoff)
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Checked")
    done = create(context, "Done")
    selected = finish(context, done)
    for disposition in ("dropped", "deferred"):
        task = create(context, disposition.title())
        store.set_disposition(task["id"], 1, disposition, "Test disposition")
    text = store.export_workstream(ws)["content"]
    queue = store.list_tasks(project, ws, include_inactive=True, include=["ids"])["items"]
    assert {item["view"] for item in queue} == {
        "ready",
        "unresolved_items",
        "prerequisites",
        "review",
        "signoff",
        "done",
        "dropped",
        "deferred",
    }
    for item in queue:
        section = task_section(text, item["title"])
        workflow = next(line for line in section.splitlines() if line.startswith("- Workflow:"))
        assert workflow.endswith(f"({item['view']})")
    assert "- Workflow: Awaiting sign-off (signoff)" in task_section(text, "Signoff")
    assert "- Stored disposition: open" in task_section(text, "Signoff")
    assert selected["id"] in task_section(text, "Done")
    assert "selected signed-off result" in task_section(text, "Done")
    filtered = store.export_workstream(ws, include_closed=False)["content"]
    assert "Exported tasks: 7 of 9" in filtered
    assert f"- ID: `{done['id']}`" not in filtered
    assert "### Dropped" not in filtered
    assert "### Deferred" in filtered
    assert "```json" not in text


def test_export_readable_questions_prerequisites_proposals_and_prose(context):
    store, _, ws = context
    satisfied = create(context, "Completed dependency", queued=False)
    finish(context, satisfied)
    blocked = create(
        context, "External scope dependency", queued=False, body="Dependency body is not exported"
    )
    task = create(context, "Feature", body="Goal\n\n- first step", acceptance_criteria="Observable")
    task = store.add_prerequisite(task["id"], 1, satisfied["id"])
    task = store.add_prerequisite(task["id"], task["revision"], blocked["id"])
    task = store.add_unresolved(task["id"], task["revision"], "Choose a version\nExplain why")
    # Observer proposals are no longer created; a legacy row still exports read-only.
    with sqlite3.connect(store.path) as db:
        db.execute(
            "INSERT INTO gate_proposals (id,task_id,gate_type,detail,proposer,created_at) "
            "VALUES ('gat_legacy',?,'unresolved','Observer concern','older-server',?)",
            (task["id"], "2026-01-01T00:00:00Z"),
        )
    revision = store.list_workstreams()["items"][0]["revision"]
    store.set_scope(ws, revision, f"{ws} +{task['id']}")
    text = store.export_workstream(ws)["content"]
    assert "> Goal\n>\n> - first step" in text
    assert "> Observable" in text
    assert "> Choose a version\n> Explain why" in text
    assert "- Satisfied: Completed dependency" in text
    assert "- Not satisfied: External scope dependency" in text
    assert "Dependency body is not exported" not in text
    assert "Proposed gates (nonblocking until accepted)" in text
    assert "> Observer concern" in text


def test_attempt_history_does_not_misrepresent_current_workstream_or_spec(context, tmp_path):
    store, project, ws = context
    other = store.init_workstream(project, str(tmp_path / "repo"), branch="other", confirmed=True)
    other_ws = other["workstream"]["id"]
    task = create(context, "History")
    elsewhere = result((store, project, other_ws), task)
    store.human_review(elsewhere["id"], 1, "Actual human review in this synthetic test")
    text = store.export_workstream(ws)["content"]
    assert "- Workflow: Ready (ready)" in text
    assert "- Context: other workstream" in text
    assert "Human review note" in text
    old = result(context, task)
    store.record_review(old["id"], 1, "independent", "pass", "First version checked")
    full = current(store, task)
    task = store.update_task(
        task["id"],
        full["revision"],
        {"body": "New version"},
        specification_etag=full["specification_etag"],
    )
    text = store.export_workstream(ws)["content"]
    assert "- Workflow: Ready (ready)" in text
    assert "superseded specification, other workstream" in text
    assert "- Context: superseded specification\n" in text
    assert "Reviewer: independent" in text
    assert "> First version checked" in text
    assert "> Tests pass" in text
    rework = result(context, task)
    store.record_review(rework["id"], 1, "reviewer", "rework", "Fix the edge case")
    text = store.export_workstream(ws)["content"]
    assert "- Workflow: Ready (ready)" in text
    assert "- State: rework" in text and "> Fix the edge case" in text


@pytest.mark.parametrize("format", ["markdown", "legacy"])
def test_export_retains_structured_proof_and_historical_text(context, tmp_path, format):
    store, _, ws = context
    task = create(context, "Recoverable proof")
    historical = result(context, task)
    legacy_notes = "Historical plain evidence\nRecorded before structured proof."
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE attempts SET evidence=? WHERE id=?", (legacy_notes, historical["id"]))

    artifact = tmp_path / "durable-proof.txt"
    artifact.write_text("Verified fixture output\n")
    assert artifact.read_text() == "Verified fixture output\n"
    references = [
        {"kind": "commit", "reference": "1234567890abcdef1234567890abcdef12345678"},
        {"kind": "artifact", "reference": str(artifact)},
    ]
    verification = "Read durable-proof.txt: expected fixture output matched.\nLimit: fixture only."
    evidence = "Context notes without the artifact references or verification."
    checked = current(store, task)
    recorded = store.record_result(
        task["id"],
        ws,
        checked["revision"],
        "proof worker",
        "Durable fixture result",
        evidence,
        artifacts=references,
        verification=verification,
        specification_etag=checked["specification_etag"],
    )
    assert verification not in evidence
    assert all(item["reference"] not in evidence for item in references)
    expected_attempts = current(store, task)["attempts"]
    before = business_state(store)
    exported = (
        store.export_workstream(ws)
        if format == "markdown"
        else store.export_workstream(ws, format="legacy")
    )
    text = exported["content"]
    if format == "markdown":
        section = text.split(f"##### Attempt `{recorded['id']}`\n", 1)[1].split(
            "\n##### Attempt ", 1
        )[0]
        assert f"> {evidence}" in section
        for item in references:
            assert f"> {item['kind']}: {item['reference']}" in section
        assert "> " + verification.replace("\n", "\n> ") in section
        assert checked["specification_etag"] in section
        assert "> " + legacy_notes.replace("\n", "\n> ") in text
    else:
        structured = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
        assert structured["attempts"] == expected_attempts
        proof = next(item for item in structured["attempts"] if item["id"] == recorded["id"])
        assert proof["evidence"] == evidence
        assert proof["artifacts"] == references
        assert proof["verification"] == verification
        assert proof["specification_etag"] == checked["specification_etag"]
        legacy = next(item for item in structured["attempts"] if item["id"] == historical["id"])
        assert legacy["evidence"] == legacy_notes and "artifacts" not in legacy
    assert exported == store.export_workstream(ws, format=format)
    assert exported["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert business_state(store) == before


def test_export_shared_groups_respect_local_scope_and_filter(context, tmp_path):
    store, project, ws = context
    remote = store.init_project(str(tmp_path / "remote"), branch="main", confirmed=True)
    group = store.create_group(ws, "Shared delivery", "Cross-repo context", "Integrated criteria")
    members = []
    for pid, title in (
        (project, "Local done"),
        (project, "Excluded"),
        (remote["project"]["id"], "Remote"),
    ):
        group = store.get_tasks([group["id"]])["items"][0]
        members.append(
            store.create_task(
                pid,
                title,
                body=f"{title} private body",
                source="user",
                user_request="Test",
                workstream_id=ws if pid == project else remote["workstream"]["id"],
                group_id=group["id"],
                group_expected_revision=group["revision"],
            )
        )
    finish(context, members[0])
    store.set_scope(
        ws,
        store.workstream_status(ws)["workstream"]["revision"],
        f"none +{group['id']} -{members[1]['id']}",
    )
    text = store.export_workstream(ws, include_closed=False)["content"]
    assert "Global progress: 1/3 done; incomplete" in text
    assert "Members in this project: 2" in text
    assert "Members in local scope: 1" in text
    assert "Members in this export: 0" in text
    assert "No tasks match this export." in text
    assert "> Cross-repo context" in text and "> Integrated criteria" in text
    assert all(member["id"] not in text for member in members)
    assert "private body" not in text
    # Including a concrete member alone must still carry its global group context.
    store.set_scope(
        ws, store.workstream_status(ws)["workstream"]["revision"], f"none +{members[0]['id']}"
    )
    text = store.export_workstream(ws)["content"]
    assert "Reference: task membership" in text
    assert "Global progress: 1/3 done; incomplete" in text
    assert members[1]["id"] not in text and members[2]["id"] not in text
    # A group used only as a prerequisite also has context, not foreign task bodies.
    dependent = create(context, "Dependent")
    store.add_prerequisite(dependent["id"], 1, group["id"])
    revision = store.list_workstreams(project)["items"][0]["revision"]
    store.set_scope(ws, revision, f"{ws} +{dependent['id']} -{group['id']} +{members[0]['id']}")
    text = store.export_workstream(ws)["content"]
    assert "Reference: prerequisite" in text
    assert "Not satisfied: Shared delivery" in text


def test_export_empty_groups_and_scope(context):
    store, _, ws = context
    assert "No tasks in local scope." in store.export_workstream(ws)["content"]
    store.create_group(ws, "Empty")
    assert "Global progress: 0/0 done; incomplete" in store.export_workstream(ws)["content"]


def business_state(store):
    with sqlite3.connect(store.path) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {
            table: db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in tables
            if table not in {"events", "sqlite_sequence"}
        }


def test_export_determinism_hash_order_escaping_and_no_state_mutation(context):
    store, project, ws = context
    first = create(
        context,
        "First | <b>literal</b>\n# Not a heading",
        body="## Body heading\n```\nif a < b && c > d:\n```\n<script>no</script>",
    )
    second = create(context, "Second")
    store.reorder_tasks(
        ws,
        [second["id"]],
        store.list_tasks(project, ws)["workstream_order_revision"],
    )
    before = business_state(store)
    one = store.export_workstream(ws)
    two = store.export_workstream(ws)
    assert one == two
    assert one["sha256"] == hashlib.sha256(one["content"].encode()).hexdigest()
    assert business_state(store) == before
    assert one["content"].index(second["id"]) < one["content"].index(first["id"])
    assert r"First \| &lt;b&gt;literal&lt;/b&gt; \# Not a heading" in one["content"]
    assert "> ## Body heading\n> ```\n> if a < b && c > d:\n> ```" in one["content"]
    assert "> <script>no</script>" in one["content"]
    assert "\n# Not a heading" not in one["content"]


def test_legacy_export_preserves_v1_layout(context):
    store, project, ws = context
    task = create(context, "Legacy", body="Body", acceptance_criteria="Criteria")
    result = store.export_workstream(ws, format="legacy")
    structured = {
        key: task[key]
        for key in (
            "unresolved_items",
            "blocked_by",
            "attempts",
            "selected_attempt_id",
            "parent_group_id",
            "object_type",
        )
    }
    expected = "\n".join(
        [
            "# simtask workstream export",
            "",
            "Format: task-mcp/v1",
            f"Project: {project} (repo)",
            f"Workstream: {ws} (main)",
            "",
            "## Legacy",
            "",
            f"ID: {task['id']}",
            "Revision: 1",
            f"Workstreams: {ws}",
            "State: open",
            "",
            "Body",
            "",
            "### Acceptance criteria",
            "",
            "Criteria",
            "",
            "### Structured data",
            "",
            "```json",
            json.dumps(structured, ensure_ascii=False, sort_keys=True),
            "```",
            "",
        ]
    )
    assert result == {
        "format": "task-mcp/v1",
        "workstream_order_revision": store.list_tasks(project, ws)["workstream_order_revision"],
        "content": expected,
        "sha256": hashlib.sha256(expected.encode()).hexdigest(),
    }
    assert result == store.export_workstream(ws, format="legacy")


@pytest.mark.parametrize("format", ["markdown", "legacy"])
def test_cli_matches_store_and_filter(context, format):
    store, _, ws = context
    task = create(context, "Closed")
    finish(context, task)
    create(context, "Still open")
    expected = store.export_workstream(ws, include_closed=False, format=format)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "simtask",
            "--db",
            str(store.path),
            "--export-workstream",
            ws,
            "--export-format",
            format,
            "--exclude-closed",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == expected["content"]


def test_invalid_export_options_do_not_create_database(context, tmp_path):
    store, _, ws = context
    with pytest.raises(TaskError, match="invalid_export_format"):
        store.export_workstream(ws, format="typo")
    for options in (
        ["--export-format", "typo"],
        ["--exclude-closed"],
        ["--export-format", "legacy"],
    ):
        database = tmp_path / "never-created.sqlite3"
        completed = subprocess.run(
            [sys.executable, "-m", "simtask", "--db", str(database), *options],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert completed.returncode == 2 and not database.exists()
