"""Explicit, local-only browser companion. Domain decisions remain in Store."""

import argparse
import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from task_mcp.store import Store, TaskError, default_database

ASSETS = Path(__file__).with_name("viewer_assets")
_ID = "[0-9a-f]{1,32}"
# 96, not the 40-character creation limit: older, longer public IDs still route.
_PUBLIC_ID = r"(?=[^/]{1,96}(?:/|$))[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
_TASK_ID = rf"(?:id/{_PUBLIC_ID}|{_ID})"
# Any loopback port, not only the listener's own: an SSH tunnel (./run.sh --remote)
# forwards a different laptop port. A literal 127.0.0.1 still defeats DNS rebinding,
# and Origin must still match this Host exactly.
LOOPBACK_HOST = re.compile(r"127\.0\.0\.1:[1-9][0-9]{0,4}")
# Location URLs the app page is served for: /p/<id>[/t/<id> | /u[/t/<id>] | /g[/<id>]],
# /w/<id>[/t/<id>], /g/<id> and /sg[/<id>]. /p/<id>/u is the project's Unassigned view.
# Full public task/group IDs follow /id/; legacy hex prefixes stay unmarked.
APP_PATH = re.compile(
    rf"/(?:p/{_ID}(?:/t/{_TASK_ID}|/u(?:/t/{_TASK_ID})?|/g(?:/{_TASK_ID})?)?|w/{_ID}(?:/t/{_TASK_ID})?|g/{_TASK_ID}|sg(?:/{_TASK_ID})?)"
)


def dispatch(store, action, data):
    """An intentionally small allowlist, never client-selected attribute access."""
    operations = {
        "projects": store.list_projects,
        "workstreams": store.list_workstreams,
        "workstream-status": store.workstream_status,
        "groups": store.list_groups,
        "tasks": store.list_tasks,
        "details": store.get_tasks,
        "resolve-prefix": store.resolve_prefix,
        "events": store.list_events,
        # A task's or group's meaningful history, newest first, in pages (no MCP tool).
        "activity": store.task_activity,
        # One result's complete proof, opened from its Activity entry (the attempt read).
        "attempt": store.get_attempt,
        "notes": store.read_notes,
        "reorder": store.reorder_tasks,
        "add-to-workstream": store.add_to_workstream,
        "remove-from-workstream": store.remove_from_workstream,
        "disposition": store.set_disposition,
        "question": store.add_unresolved,
        # A quick idea becomes an Unassigned (inbox) task held by an Idea to process item
        # (no MCP tool).
        "idea": store.capture_idea,
        # The ID an idea would get from its title: the Store's one short-slug rule.
        "idea-id": store.suggest_task_id,
    }
    if action not in operations:
        raise TaskError("unknown_action")
    if action == "question":
        data = {**data, "handling": "user"}
    if action == "tasks":
        # The board shows every section, closed ones included, from unabridged cards.
        data = {**data, "include_inactive": True, "full_cards": True}
    # Store._standing is the one rule for where a task stands: each card's and task
    # detail's standing, and each workstream's sidebar Needs input count.
    if action == "workstreams":
        data = {**data, "standings": True}
    if action == "details":
        data = {**data, "standing": True}
    return operations[action](**data)


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, store, token=None):
        self.store = store
        # Keep this process's UI paired with its loaded dispatch code. Source updates
        # take effect together only when the viewer is explicitly restarted.
        self.assets = {
            name: (ASSETS / name).read_bytes() for name in ("index.html", "app.js", "style.css")
        }
        self.token = token or secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", 0), ViewerHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class ViewerHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Neither credentials nor task text belongs in access logs.

    def reply(self, status, content, kind="application/json"):
        payload = json.dumps(content).encode() if kind == "application/json" else content
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def allowed(self, authenticated=False):
        host = self.headers.get("Host") or ""
        if not LOOPBACK_HOST.fullmatch(host):
            self.reply(403, {"error": "Invalid host"})
            return False
        self.origin = "http://" + host
        origin = self.headers.get("Origin")
        if origin and origin != self.origin:
            self.reply(403, {"error": "Cross-origin requests are forbidden"})
            return False
        if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            self.reply(403, {"error": "Cross-site requests are forbidden"})
            return False
        if authenticated and not secrets.compare_digest(
            self.headers.get("X-Task-Token", "").encode(), self.server.token.encode()
        ):
            self.reply(401, {"error": "Open the private launch link to connect"})
            return False
        return True

    def do_GET(self):
        if not self.allowed(self.path.startswith("/api/")):
            return
        if self.path == "/api/ping":
            self.reply(200, {"service": "task-mcp-viewer"})
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        # Location URLs get the same page as "/" under the same checks; the page's API
        # calls still need the token header.
        page = assets["/"] if APP_PATH.fullmatch(self.path) else None
        if self.path not in assets and not page:
            self.reply(404, {"error": "Not found"})
            return
        name, kind = assets.get(self.path, page)
        self.reply(200, self.server.assets[name], kind)

    def do_POST(self):
        if not self.allowed(True):
            return
        if self.headers.get("Origin") != self.origin:
            self.reply(403, {"error": "Same-origin confirmation required"})
            return
        if self.headers.get("Content-Type") != "application/json":
            self.reply(415, {"error": "JSON required"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                raise ValueError("Request too large or empty")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            action = self.path.removeprefix("/api/")
            if self.path == "/api/stop":
                self.reply(200, {"stopped": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            result = dispatch(self.server.store, action, data)
            self.reply(200, result)
        except TaskError as exc:
            self.reply(409 if "revision_conflict" in str(exc) else 400, {"error": str(exc)})
        except (ValueError, TypeError):
            self.reply(400, {"error": "Invalid request fields"})
        except Exception:
            self.reply(500, {"error": "Local service error. Your draft has been preserved."})


def _paths(database):
    database = database.expanduser().resolve()
    return database, Path(str(database) + ".viewer.json")


def _private_file(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "r+")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Viewer redirect refused", headers, fp)


def _control_open(request, timeout):
    # Control credentials must never reach an environment-configured proxy or
    # a redirect destination, even when localhost is missing from NO_PROXY.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)


def _live(state):
    try:
        info = json.loads(state.read_text())
        parsed = urlsplit(info["origin"])
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            return None
        request = urllib.request.Request(
            info["origin"] + "/api/ping", headers={"X-Task-Token": info["token"]}
        )
        with _control_open(request, timeout=0.5) as response:
            if json.load(response).get("service") == "task-mcp-viewer":
                return info
    except (OSError, ValueError, KeyError):
        pass
    return None


def launch_viewer(database):
    """Serialize launches across CLI/MCP processes and reuse the live database instance."""
    database, state = _paths(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    with _private_file(Path(str(state) + ".launch")) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        info = _live(state)
        reused = bool(info)
        if not info:
            subprocess.Popen(
                [sys.executable, "-m", "task_mcp.viewer", "--serve", "--db", str(database)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(100):
                time.sleep(0.05)
                info = _live(state)
                if info:
                    break
        if not info:
            raise TaskError("viewer_start_failed: local listener could not start")
        return {
            "url": info["origin"] + "/#" + info["token"],
            "reused": reused,
            "changed": not reused,
            "lifecycle": "Runs until Stop viewer or task-mcp ui --stop; no automatic startup.",
        }


def stop_viewer(database):
    _, state = _paths(database)
    if not state.parent.exists():
        return False
    with _private_file(Path(str(state) + ".launch")) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        info = _live(state)
        if not info:
            return False
        request = urllib.request.Request(
            info["origin"] + "/api/stop",
            data=b"{}",
            headers={
                "X-Task-Token": info["token"],
                "Origin": info["origin"],
                "Content-Type": "application/json",
            },
        )
        with _control_open(request, timeout=2):
            pass
        for _ in range(100):
            if not state.exists():
                return True
            time.sleep(0.02)
        raise TaskError("viewer_stop_pending: shutdown requested; retry after it finishes")


def serve(database):
    database, state = _paths(database)
    with _private_file(Path(str(state) + ".lock")) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        server = ViewerServer(Store(database, actor="local-browser-human"))
        try:
            with _private_file(state) as handle:
                handle.seek(0)
                json.dump({"origin": server.origin, "token": server.token}, handle)
                handle.truncate()
            server.serve_forever(poll_interval=0.1)
        finally:
            server.server_close()
            state.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=default_database())
    parser.add_argument("--serve", action="store_true", required=True)
    serve(parser.parse_args().db)
