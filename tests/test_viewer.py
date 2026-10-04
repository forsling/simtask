"""Browser transport exercises the real Store with disposable state only."""

import asyncio
import json
import shutil
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from task_mcp.server import create_server
from task_mcp.store import Store
from task_mcp.viewer import ViewerServer, _live, launch_viewer, stop_viewer


@pytest.fixture
def viewer(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", "test-browser")
    context = store.init_project(str(tmp_path / "project"), branch="main", confirmed=True)
    server = ViewerServer(store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, store, context
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(server, action, data=None, headers=None, method="POST"):
    defaults = {
        "X-Task-Token": server.token,
        "Origin": server.origin,
        "Content-Type": "application/json",
    }
    defaults.update(headers or {})
    req = urllib.request.Request(
        server.origin + action,
        data=json.dumps(data or {}).encode() if method == "POST" else None,
        method=method,
        headers=defaults,
    )
    try:
        response = urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if "json" in response.headers[
            "Content-Type"
        ] else raw


def create(server, context, **overrides):
    args = {
        "project": context["project"]["id"],
        "workstream_id": context["workstream"]["id"],
        "title": "A task",
        "body": "The specification",
        "acceptance_criteria": "Verified outcome",
        "user_request": "An explicit synthetic human request",
        **overrides,
    }
    status, task = request(server, "/api/create", args)
    assert status == 200, task
    return request(server, "/api/details", {"ids": [task["id"]]})[1]["items"][0]


def test_browse_edit_conflicts_and_decisions(viewer):
    server, store, context = viewer
    task = create(server, context)
    assert task["queue_workstream_id"]
    assert request(server, "/api/projects")[1]["items"][0]["id"] == context["project"]["id"]
    assert request(server, "/api/workstreams")[1]["items"][0]["id"] == context["workstream"]["id"]
    status, edited = request(
        server,
        "/api/edit",
        {"task_id": task["id"], "expected_revision": 1, "changes": {"title": "Updated"}},
    )
    assert status == 200 and edited["queue_workstream_id"] == context["workstream"]["id"]
    status, conflict = request(
        server,
        "/api/edit",
        {"task_id": task["id"], "expected_revision": 1, "changes": {"title": "Stale"}},
    )
    assert status == 409 and "revision_conflict" in conflict["error"]
    assert store.get_tasks([task["id"]])["items"][0]["title"] == "Updated"
    status, task = request(
        server,
        "/api/unqueue",
        {"task_id": task["id"], "expected_revision": 2},
    )
    assert status == 200 and task["queue_workstream_id"] is None
    status, task = request(
        server,
        "/api/question",
        {"task_id": task["id"], "expected_revision": 3, "text": "Which variant?"},
    )
    assert status == 200
    item = task["unresolved_items"][0]
    status, task = request(
        server,
        "/api/resolve",
        {
            "task_id": task["id"],
            "expected_revision": 4,
            "item_id": item["id"],
            "user_note": "Use the simpler variant",
        },
    )
    assert status == 200 and not task["unresolved_items"]
    for disposition in ("deferred", "open", "dropped", "open"):
        status, task = request(
            server,
            "/api/disposition",
            {
                "task_id": task["id"],
                "expected_revision": task["revision"],
                "disposition": disposition,
                "note": "Explicit synthetic decision",
                "authorization": "Synthetic actual revival instruction",
            },
        )
        assert status == 200 and task["status"] == disposition


def test_review_and_signoff_use_separate_revisions_and_store_gates(viewer):
    server, store, context = viewer
    task = create(server, context)
    attempt = store.record_result(
        task["id"],
        context["workstream"]["id"],
        1,
        "builder",
        "Result",
        "Evidence",
        artifacts=[{"kind": "artifact", "reference": "tests/test_viewer.py"}],
        verification="Evidence",
        specification_etag=store.get_tasks([task["id"]])["items"][0]["specification_etag"],
    )
    signoff = {
        "task_id": task["id"],
        "expected_revision": 2,
        "attempt_id": attempt["id"],
        "decision": "approve",
        "expected_attempt_revision": 2,
        "reasons": "I approve",
    }
    assert request(server, "/api/signoff", signoff)[0] == 400
    assert (
        request(
            server,
            "/api/human-review",
            {"attempt_id": attempt["id"], "expected_revision": 2, "user_note": "My review"},
        )[0]
        == 409
    )
    assert (
        request(
            server,
            "/api/human-review",
            {
                "attempt_id": attempt["id"],
                "expected_revision": 1,
                "user_note": "I reviewed the evidence",
            },
        )[0]
        == 200
    )
    task = store.add_unresolved(task["id"], 2, "Late unresolved decision")
    assert request(server, "/api/signoff", {**signoff, "expected_revision": 3})[0] == 400
    store.resolve_unresolved(task["id"], 3, task["unresolved_items"][0]["id"], "Resolved")
    status, completed = request(server, "/api/signoff", {**signoff, "expected_revision": 4})
    assert status == 200 and completed["status"] == "done"
    assert (
        request(
            server,
            "/api/edit",
            {"task_id": task["id"], "expected_revision": 5, "changes": {"title": "No"}},
        )[0]
        == 400
    )


def test_scope_and_global_group_progress_remain_distinct(viewer, tmp_path):
    server, store, context = viewer
    group = store.create_group(context["workstream"]["id"], "Shared")
    local = create(server, context, group_id=group["id"], group_expected_revision=1)
    remote = store.init_project(str(tmp_path / "other"), branch="main", confirmed=True)
    other = create(server, remote, group_id=group["id"], group_expected_revision=2)
    rows = request(
        server,
        "/api/tasks",
        {"project": context["project"]["id"], "workstream_id": context["workstream"]["id"]},
    )[1]["items"]
    assert [r["id"] for r in rows] == [local["id"]]
    details = request(server, "/api/details", {"ids": [group["id"]]})[1]["items"][0]
    assert set(details["members"]) == {local["id"], other["id"]}
    assert details["progress"]["total"] == 2
    assert request(server, "/api/groups")[1]["items"][0]["progress"]["total"] == 2
    local_group = store.create_group(context["workstream"]["id"], "Local")
    empty_remote = store.create_group(remote["workstream"]["id"], "Remote empty")
    project_groups = request(server, "/api/groups", {"project": context["project"]["id"]})[1][
        "items"
    ]
    assert {g["id"] for g in project_groups} == {group["id"], local_group["id"]}
    assert empty_remote["id"] not in {g["id"] for g in project_groups}
    assert next(g for g in project_groups if g["id"] == group["id"])["project_count"] == 2


def test_group_inclusion_scope_can_be_paged_beyond_workstream_preview(viewer):
    server, store, context = viewer
    ws = context["workstream"]["id"]
    included = {store.create_group(ws, f"Group {i}")["id"] for i in range(4)}
    ordinary = request(server, "/api/workstreams")[1]["items"][0]
    assert len(ordinary["groups"]) == 3 and ordinary["groups_has_more"]
    omitted = included - set(ordinary["groups"])
    assert len(omitted) == 1
    args = {"workstream_id": ws, "include_scope": True, "limit": 2}
    status, first = request(server, "/api/workstream-status", args)
    assert status == 200
    groups = first["scope"]["groups"]
    assert groups["total"] == 4 and groups["next_offset"] == 2
    status, rest = request(server, "/api/workstream-status", {**args, "offset": 2})
    assert status == 200 and rest["scope"]["groups"]["next_offset"] is None
    assert set(groups["ids"] + rest["scope"]["groups"]["ids"]) == included


def test_remote_prerequisites_transport_is_compact_and_review_clears_default_link(viewer, tmp_path):
    server, store, context = viewer
    other = store.init_project(str(tmp_path / "remote"), branch="main", confirmed=True)
    dependent = create(server, context, title="Dependent")
    blocker = create(server, other, title="Remote", body="Full remote requirements")
    attempt = store.record_result(
        blocker["id"],
        other["workstream"]["id"],
        1,
        "builder",
        "Built remote",
        "Remote evidence stays remote",
        artifacts=[{"kind": "artifact", "reference": "tests/test_viewer.py"}],
        verification="Remote evidence stays remote",
        specification_etag=store.get_tasks([blocker["id"]])["items"][0]["specification_etag"],
    )
    store.add_prerequisite(dependent["id"], 1, blocker["id"])
    status, details = request(server, "/api/details", {"ids": [dependent["id"]]})
    assert status == 200
    ref = details["items"][0]["prerequisites"][0]
    assert ref["project_id"] == other["project"]["id"] and ref["project_name"] == "remote"
    assert ref["state"] == "open" and ref["blocking"] and not ref["complete"]
    assert "Full remote requirements" not in str(details)
    assert "Remote evidence stays remote" not in str(details)
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Checked")
    query = {"project": context["project"]["id"], "workstream_id": context["workstream"]["id"]}
    status, queue = request(server, "/api/tasks", query)
    assert status == 200 and [row["id"] for row in queue["items"]] == [dependent["id"]]
    assert queue["items"][0]["prerequisites"] == [{**ref, "satisfied": True, "blocking": False}]
    assert queue["items"][0]["view"] == "ready"
    status, signed_off = request(
        server,
        "/api/signoff",
        {
            "task_id": blocker["id"],
            "expected_revision": 2,
            "expected_attempt_revision": 2,
            "attempt_id": attempt["id"],
            "decision": "approve",
            "reasons": "Synthetic informed approval",
        },
    )
    assert status == 200 and signed_off["status"] == "done"
    ref = request(server, "/api/details", {"ids": [dependent["id"]]})[1]["items"][0][
        "prerequisites"
    ][0]
    assert ref["state"] == "done" and ref["complete"] and not ref["blocking"]
    assert request(server, "/api/tasks", query)[1]["items"][0]["view"] == "ready"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_prerequisite_rows_use_compact_facts_and_fetch_details_only_on_navigation():
    script = Path(__file__).with_name("viewer_prerequisites.test.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"X-Task-Token": "wrong"}, 401),
        ({"Origin": "http://evil.invalid"}, 403),
        ({"Origin": "null"}, 403),
        ({"Origin": ""}, 403),
        ({"Host": "evil.invalid"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Content-Type": "text/plain"}, 415),
    ],
)
def test_request_security(viewer, headers, expected):
    server, _, _ = viewer
    assert request(server, "/api/projects", headers=headers)[0] == expected


def test_narrow_api_and_safe_assets(viewer):
    server, _, context = viewer
    assert request(server, "/api/_connect")[0] == 400
    assert request(server, "/api/init_project", {"path": "/tmp/not-authorized"})[0] == 400
    assert request(server, "/api/projects", {"db": "/tmp/other"})[0] == 400
    task = create(server, context, title="<script>alert(1)</script>")
    assert (
        request(server, "/api/details", {"ids": [task["id"]]})[1]["items"][0]["title"]
        == task["title"]
    )
    status, script = request(server, "/app.js", method="GET")
    assert status == 200 and b"textContent" in script and b"innerHTML" not in script
    assert request(server, "/../store.py", method="GET")[0] == 404
    req = urllib.request.Request(server.origin + "/")
    with urllib.request.urlopen(req) as response:
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["Referrer-Policy"] == "no-referrer"
    assert request(server, "/api/ping", headers={"X-Task-Token": ""}, method="GET")[0] == 401


def test_idempotent_launch_stop_and_restart(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    Store(database)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: launch_viewer(database), range(3)))
        assert len({r["url"] for r in results}) == 1
        mcp_result = asyncio.run(create_server(Store(database)).call_tool("open_task_viewer", {}))
        assert mcp_result.structured_content["url"] == results[0]["url"]
        assert mcp_result.structured_content["reused"] is True
        assert sum(not r["reused"] for r in results) == 1
        assert urlsplit(results[0]["url"]).hostname == "127.0.0.1"
        state = database.with_name(database.name + ".viewer.json")
        assert stat.S_IMODE(state.stat().st_mode) == 0o600
        assert stop_viewer(database)
        for _ in range(100):
            if not state.exists():
                break
            time.sleep(0.02)
        assert not state.exists()
        assert not stop_viewer(database)
        restarted = launch_viewer(database)
        assert not restarted["reused"] and restarted["url"] != results[0]["url"]
    finally:
        stop_viewer(database)


@pytest.fixture
def control_endpoint():
    hits = []
    redirects = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append((self.path, self.headers.get("X-Task-Token")))
            if self.path in redirects:
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/leaked")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"service":"task-mcp-viewer"}')

        do_POST = do_GET

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", hits, redirects
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_control_requests_ignore_environment_proxies(tmp_path, monkeypatch, control_endpoint):
    proxy, hits, _ = control_endpoint
    for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(key, proxy)
    for key in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(key, "")
    database = tmp_path / "tasks.sqlite3"
    try:
        first = launch_viewer(database)
        assert launch_viewer(database)["url"] == first["url"]
        assert stop_viewer(database)
        assert hits == [], "The proxy must receive neither liveness nor stop credentials"
    finally:
        stop_viewer(database)


def test_control_requests_never_follow_redirects(tmp_path, control_endpoint):
    origin, hits, redirects = control_endpoint
    database = tmp_path / "tasks.sqlite3"
    state = tmp_path / "tasks.sqlite3.viewer.json"
    state.write_text(json.dumps({"origin": origin, "token": "synthetic-bearer"}))
    redirects.add("/api/ping")
    assert _live(state) is None
    assert hits == [("/api/ping", "synthetic-bearer")]
    redirects.clear()
    redirects.add("/api/stop")
    with pytest.raises(urllib.error.HTTPError, match="Viewer redirect refused"):
        stop_viewer(database)
    assert [path for path, _ in hits] == ["/api/ping", "/api/ping", "/api/stop"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_project_and_shared_group_navigation():
    script = Path(__file__).with_name("viewer_groups.test.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_attempt_actions_and_details_select_eligible_results():
    script = Path(__file__).with_name("viewer_attempt.test.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_pending_dialog_submission_cannot_be_abandoned():
    script = Path(__file__).with_name("viewer_dialog.test.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_queue_inbox_moves_return_compact_continuation_without_notes(viewer):
    server, store, context = viewer
    ws = context["workstream"]["id"]
    draft = create(server, context, workstream_id=None, user_request="Save user-origin idea")
    assert draft["queue_workstream_id"] is None and draft["source"] == "user"
    status, queued = request(
        server,
        "/api/queue",
        {"task_id": draft["id"], "workstream_id": ws, "expected_revision": draft["revision"]},
    )
    assert status == 200 and queued["queue_workstream_id"] == ws
    assert queued["revision"] == draft["revision"] + 1
    assert not {"title", "body", "attempts", "approval_decision"} & queued.keys()
    status, inbox = request(
        server, "/api/unqueue", {"task_id": draft["id"], "expected_revision": queued["revision"]}
    )
    assert status == 200 and inbox["queue_workstream_id"] is None
    assert inbox["spec_revision"] == queued["spec_revision"] and inbox["gate_diagnostics"] == [
        "inbox"
    ]
    assert store.get_tasks([draft["id"]])["items"][0]["user_request"] == "Save user-origin idea"
    assert (
        request(
            server,
            "/api/unqueue",
            {"task_id": draft["id"], "expected_revision": queued["revision"]},
        )[0]
        == 409
    )
    assert (
        request(
            server, "/api/accept", {"task_id": draft["id"], "expected_revision": inbox["revision"]}
        )[0]
        == 400
    )
    assert (
        request(
            server,
            "/api/withdraw-acceptance",
            {"task_id": draft["id"], "expected_revision": inbox["revision"]},
        )[0]
        == 400
    )
    status, created = request(
        server,
        "/api/create",
        {
            "project": context["project"]["id"],
            "workstream_id": ws,
            "title": "Queued creation",
            "user_request": "Actual request",
        },
    )
    assert status == 200 and created["queue_workstream_id"] == ws
    assert not {"title", "body", "attempts", "user_request"} & created.keys()


def test_approved_and_draft_amendment_http_flow(viewer):
    server, store, context = viewer
    task = create(server, context)
    payload = {
        "task_id": task["id"],
        "expected_revision": task["revision"],
        "changes": {"body": "Complete amended scope", "acceptance_criteria": "New proof"},
    }
    status, denied = request(server, "/api/edit", payload)
    assert status == 400 and "specification_read_required" in denied["error"]
    assert store.get_tasks([task["id"]])["items"][0] == task
    payload["specification_etag"] = task["specification_etag"]
    status, saved = request(server, "/api/edit", payload)
    assert (
        status == 200
        and saved["queue_workstream_id"] == context["workstream"]["id"]
        and saved["spec_changed"]
    )
    assert saved["spec_revision"] == 2
    assert not {"body", "attempts", "acceptance_note"} & saved.keys()
    status, conflict = request(server, "/api/edit", {**payload, "changes": {"body": "Stale"}})
    assert status == 409 and "full specification" in conflict["error"]
    latest = request(server, "/api/details", {"ids": [task["id"]]})[1]["items"][0]
    assert latest["body"] == "Complete amended scope"
    status, draft = request(
        server,
        "/api/edit",
        {
            "task_id": task["id"],
            "expected_revision": latest["revision"],
            "specification_etag": latest["specification_etag"],
            "changes": {"body": "Draft scope"},
        },
    )
    assert (
        status == 200
        and draft["queue_workstream_id"] == context["workstream"]["id"]
        and draft["spec_revision"] == 3
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_creation_edits_queue_and_inbox_form_handlers():
    script = Path(__file__).with_name("viewer_queue.test.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "decision,disposition",
    [
        ("approve", "done"),
        ("rework", "rework"),
        ("revise", "open"),
        ("drop", "dropped"),
    ],
)
def test_purpose_result_decisions_over_real_viewer_transport(viewer, decision, disposition):
    server, store, context = viewer
    task = create(server, context)
    attempt = store.record_result(
        task["id"],
        context["workstream"]["id"],
        1,
        "worker",
        "Result",
        "Actual evidence",
        artifacts=[{"kind": "artifact", "reference": "tests/test_viewer.py"}],
        verification="Actual evidence",
        specification_etag=store.get_tasks([task["id"]])["items"][0]["specification_etag"],
    )
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Independent review")
    data = {
        "task_id": task["id"],
        "expected_revision": 2,
        "attempt_id": attempt["id"],
        "expected_attempt_revision": 1,
        "decision": decision,
        "reasons": "Actual synthetic verdict",
    }
    assert request(server, "/api/signoff", data)[0] == 409
    data["expected_attempt_revision"] = 2
    status, ack = request(server, "/api/signoff", data)
    assert status == 200 and ack["status"] == disposition
    assert not {"attempts", "body", "signoff_decisions"} & ack.keys()
    saved = request(server, "/api/details", {"ids": [task["id"]]})[1]["items"][0]
    judgment = saved["signoff_decisions"][0]
    assert judgment["decision_ref"] == ack["decision_ref"]
    assert judgment["reasons"] == "Actual synthetic verdict"
    assert not {"purpose_source", "result_judgment"} & judgment.keys()
    if decision in {"rework", "revise"}:
        assert saved["latest_rejection"]["reasons"] == judgment["reasons"]
    assert saved["attempts"][0]["review_note"] == "Independent review"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_frontend_purpose_result_decision_forms():
    script = Path(__file__).with_name("viewer_signoff.test.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_atomic_moves_over_real_viewer_transport(viewer):
    server, store, context = viewer
    first = create(server, context, title="Done anchor")
    second = create(server, context, title="Prioritize")
    ws, project = context["workstream"]["id"], context["project"]["id"]
    attempt = store.record_result(
        first["id"],
        ws,
        1,
        "builder",
        "Delivered",
        "Proof",
        artifacts=[{"kind": "artifact", "reference": "tests/test_viewer.py"}],
        verification="Proof",
        specification_etag=store.get_tasks([first["id"]])["items"][0]["specification_etag"],
    )
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Reviewed")
    store.signoff_task(first["id"], 2, "approve", "Synthetic human verdict", attempt["id"], 2)
    done = request(server, "/api/details", {"ids": [first["id"]]})[1]["items"][0]
    status, board = request(server, "/api/tasks", {"project": project})
    assert status == 200
    payload = dict(
        project=project,
        task_id=second["id"],
        anchor_id=first["id"],
        position="before",
        expected_order_revision=board["project_order_revision"],
        instruction="Prioritize this work",
    )
    status, ack = request(server, "/api/reorder", payload)
    assert status == 200 and ack["changed"] and "ordered_ids" not in ack
    assert request(server, "/api/reorder", payload)[0] == 409
    payload["expected_order_revision"] = ack["project_order_revision"]
    assert request(server, "/api/reorder", payload)[1]["changed"] is False
    after = request(server, "/api/details", {"ids": [first["id"]]})[1]["items"][0]
    assert {k: v for k, v in after.items() if k != "order_key"} == {
        k: v for k, v in done.items() if k != "order_key"
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_frontend_atomic_order_form_and_conflict_reconciliation():
    script = Path(__file__).with_name("viewer_order.test.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_concerns_reach_real_viewer_details_and_status(viewer):
    server, store, context = viewer
    task = create(server, context)
    ws = context["workstream"]["id"]
    attempt = store.record_result(
        task["id"],
        ws,
        task["revision"],
        "builder",
        "Built",
        "Context",
        [{"kind": "artifact", "reference": "synthetic.txt"}],
        "Synthetic verification",
        task["specification_etag"],
        concerns=[{"kind": "value", "text": "Priority changes require changing this task."}],
    )
    store.record_review(
        attempt["id"],
        1,
        "checker",
        "pass",
        "Checked",
        concerns=[{"kind": "design", "text": "Another design changes scope."}],
    )
    status, details = request(server, "/api/details", {"ids": [task["id"]]})
    assert status == 200
    proof = details["items"][0]["attempts"][0]
    assert [c["source"] for c in proof["concerns"]] == ["implementer", "reviewer"]
    assert proof["evidence"] == "Context"
    status, overview = request(server, "/api/workstream-status", {"workstream_id": ws})
    assert status == 200 and overview["concern_tasks"]["items"][0]["view"] == "signoff"
    assert overview["concern_tasks"]["items"][0]["concern_count"] == 2
    assert "Another design" not in json.dumps(overview)
    status, ack = request(
        server,
        "/api/signoff",
        {
            "task_id": task["id"],
            "expected_revision": attempt["task_revision"],
            "attempt_id": attempt["id"],
            "expected_attempt_revision": 2,
            "decision": "approve",
            "reasons": "Synthetic verdict",
        },
    )
    assert status == 200 and ack["status"] == "done"
    assert store.get_attempt(attempt["id"])["concerns"] == proof["concerns"]
