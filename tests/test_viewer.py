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

from task_mcp import store as store_module
from task_mcp.server import create_server
from task_mcp.store import Store
from task_mcp.viewer import ASSETS, ViewerServer, _live, launch_viewer, stop_viewer


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
    # The browser creates no tasks; agents do, through the Store.
    args = {
        "project": context["project"]["id"],
        "workstream_id": context["workstream"]["id"],
        "title": "A task",
        "body": "The specification",
        "acceptance_criteria": "Verified outcome",
        "user_request": "An explicit synthetic human request",
        "source": "user",
        **overrides,
    }
    task = server.store.create_task(**args)
    return request(server, "/api/details", {"ids": [task["id"]]})[1]["items"][0]


def test_browse_membership_questions_and_status_changes(viewer):
    server, store, context = viewer
    task = create(server, context)
    assert task["workstream_ids"]
    assert request(server, "/api/projects")[1]["items"][0]["id"] == context["project"]["id"]
    assert request(server, "/api/workstreams")[1]["items"][0]["id"] == context["workstream"]["id"]
    status, task = request(
        server,
        "/api/remove-from-workstream",
        {
            "task_id": task["id"],
            "workstream_id": context["workstream"]["id"],
            "expected_revision": 1,
        },
    )
    assert status == 200 and task["workstream_ids"] == []
    status, task = request(
        server,
        "/api/question",
        {"task_id": task["id"], "expected_revision": 2, "text": "Which variant?"},
    )
    assert status == 200 and task["unresolved_items"][0]["text"] == "Which variant?"
    status, conflict = request(
        server,
        "/api/question",
        {"task_id": task["id"], "expected_revision": 2, "text": "Stale"},
    )
    assert status == 409 and "revision_conflict" in conflict["error"]
    assert len(store.get_tasks([task["id"]])["items"][0]["unresolved_items"]) == 1
    # Defer, resume and drop carry the viewer's stand-in reason when the user gives none;
    # leaving dropped still needs the user's reason as its authorization.
    for disposition, extra in (
        ("deferred", {"note": "Deferred in the browser."}),
        ("open", {"note": "Resumed in the browser."}),
        ("dropped", {"note": "Dropped in the browser."}),
    ):
        status, task = request(
            server,
            "/api/disposition",
            {
                "task_id": task["id"],
                "expected_revision": task["revision"],
                "disposition": disposition,
                **extra,
            },
        )
        assert status == 200 and task["status"] == disposition
    revive = {
        "task_id": task["id"],
        "expected_revision": task["revision"],
        "disposition": "open",
        "note": "Needed after all",
    }
    status, refused = request(server, "/api/disposition", revive)
    assert status == 400 and "revival_authorization_required" in refused["error"]
    status, task = request(
        server, "/api/disposition", {**revive, "authorization": "Needed after all"}
    )
    assert status == 200 and task["status"] == "open"


def test_viewer_offers_no_create_edit_answer_review_or_signoff_actions(viewer):
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
        specification_etag=task["specification_etag"],
    )
    store.record_review(attempt["id"], 1, "reviewer", "pass", "Checked")
    before = store.get_tasks([task["id"]])["items"][0]
    for action, data in (
        ("create", {"project": context["project"]["id"], "title": "From the browser"}),
        ("edit", {"task_id": task["id"], "expected_revision": 2, "changes": {"title": "No"}}),
        (
            "resolve",
            {"task_id": task["id"], "expected_revision": 2, "item_id": "x", "user_note": ""},
        ),
        ("human-review", {"attempt_id": attempt["id"], "expected_revision": 2, "user_note": "n"}),
        (
            "signoff",
            {
                "task_id": task["id"],
                "expected_revision": 2,
                "attempt_id": attempt["id"],
                "expected_attempt_revision": 2,
                "decision": "approve",
                "reasons": "No",
            },
        ),
        ("next-action", {"workstream_id": context["workstream"]["id"]}),
    ):
        status, error = request(server, "/api/" + action, data)
        assert (status, error) == (400, {"error": "unknown_action"}), action
    assert store.get_tasks([task["id"]])["items"][0] == before
    assert store.list_tasks(context["project"]["id"])["total"] == 1


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
    signed_off = store.signoff_task(
        blocker["id"], 2, "approve", "Synthetic informed approval", attempt["id"], 2
    )
    assert signed_off["status"] == "done"
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


LOCATION_PATHS = [
    "/p/1c4684b6",
    "/p/1c4684b6/t/a5dfba02",
    "/p/1c4684b6/g",
    "/p/1c4684b6/g/a5dfba02",
    "/w/1c4684b6",
    "/w/1c4684b6/t/a5dfba02",
    "/w/" + "1c4684b6" * 4 + "/t/" + "a5dfba02" * 4,
    "/g/a5dfba02",
    "/sg",
    "/sg/a5dfba02",
]


@pytest.mark.parametrize("path", LOCATION_PATHS)
def test_location_paths_serve_the_app_page_with_unchanged_checks(viewer, path):
    server, _, _ = viewer
    page = (ASSETS / "index.html").read_bytes()
    # The page itself needs no token, exactly like "/"; its API calls still do.
    status, body = request(server, path, headers={"X-Task-Token": ""}, method="GET")
    assert (status, body) == (200, page)
    req = urllib.request.Request(server.origin + path)
    with urllib.request.urlopen(req) as response:
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cache-Control"] == "no-store"
    for headers in (
        {"Host": "evil.invalid"},
        {"Origin": "http://evil.invalid"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ):
        assert request(server, path, headers=headers, method="GET")[0] == 403, headers
        assert request(server, "/", headers=headers, method="GET")[0] == 403, headers


@pytest.mark.parametrize(
    "path",
    [
        "/w",
        "/w/",
        "/w/1c4684b6/",
        "/w/1C4684B6",
        "/w/zz",
        "/w/" + "a" * 33,
        "/w/1c4684b6?view=1",
        "/w/1c4684b6/t",
        "/w/1c4684b6/g/a5dfba02",
        "/p/1c4684b6/x",
        "/p/1c4684b6/t/a5dfba02/more",
        "/p/1c4684b6/g/",
        "/g",
        "/g/a5dfba02/t/1c4684b6",
        "/sg/",
        "/t/a5dfba02",
        "/project/prj_1c4684b6",
        "/index.html",
        "/w/../app.js",
        "/?x=1",
    ],
)
def test_unknown_paths_stay_not_found(viewer, path):
    server, _, _ = viewer
    assert request(server, path, method="GET") == (404, {"error": "Not found"})


def test_api_routes_keep_their_status_beside_location_paths(viewer):
    server, _, _ = viewer
    assert request(server, "/api/ping", method="GET")[0] == 200
    assert request(server, "/api/ping", headers={"X-Task-Token": ""}, method="GET")[0] == 401
    assert request(server, "/api/w/1c4684b6", headers={"X-Task-Token": ""}, method="GET")[0] == 401
    assert request(server, "/api/w/1c4684b6", method="GET")[0] == 404
    assert request(server, "/api/projects", method="GET")[0] == 404
    assert request(server, "/w/1c4684b6", method="POST")[0] == 400
    assert request(server, "/w/1c4684b6", headers={"X-Task-Token": ""})[0] == 401
    assert (
        request(
            server,
            "/api/resolve-prefix",
            {"kind": "workstream", "prefix": "1c"},
            {"X-Task-Token": "x"},
        )[0]
        == 401
    )


def test_resolve_matches_workstream_and_group_prefixes_and_reports_ambiguity(tmp_path, monkeypatch):
    # Controlled IDs: two workstreams share a short prefix across projects.
    planned = {
        "wst_": iter(["1c4684b6" + "1" * 24, "1c4684b6" + "2" * 24]),
        "tsk_": iter(["a5dfba02" + "1" * 24, "a5dfba02" + "2" * 24, "a5dfba02" + "3" * 24]),
    }
    original = store_module._id
    monkeypatch.setattr(
        store_module,
        "_id",
        lambda prefix: prefix + next(planned[prefix]) if prefix in planned else original(prefix),
    )
    store = Store(tmp_path / "tasks.sqlite3", "test-browser")
    one = store.init_project(str(tmp_path / "one"), branch="main", confirmed=True)
    two = store.init_project(str(tmp_path / "two"), branch="main", confirmed=True)
    w1, w2 = one["workstream"]["id"], two["workstream"]["id"]
    task = store.create_task(
        one["project"]["id"],
        "A task",
        "Body",
        "Done",
        workstream_id=w1,
        user_request="An explicit synthetic human request",
    )
    group_one = store.create_group(w1, "Group one")
    group_two = store.create_group(w2, "Group two")
    assert task["id"].startswith("tsk_a5dfba02") and group_two["id"].startswith("tsk_a5dfba02")
    server = ViewerServer(store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:

        def resolve(kind, prefix):
            status, result = request(
                server, "/api/resolve-prefix", {"kind": kind, "prefix": prefix}
            )
            assert status == 200, result
            return result["items"]

        pid1, pid2 = one["project"]["id"], two["project"]["id"]
        assert resolve("workstream", "1c4684b6") == [
            {"id": w1, "project_id": pid1},
            {"id": w2, "project_id": pid2},
        ]
        assert resolve("workstream", w2[4:]) == [{"id": w2, "project_id": pid2}]
        assert resolve("workstream", "1c4684b62") == [{"id": w2, "project_id": pid2}]
        assert resolve("workstream", "ffffffff") == []
        # Groups share the task ID space; a task is never a group match.
        ids = lambda items: [item["id"] for item in items]  # noqa: E731
        assert ids(resolve("group", "a5dfba02")) == [group_one["id"], group_two["id"]]
        assert resolve("group", task["id"][4:]) == []
        assert ids(resolve("group", group_two["id"][4:13])) == [group_two["id"]]
        for data in (
            {"kind": "task", "prefix": "a5dfba02"},
            {"kind": "project", "prefix": "a5dfba02"},
            {"kind": "workstream", "prefix": ""},
            {"kind": "workstream", "prefix": "1C4684B6"},
            {"kind": "workstream", "prefix": "1c46%"},
            {"kind": "workstream", "prefix": "a" * 33},
            {"kind": "workstream", "prefix": 1},
            {"kind": ["workstream"], "prefix": "1c"},
            {"kind": "workstream"},
        ):
            assert request(server, "/api/resolve-prefix", data)[0] == 400, data
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


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
    assert draft["workstream_ids"] == [] and draft["source"] == "user"
    status, queued = request(
        server,
        "/api/add-to-workstream",
        {"task_id": draft["id"], "workstream_id": ws, "expected_revision": draft["revision"]},
    )
    assert status == 200 and queued["workstream_ids"] == [ws]
    assert queued["revision"] == draft["revision"] + 1
    assert not {"title", "body", "attempts", "approval_decision"} & queued.keys()
    status, inbox = request(
        server,
        "/api/remove-from-workstream",
        {"task_id": draft["id"], "workstream_id": ws, "expected_revision": queued["revision"]},
    )
    assert status == 200 and inbox["workstream_ids"] == []
    assert inbox["spec_revision"] == queued["spec_revision"] and inbox["gate_diagnostics"] == [
        "inbox"
    ]
    assert store.get_tasks([draft["id"]])["items"][0]["user_request"] == "Save user-origin idea"
    assert (
        request(
            server,
            "/api/remove-from-workstream",
            {"task_id": draft["id"], "workstream_id": ws, "expected_revision": queued["revision"]},
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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_membership_and_dialog_form_handlers():
    script = Path(__file__).with_name("viewer_queue.test.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_frontend_signoff_handoff_and_status_controls():
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
    status, board = request(server, "/api/tasks", {"project": project, "workstream_id": ws})
    assert status == 200
    payload = dict(
        workstream_id=ws,
        task_ids=[second["id"]],
        expected_order_revision=board["workstream_order_revision"],
    )
    status, ack = request(server, "/api/reorder", payload)
    assert status == 200 and ack["changed"] and "ordered_ids" not in ack
    assert request(server, "/api/reorder", payload)[0] == 409
    payload["expected_order_revision"] = ack["workstream_order_revision"]
    assert request(server, "/api/reorder", payload)[1]["changed"] is False
    after = request(server, "/api/details", {"ids": [first["id"]]})[1]["items"][0]
    assert {k: v for k, v in after.items() if k != "order_key"} == {
        k: v for k, v in done.items() if k != "order_key"
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_frontend_drag_and_drop_order_and_conflict_reconciliation():
    script = Path(__file__).with_name("viewer_order.test.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_frontend_location_urls_history_and_stale_fallbacks():
    script = Path(__file__).with_name("viewer_routes.test.cjs")
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
    assert "concerns_json" not in json.dumps(details)
    assert [c["source"] for c in proof["concerns"]] == ["implementer", "reviewer"]
    assert proof["evidence"] == "Context"
    status, overview = request(server, "/api/workstream-status", {"workstream_id": ws})
    assert status == 200 and overview["concern_tasks"]["items"][0]["view"] == "signoff"
    assert overview["concern_tasks"]["items"][0]["concern_count"] == 2
    assert "Another design" not in json.dumps(overview)
    ack = store.signoff_task(
        task["id"], attempt["task_revision"], "approve", "Synthetic verdict", attempt["id"], 2
    )
    assert ack["status"] == "done"
    assert store.get_attempt(attempt["id"])["concerns"] == proof["concerns"]


def test_legacy_json_never_becomes_concern_metadata_in_real_viewer(viewer):
    server, store, context = viewer
    task = create(server, context)
    ws = context["workstream"]["id"]
    attempt = store.record_result(
        task["id"],
        ws,
        task["revision"],
        "builder",
        "Historical",
        "Placeholder",
        [{"kind": "artifact", "reference": "synthetic.txt"}],
        "Synthetic verification",
        task["specification_etag"],
    )
    original = (
        " \n"
        + json.dumps(
            {
                "format": "attempt-concerns-v1",
                "original_evidence": "Historical inner β text",
                "concerns": [
                    {
                        "kind": "value",
                        "text": "Historical JSON data",
                        "source": "reviewer",
                        "author": "old",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n\t"
    )
    with store._connect() as db:
        db.execute("UPDATE attempts SET evidence=? WHERE id=?", (original, attempt["id"]))
    for after in (False, True):
        if after:
            store.record_review(
                attempt["id"],
                1,
                "checker",
                "pass",
                "Checked historical proof",
                concerns=[{"kind": "design", "text": "Actual review contribution"}],
            )
        status, details = request(server, "/api/details", {"ids": [task["id"]]})
        assert status == 200 and "concerns_json" not in json.dumps(details)
        proof = details["items"][0]["attempts"][0]
        assert proof["evidence"] == original
        assert proof["concerns"] == (
            [
                {
                    "kind": "design",
                    "text": "Actual review contribution",
                    "source": "reviewer",
                    "author": "checker",
                }
            ]
            if after
            else []
        )
        status, overview = request(server, "/api/workstream-status", {"workstream_id": ws})
        assert status == 200 and overview["status"]["concern_count"] == int(after)
        assert overview["concern_tasks"]["total"] == int(after)
        assert "Historical JSON data" not in json.dumps(overview)
    restarted = Store(store.path)
    assert restarted.get_attempt(attempt["id"]) == proof
    with restarted._connect() as db:
        assert (
            db.execute("SELECT evidence FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
            == original
        )


def test_browser_add_b_retains_a_remove_a_retains_b_and_local_proof(viewer):
    server, store, ctx = viewer
    project, a = ctx["project"]["id"], ctx["workstream"]["id"]
    b = store.init_workstream(
        project, ctx["workstream"]["checkout_path"], branch="b", confirmed=True
    )["workstream"]["id"]
    task = store.create_task(project, "Shared browser task", workstream_id=a)
    status, added = request(
        server,
        "/api/add-to-workstream",
        {"task_id": task["id"], "workstream_id": b, "expected_revision": 1},
    )
    assert status == 200 and set(added["workstream_ids"]) == {a, b}
    proof = store.record_result(
        task["id"],
        a,
        added["revision"],
        "builder",
        "Built A",
        "Proof A",
        [{"kind": "artifact", "reference": str(__file__)}],
        "Synthetic browser verification",
        task["specification_etag"],
    )
    status, detail = request(server, "/api/details", {"ids": [task["id"]]})
    assert status == 200 and set(detail["items"][0]["workstream_ids"]) == {a, b}
    status, removed = request(
        server,
        "/api/remove-from-workstream",
        {"task_id": task["id"], "workstream_id": a, "expected_revision": proof["task_revision"]},
    )
    assert status == 200 and removed["workstream_ids"] == [b] and removed["adopted"]
    assert store.get_attempt(proof["id"])["workstream_id"] == a
    assert store.get_next_action(b)["action"] == "implement"


def test_quick_idea_is_one_audited_inbox_task_held_until_processed(viewer):
    server, store, context = viewer
    project, ws = context["project"]["id"], context["workstream"]["id"]
    text, note = "  Colour-code stale workstreams ", "Maybe after a week without results."
    status, saved = request(server, "/api/idea", {"project": project, "text": text, "note": note})
    assert status == 200 and saved["changed"] and saved["workstream_ids"] == []
    task = store.get_tasks([saved["id"]])["items"][0]
    assert (task["title"], task["body"], task["acceptance_criteria"]) == (
        "Colour-code stale workstreams",
        note,
        "",
    )
    assert (task["source"], task["user_request"]) == ("user", text + "\n\n" + note)
    assert task["revision"] == 1 and task["status"] == "open"
    assert [i["text"] for i in task["unresolved_items"]] == [store_module.IDEA_ITEM]
    assert store_module.IDEA_ITEM.startswith("Idea to process:")
    # One audited write holds the task and its item together.
    events = store.list_events(task_id=task["id"], include_details=True)["items"]
    assert [
        (e["action"], e["outcome"], e["actor"]) for e in events if "read" not in e["action"]
    ] == [("task.idea_captured", "ok", "test-browser")]
    # All tasks lists it as held (Needs input, Design/decision); no agent picks it up,
    # even if someone later adds it to a workstream before processing it.
    row = next(r for r in store.list_tasks(project)["items"] if r["id"] == task["id"])
    assert row["unresolved_count"] == 1
    store.add_to_workstream(task["id"], ws, 1)
    next_action = store.get_next_action(ws)
    assert next_action["task"] is None and next_action["diagnostics"]["unresolved_items"] == 1
    # A bare line works too; no note means an empty body and the line as the request.
    status, bare = request(server, "/api/idea", {"project": project, "text": "Dark mode"})
    bare = store.get_tasks([bare["id"]])["items"][0]
    assert (bare["body"], bare["user_request"]) == ("", "Dark mode")


@pytest.mark.parametrize(
    "data",
    [
        {"text": ""},
        {"text": "   "},
        {"text": "Two\nlines"},
        {"text": "x" * 201},
        {"text": "Fine", "note": "y" * 501},
        {"text": "Fine", "note": "Two\rlines"},
        {"text": 3},
    ],
)
def test_quick_idea_rejects_anything_but_a_line_and_a_sentence(viewer, data):
    server, store, context = viewer
    status, error = request(server, "/api/idea", {"project": context["project"]["id"], **data})
    assert status == 400 and "invalid_idea" in error["error"]
    assert store.list_tasks(context["project"]["id"])["total"] == 0
    events = store.list_events(project=context["project"]["id"], limit=100)["items"]
    assert any((e["action"], e["outcome"]) == ("task.idea_captured", "error") for e in events)


def test_quick_idea_is_a_viewer_operation_not_an_mcp_tool(tmp_path):
    tools = asyncio.run(create_server(Store(tmp_path / "tasks.sqlite3")).list_tools())
    assert not [t.name for t in tools if "idea" in t.name]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_frontend_quick_idea_dialog_confirmation_and_hand_off():
    script = Path(__file__).with_name("viewer_idea.test.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
