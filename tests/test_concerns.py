import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from task_mcp.runtime import PROTOCOL_SCHEMA_REVISION
from task_mcp.store import DATABASE_SCHEMA_REVISION, Store, TaskError

LEGACY_CONCERNS_LOOKALIKE = (
    '{"format":"attempt-concerns-v1","original_evidence":"Legacy inner text",'
    '"concerns":[{"kind":"value","text":"Legacy JSON data, not a recorded concern"}]}'
)
ATTRIBUTED_LEGACY_LOOKALIKE = (
    " \n"
    + json.dumps(
        {
            "format": "attempt-concerns-v1",
            "original_evidence": "Legacy inner β text",
            "concerns": [
                {
                    "kind": "value",
                    "text": "Historical JSON data, not an actual recorded concern",
                    "source": "reviewer",
                    "author": "historical-data-name",
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n\t"
)


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / "concerns.sqlite3")
    setup = store.init(
        str(tmp_path / "repo"), branch="main", action="create_project", confirmed=True
    )
    return store, setup["project"]["id"], setup["workstream"]["id"]


def result(store, task, ws, concerns=None):
    return store.compact_call(
        "record_result",
        task["id"],
        ws,
        task["revision"],
        "builder",
        "Built",
        "Context",
        [{"kind": "artifact", "reference": "synthetic.txt"}],
        "Synthetic verification",
        task["specification_etag"],
        concerns=concerns,
    )


def test_concerns_persist_and_pass_review_clears_prerequisite(context):
    store, project, ws = context
    task = store.create_task(project, "Deliver", workstream_id=ws)
    dependent = store.create_task(project, "Use delivery", workstream_id=ws)
    store.add_prerequisite(dependent["id"], dependent["revision"], task["id"])
    own = {"kind": "value", "text": "A different priority would change this task."}
    review = {"kind": "design", "text": "An alternative interface needs a scope change."}
    ack = result(store, task, ws, [own])
    with sqlite3.connect(store.path) as db:
        original_evidence = db.execute(
            "SELECT evidence FROM attempts WHERE id=?", (ack["id"],)
        ).fetchone()[0]
    assert "concerns" not in json.loads(original_evidence)
    assert ack["concern_count"] == 1 and "concerns" not in ack
    assert own["text"] not in json.dumps(ack)
    next_action = store.get_next_action(ws)
    assert next_action["action"] == "review"
    assert next_action["attempt"]["concerns"] == [
        {**own, "source": "implementer", "author": "builder"}
    ]
    assert own["text"] not in next_action["attempt"]["evidence"]
    assert json.dumps(next_action).count(own["text"]) == 1
    full = store.read_tasks([task["id"]], specification=True, workstream_id=ws)["items"][0]
    assert full["concerns"][0]["attempt_id"] == ack["id"]
    assert full["concerns"][0]["text"] == own["text"]
    chosen = store.read_tasks([task["id"]], True, ws, [ack["id"]])["items"][0]
    assert chosen["concerns"] == [] and json.dumps(chosen).count(own["text"]) == 1
    reviewed = store.compact_call(
        "record_review", ack["id"], 1, "checker", "pass", "Verified", concerns=[review]
    )
    assert reviewed["state"] == "passed" and reviewed["concern_count"] == 2
    assert not {"concerns", "evidence", "review_note"} & reviewed.keys()
    assert review["text"] not in json.dumps(reviewed)
    restarted = Store(store.path)
    proof = restarted.get_attempt(ack["id"])
    assert "concerns_json" not in json.dumps(proof)
    assert proof["concerns"] == [
        {**own, "source": "implementer", "author": "builder"},
        {**review, "source": "reviewer", "author": "checker"},
    ]
    assert proof["evidence"] == "Context"
    assert proof["artifacts"] == [{"kind": "artifact", "reference": "synthetic.txt"}]
    assert proof["verification"] == "Synthetic verification"
    assert proof["specification_etag"] == task["specification_etag"]
    with sqlite3.connect(store.path) as db:
        raw, metadata = db.execute(
            "SELECT evidence,concerns_json FROM attempts WHERE id=?", (ack["id"],)
        ).fetchone()
    assert raw == original_evidence and json.loads(metadata) == proof["concerns"]
    assert (
        restarted.get_tasks([task["id"]])["items"][0]["attempts"][0]["concerns"]
        == proof["concerns"]
    )
    cards = restarted.list_tasks(project, ws, include=["blockers"])["items"]
    assert cards[0]["state"] == "signoff" and cards[0]["concern_count"] == 2
    assert cards[1]["state"] == "ready" and cards[1]["prerequisites"][0]["satisfied"]
    assert restarted.get_next_action(ws)["task"]["id"] == dependent["id"]
    export = restarted.export_workstream(ws)["content"]
    for concern in proof["concerns"]:
        assert concern["text"] in export and concern["author"] in export
    # Synthetic verdict demonstrates concerns do not add a sign-off gate.
    done = restarted.signoff_task(
        task["id"], ack["task_revision"], "approve", "Synthetic verdict", ack["id"], 2
    )
    assert done["status"] == "done"
    assert restarted.get_attempt(ack["id"])["concerns"] == proof["concerns"]


@pytest.mark.parametrize("supplied", ["omitted", None, []])
def test_review_omission_preserves_original_envelope(context, supplied):
    store, project, ws = context
    task = store.create_task(project, "Deliver", workstream_id=ws)
    ack = result(store, task, ws, [{"kind": "design", "text": "Scope needs a different design."}])
    with sqlite3.connect(store.path) as db:
        # Noncanonical whitespace in explicit metadata is still preserved on omission.
        db.execute("UPDATE attempts SET concerns_json=' ' || concerns_json || char(10)")
        before = db.execute(
            "SELECT evidence,concerns_json FROM attempts WHERE id=?", (ack["id"],)
        ).fetchone()
    arguments = {} if supplied == "omitted" else {"concerns": supplied}
    store.record_review(ack["id"], 1, "checker", "pass", "Checked", **arguments)
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute(
                "SELECT evidence,concerns_json FROM attempts WHERE id=?", (ack["id"],)
            ).fetchone()
            == before
        )
    assert store.get_attempt(ack["id"])["concerns"][0]["author"] == "builder"


def assert_legacy_concern_projections(store, task, ws, attempt_id, legacy, concerns):
    proof = store.get_attempt(attempt_id)
    assert "concerns_json" not in json.dumps(proof)
    assert proof["evidence"] == legacy and proof["concerns"] == concerns
    assert store.get_tasks([task["id"]])["items"][0]["attempts"][0] == proof
    assert store.list_task_attempts(task["id"])["items"][0]["concern_count"] == len(concerns)
    card = store.list_tasks(task["project_id"], ws)["items"][0]
    assert card.get("concern_count", 0) == len(concerns)
    card = store.list_tasks(task["project_id"], ws, include=["concerns"])["items"][0]
    assert card["concern_count"] == len(concerns)
    assert card["concern_attempt_total"] == bool(concerns)
    assert [c["text"] for c in card["concerns"]] == [c["text"] for c in concerns]
    full = store.read_tasks([task["id"]], True, ws)["items"][0]
    assert full["concern_count"] == len(concerns)
    assert [c["text"] for c in full["concerns"]] == [c["text"] for c in concerns]
    selected = store.read_tasks([task["id"]], True, ws, [attempt_id])["items"][0]
    assert selected["concerns"] == [] and selected["attempts"][0] == proof
    status = store.workstream_status(ws)
    assert status["status"]["concern_count"] == len(concerns)
    assert status["concern_tasks"]["total"] == bool(concerns)
    if proof["state"] == "review":
        assert store.get_next_action(ws)["attempt"] == proof
    markdown = store.export_workstream(ws)["content"]
    assert "> " + legacy.replace("\n", "\n> ") in markdown
    assert markdown.count(" concern — ") == len(concerns)
    exported = store.export_workstream(ws, format="legacy")["content"]
    structured = json.loads(exported.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert structured["attempts"] == [proof]


@pytest.mark.parametrize(
    "legacy",
    [
        "plain legacy proof\nβ",
        '{"old": "proof"}',
        '{"format": "attempt-concerns-v1"}',
        '{"format": "durable-result-v1"}',
        '{"format":"durable-result-v1","concerns":[{"kind":"value","text":"Legacy context"}]}',
        LEGACY_CONCERNS_LOOKALIKE,
        ATTRIBUTED_LEGACY_LOOKALIKE,
        '{"format":"attempt-concerns-v1","original_evidence":"Legacy inner text","concerns":[]}',
    ],
)
@pytest.mark.parametrize(
    "supplied",
    ["omitted", None, [], [{"kind": "value", "text": "Task priority needs reconsidering."}]],
)
def test_legacy_proof_is_lossless_with_reviewer_concerns(context, legacy, supplied):
    store, project, ws = context
    task = store.create_task(project, "Legacy delivery", workstream_id=ws)
    ack = result(store, task, ws)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE attempts SET evidence=? WHERE id=?", (legacy, ack["id"]))
    assert_legacy_concern_projections(store, task, ws, ack["id"], legacy, [])
    added = isinstance(supplied, list) and bool(supplied)
    arguments = {} if supplied == "omitted" else {"concerns": supplied}
    reviewed = store.compact_call(
        "record_review", ack["id"], 1, "checker", "pass", "Checked", **arguments
    )
    assert reviewed["concern_count"] == added and "concerns" not in reviewed
    concerns = [{**supplied[0], "source": "reviewer", "author": "checker"}] if added else []
    assert_legacy_concern_projections(Store(store.path), task, ws, ack["id"], legacy, concerns)
    with sqlite3.connect(store.path) as db:
        raw = db.execute("SELECT evidence FROM attempts WHERE id=?", (ack["id"],)).fetchone()[0]
        assert raw == legacy


@pytest.mark.parametrize("format", ["durable-result-v1", "attempt-concerns-v1"])
@pytest.mark.parametrize(
    "invalid",
    [
        {"kind": "quality"},
        {"kind": ["value"]},
        {"text": " "},
        {"text": 7},
        {"source": None},
        {"source": ["reviewer"]},
        {"source": "observer"},
        {"author": None},
        {"author": {"name": "legacy"}},
        {"author": " "},
        {"extra": "unsupported metadata"},
        {},  # Even fully attributed historical JSON has no concern provenance.
    ],
)
def test_malformed_stored_concerns_remain_lossless_legacy_evidence(context, format, invalid):
    store, project, ws = context
    task = store.create_task(project, "Legacy metadata", workstream_id=ws)
    ack = result(store, task, ws)
    valid = {"kind": "value", "text": "Legacy JSON β", "source": "implementer", "author": "old"}
    fields = (
        {"original_evidence": "Inner legacy context"}
        if format == "attempt-concerns-v1"
        else {
            "evidence": "Inner context",
            "artifacts": [],
            "verification": "old",
            "specification_etag": "old",
        }
    )
    legacy = json.dumps(
        {"format": format, **fields, "concerns": [valid, {**valid, **invalid}]},
        ensure_ascii=False,
        indent=2,
    )
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE attempts SET evidence=? WHERE id=?", (legacy, ack["id"]))
    # The established durable-result decoder keeps its original interpretation,
    # regardless of extra historical keys. None can become concern metadata.
    expected = fields.get("evidence", legacy)
    assert_legacy_concern_projections(store, task, ws, ack["id"], expected, [])
    addition = {"kind": "design", "text": "Actual reviewer addition"}
    reviewed = store.compact_call(
        "record_review", ack["id"], 1, "checker", "pass", "Checked", concerns=[addition]
    )
    assert reviewed["concern_count"] == 1
    assert_legacy_concern_projections(
        Store(store.path),
        task,
        ws,
        ack["id"],
        expected,
        [{**addition, "source": "reviewer", "author": "checker"}],
    )
    with sqlite3.connect(store.path) as db:
        raw = db.execute("SELECT evidence FROM attempts WHERE id=?", (ack["id"],)).fetchone()[0]
    assert raw == legacy
    if format == "durable-result-v1":
        proof = store.get_attempt(ack["id"])
        assert all(proof[key] == fields[key] for key in fields)


@pytest.mark.parametrize(
    "invalid",
    [
        [{"kind": "quality", "text": "Wrong kind"}],
        [{"kind": "VALUE", "text": "Wrong case"}],
        [{"kind": "value", "text": " "}],
        [{"kind": "design", "text": 7}],
        [{"kind": None, "text": "Wrong type"}],
        [{"kind": ["value"], "text": "Wrong type"}],
        [{"kind": "design", "text": "OK", "source": "reviewer"}],
        [{"text": "Missing kind"}],
        [{"kind": "value", "text": "Valid first"}, {"kind": "wrong", "text": "Invalid last"}],
        {},
        "value",
        [None],
    ],
)
def test_invalid_concerns_roll_back_results_and_reviews(context, invalid):
    store, project, ws = context
    task = store.create_task(project, "Deliver", workstream_id=ws)
    with pytest.raises(TaskError, match="invalid_concerns"):
        result(store, task, ws, invalid)
    assert store.get_tasks([task["id"]])["items"][0]["revision"] == task["revision"]
    assert store.list_task_attempts(task["id"])["total"] == 0
    ack = result(store, task, ws, [{"kind": "value", "text": "Existing concern"}])
    before = store.get_attempt(ack["id"])
    with pytest.raises(TaskError, match="invalid_concerns"):
        store.record_review(ack["id"], 1, "checker", "pass", "Checked", concerns=invalid)
    assert store.get_attempt(ack["id"]) == before
    assert store.get_tasks([task["id"]])["items"][0]["revision"] == ack["task_revision"]


def test_concerns_leave_existing_gates_unchanged(context):
    store, project, ws = context
    task = store.create_task(project, "Gated result", workstream_id=ws)
    gate = store.add_unresolved(task["id"], task["revision"], "Actual unresolved requirement")
    task = store.get_tasks([task["id"]])["items"][0]
    ack = result(
        store, task, ws, [{"kind": "design", "text": "Another design requires different scope."}]
    )
    assert store.get_next_action(ws)["action"] is None
    assert "unresolved_items" in ack["gate_diagnostics"]
    review = store.compact_call("record_review", ack["id"], 1, "checker", "pass", "Checked")
    assert review["state"] == "passed" and "unresolved_items" in review["gate_diagnostics"]
    full = store.get_tasks([task["id"]])["items"][0]
    assert full["unresolved_items"] == gate["unresolved_items"]
    with pytest.raises(TaskError, match="task_not_ready_for_signoff"):
        store.signoff_task(
            task["id"], ack["task_revision"], "approve", "Synthetic verdict", ack["id"], 2
        )


def test_review_concern_addition_rolls_back_with_failed_audit(context, monkeypatch):
    store, project, ws = context
    task = store.create_task(project, "Atomic review", workstream_id=ws)
    ack = result(store, task, ws, [{"kind": "value", "text": "Implementer contribution"}])
    with sqlite3.connect(store.path) as db:
        before = db.execute("SELECT * FROM attempts WHERE id=?", (ack["id"],)).fetchone()

    def failed_audit(*args, **kwargs):
        raise RuntimeError("Injected review audit failure")

    monkeypatch.setattr(store, "_event", failed_audit)
    with pytest.raises(RuntimeError, match="Injected review"):
        store.record_review(
            ack["id"],
            1,
            "checker",
            "pass",
            "Checked",
            concerns=[{"kind": "design", "text": "Reviewer contribution"}],
        )
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT * FROM attempts WHERE id=?", (ack["id"],)).fetchone() == before


def test_status_and_full_read_windows_are_paged_current_and_local(context, tmp_path):
    store, project, ws = context
    other = store.init_workstream(project, str(tmp_path / "repo"), branch="other", confirmed=True)[
        "workstream"
    ]["id"]
    plain = store.create_task(project, "First unremarkable task", workstream_id=ws)
    task = store.create_task(project, "Delivery with many attempts", workstream_id=ws)
    attempts = []
    for i, stream in enumerate([ws] * 5 + [other]):
        task = store.get_tasks([task["id"]])["items"][0]
        attempts.append(result(store, task, stream, [{"kind": "design", "text": f"Concern {i}"}]))
    status = store.workstream_status(ws, limit=1)
    assert status["items"][0]["id"] == plain["id"]
    assert status["concern_tasks"]["items"][0]["id"] == task["id"]
    assert status["concern_tasks"]["items"][0]["concern_count"] == 5
    assert status["concern_tasks"]["items"][0]["view"] == "review"
    assert status["status"]["concern_task_count"] == 1
    assert "Concern " not in json.dumps(status)
    full = store.read_tasks([task["id"]], True, ws)["items"][0]
    assert full["concern_count"] == full["concern_attempt_total"] == 5
    assert len(full["concerns"]) == 3 and full["concerns_has_more"]
    assert {c["workstream_id"] for c in full["concerns"]} == {ws}
    assert store.workstream_status(other)["concern_tasks"]["total"] == 0  # Task is queued on ws.
    second = store.create_task(project, "Ready to sign off", workstream_id=ws)
    second_ack = result(
        store, second, ws, [{"kind": "value", "text": "Change of priority needed."}]
    )
    store.record_review(second_ack["id"], 1, "checker", "pass", "Checked")
    first_page = store.workstream_status(ws, limit=1)["concern_tasks"]
    assert first_page["total"] == 2 and first_page["items"][0]["id"] == second["id"]
    assert first_page["items"][0]["view"] == "signoff" and first_page["next_offset"] == 1
    rest = store.workstream_status(ws, limit=1, offset=first_page["next_offset"])["concern_tasks"]
    assert rest["items"][0]["id"] == task["id"] and rest["next_offset"] is None
    updated = store.update_task(task["id"], task["revision"] + 1, {"title": "Changed requirements"})
    assert updated["spec_revision"] == 2
    assert store.read_tasks([task["id"]], True, ws)["items"][0]["concerns"] == []
    assert store.workstream_status(ws)["concern_tasks"]["total"] == 1
    history = store.list_task_attempts(task["id"], current_spec_only=False)
    assert history["total"] == 6 and history["items"][0]["concern_count"] == 1
    assert store.get_attempt(attempts[0]["id"])["concerns"][0]["text"] == "Concern 0"


def test_fresh_stdio_discovers_optional_inputs_and_records_complete_concerns(tmp_path):
    database = tmp_path / "stdio.sqlite3"
    root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "task_mcp", "--db", str(database)],
        env={"PYTHONPATH": str(root / "src"), "TASK_MCP_DB": str(database)},
    )

    async def exercise():
        async with Client(parameters, read_timeout_seconds=30) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 28
            for name in ("record_result", "record_review"):
                descriptor = next(t for t in tools if t.name == name)
                schema = descriptor.input_schema
                assert "concerns" in schema["properties"] and "concerns" not in schema["required"]
                assert schema["$defs"]["ConcernInput"]["properties"]["kind"]["enum"] == [
                    "value",
                    "design",
                ]
                assert (
                    "cannot be" in descriptor.description
                    and "changing what the task says" in descriptor.description
                )
                assert "rare" not in descriptor.description

            async def call(name, **arguments):
                response = await client.call_tool(name, arguments)
                assert not response.is_error, response.content
                return response.structured_content

            runtime = (await call("init", path=str(tmp_path / "probe"), branch="main"))["runtime"]
            assert runtime["package_path"] == str(root / "src/task_mcp")
            assert runtime["protocol_schema_revision"] == PROTOCOL_SCHEMA_REVISION == 15
            assert runtime["database_schema_revision"] == DATABASE_SCHEMA_REVISION == 10
            ctx = await call(
                "init",
                path=str(tmp_path / "repo"),
                branch="main",
                action="create_project",
                confirmed=True,
            )
            ws = ctx["workstream"]["id"]
            task = await call(
                "create_task", project=ctx["project"]["id"], title="Deliver", workstream_id=ws
            )
            full = (await call("get_tasks", ids=[task["id"]], specification=True))["items"][0]
            proof = dict(
                task_id=task["id"],
                workstream_id=ws,
                expected_revision=task["revision"],
                implementer="builder",
                summary="Built",
                evidence="Context",
                artifacts=[{"kind": "artifact", "reference": "synthetic.txt"}],
                verification="Synthetic verification",
                specification_etag=full["specification_etag"],
            )
            invalid = await client.call_tool(
                "record_result", {**proof, "concerns": [{"kind": "wrong", "text": "Invalid"}]}
            )
            assert invalid.is_error
            ack = await call(
                "record_result",
                **proof,
                concerns=[
                    {"kind": "value", "text": "Different priority requires a different task."}
                ],
            )
            assert ack["concern_count"] == 1 and "concerns" not in ack
            selected = await call("get_next_action", workstream_id=ws)
            assert selected["attempt"]["concerns"][0]["source"] == "implementer"
            reviewed = await call(
                "record_review",
                attempt_id=ack["id"],
                expected_revision=1,
                reviewer="checker",
                verdict="pass",
                note="Checked",
                concerns=[
                    {"kind": "design", "text": "A different interface requires changing scope."}
                ],
            )
            assert reviewed["state"] == "passed" and reviewed["concern_count"] == 2
            assert "concerns" not in reviewed and len(json.dumps(reviewed)) < 1200
            status = await call("workstream_status", workstream_id=ws)
            assert status["concern_tasks"]["items"][0]["view"] == "signoff"
            legacy_task = await call(
                "create_task", project=ctx["project"]["id"], title="Legacy proof", workstream_id=ws
            )
            legacy_full = (await call("get_tasks", ids=[legacy_task["id"]], specification=True))[
                "items"
            ][0]
            legacy_ack = await call(
                "record_result",
                **{
                    **proof,
                    "task_id": legacy_task["id"],
                    "expected_revision": legacy_task["revision"],
                    "specification_etag": legacy_full["specification_etag"],
                },
            )
            # Historical evidence was arbitrary text/JSON before proof envelopes.
            with sqlite3.connect(database) as db:
                db.execute(
                    "UPDATE attempts SET evidence=? WHERE id=?",
                    (ATTRIBUTED_LEGACY_LOOKALIKE, legacy_ack["id"]),
                )
            legacy_read = await call("get_attempt", attempt_id=legacy_ack["id"])
            assert legacy_read["evidence"] == ATTRIBUTED_LEGACY_LOOKALIKE
            assert legacy_read["concerns"] == []
            selected = await call("get_next_action", workstream_id=ws)
            assert selected["attempt"] == legacy_read and selected["task"]["concern_count"] == 0
            status = await call("workstream_status", workstream_id=ws)
            assert status["concern_tasks"]["total"] == 1
            # Export is a CLI/Store surface, no longer an MCP tool.
            exported = Store(database).export_workstream(ws)
            assert "> " + ATTRIBUTED_LEGACY_LOOKALIKE.replace("\n", "\n> ") in exported["content"]
            assert "Value concern — reviewer historical" not in exported["content"]
            legacy_reviewed = await call(
                "record_review",
                attempt_id=legacy_ack["id"],
                expected_revision=1,
                reviewer="legacy-checker",
                verdict="pass",
                note="Checked historical proof",
                concerns=[{"kind": "design", "text": "Actual reviewer addition"}],
            )
            assert legacy_reviewed["concern_count"] == 1 and "concerns" not in legacy_reviewed
        async with Client(parameters, read_timeout_seconds=30) as restarted:
            response = await restarted.call_tool("get_attempt", {"attempt_id": ack["id"]})
            assert not response.is_error
            assert len(response.structured_content["concerns"]) == 2
            assert response.structured_content["evidence"] == "Context"
            response = await restarted.call_tool("get_attempt", {"attempt_id": legacy_ack["id"]})
            assert not response.is_error
            assert response.structured_content["evidence"] == ATTRIBUTED_LEGACY_LOOKALIKE
            assert response.structured_content["concerns"] == [
                {
                    "kind": "design",
                    "text": "Actual reviewer addition",
                    "source": "reviewer",
                    "author": "legacy-checker",
                }
            ]
            with sqlite3.connect(database) as db:
                raw = db.execute(
                    "SELECT evidence FROM attempts WHERE id=?", (legacy_ack["id"],)
                ).fetchone()[0]
            assert raw == ATTRIBUTED_LEGACY_LOOKALIKE

    asyncio.run(exercise())
