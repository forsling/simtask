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
        "scope": "workstream",
        "title": "A task",
        "body": "The specification",
        "acceptance_criteria": "Verified outcome",
        "user_request": "An explicit synthetic human request",
        **overrides,
    }
    status, task = request(server, "/api/create", args)
    assert status == 200, task
    return task


def test_browse_edit_conflicts_and_decisions(viewer):
    server, store, context = viewer
    task = create(server, context)
    assert task["accepted"]
    assert request(server, "/api/projects")[1]["items"][0]["id"] == context["project"]["id"]
    assert request(server, "/api/workstreams")[1]["items"][0]["id"] == context["workstream"]["id"]
    status, edited = request(
        server,
        "/api/edit",
        {"task_id": task["id"], "expected_revision": 1, "changes": {"title": "Updated"}},
    )
    assert status == 200 and not edited["accepted"]
    status, conflict = request(
        server,
        "/api/edit",
        {"task_id": task["id"], "expected_revision": 1, "changes": {"title": "Stale"}},
    )
    assert status == 409 and "revision_conflict" in conflict["error"]
    assert store.get_tasks([task["id"]])["items"][0]["title"] == "Updated"
    status, task = request(
        server,
        "/api/accept",
        {"task_id": task["id"], "expected_revision": 2, "user_note": "I accept this specification"},
    )
    assert status == 200 and task["accepted"]
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
            },
        )
        assert status == 200 and task["status"] == disposition


def test_review_and_signoff_use_separate_revisions_and_store_gates(viewer):
    server, store, context = viewer
    task = create(server, context)
    attempt = store.record_result(
        task["id"], context["workstream"]["id"], 1, "builder", "Result", "Evidence"
    )
    signoff = {
        "task_id": task["id"],
        "expected_revision": 2,
        "attempt_id": attempt["id"],
        "verdict": "approve",
        "user_note": "I approve",
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
    assert (
        len(next(g for g in project_groups if g["id"] == group["id"])["progress"]["by_project"])
        == 2
    )


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
