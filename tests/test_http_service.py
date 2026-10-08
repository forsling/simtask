import concurrent.futures

import pytest
from starlette.testclient import TestClient

from task_mcp.http_service import create_app, persistent_token
from task_mcp.store import Store


@pytest.fixture
def endpoint(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    app = create_app(db, "https://workstation.example:8789", tracing=False)
    headers = {
        "Authorization": "Bearer " + persistent_token(db),
        "Host": "workstation.example:8789",
        "Accept": "application/json, text/event-stream",
    }
    return db, app, headers


def initialize(client, name, headers):
    return client.post(
        "/" + name + "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )


@pytest.mark.parametrize("name", ["codex", "claude"])
def test_authenticated_session_survives_service_restart(endpoint, name):
    db, app, headers = endpoint
    for current in (app, create_app(db, "https://workstation.example:8789", tracing=False)):
        with TestClient(current) as client:
            response = initialize(client, name, headers)
            assert response.status_code == 200
            assert response.json()["result"]["serverInfo"]["name"] == "task-mcp"
            response = client.post(
                "/" + name + "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                },
            )
            assert response.status_code == 200
            assert "init" in {t["name"] for t in response.json()["result"]["tools"]}
    assert persistent_token(db) == headers["Authorization"][7:]


@pytest.mark.parametrize("credential", [None, "Bearer wrong", "Basic secret"])
def test_authentication_is_required(endpoint, credential):
    _, app, headers = endpoint
    headers.pop("Authorization")
    if credential is not None:
        headers["Authorization"] = credential
    with TestClient(app) as client:
        for name in ("codex", "claude"):
            assert initialize(client, name, headers).status_code == 401


def test_untrusted_host_and_origin_rejected(endpoint):
    _, app, headers = endpoint
    with TestClient(app) as client:
        assert initialize(client, "codex", {**headers, "Host": "evil.example"}).status_code == 421
        assert (
            initialize(
                client,
                "claude",
                {
                    **headers,
                    "Origin": "https://evil.example",
                },
            ).status_code
            == 403
        )


def test_token_private_stable_and_concurrent(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(persistent_token, [db] * 8))
    assert len(set(tokens)) == 1
    path = tmp_path / "tasks.sqlite3.mcp.token"
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    assert persistent_token(db) == tokens[0]
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("corrupt")
    with pytest.raises(ValueError, match="refusing to rotate"):
        persistent_token(db)
    assert path.read_text() == "corrupt"


def test_token_rejects_symlinks(tmp_path):
    db = tmp_path / "tasks.sqlite3"
    target = tmp_path / "other"
    target.write_text("do not overwrite")
    (tmp_path / "tasks.sqlite3.mcp.token").symlink_to(target)
    with pytest.raises(OSError):
        persistent_token(db)
    assert target.read_text() == "do not overwrite"


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost",
        "https://user:pass@host",
        "https://host/path",
        "https://host#token",
        "https://host:bad",
    ],
)
def test_invalid_public_origin_rejected(tmp_path, origin):
    with pytest.raises(ValueError):
        create_app(tmp_path / "tasks.sqlite3", origin)


@pytest.mark.parametrize("name,actor", [("codex", "codex-coordinator"), ("claude", "claude-code")])
def test_client_audit_actor(endpoint, name, actor):
    import sqlite3

    db, app, headers = endpoint
    project = Store(db).init(
        str(db.parent / "repo"), "main", action="create_project", confirmed=True
    )["project"]["id"]
    with TestClient(app) as client:
        response = client.post(
            "/" + name + "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_note",
                    "arguments": {
                        "title": "actor test",
                        "text": "test",
                        "references": [{"kind": "project", "id": project}],
                    },
                },
            },
        )
        assert response.status_code == 200
        assert not response.json()["result"].get("isError")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT actor FROM events WHERE action='note.created'").fetchone() == (
            actor,
        )


def test_traces_remain_active_between_http_requests(tmp_path):
    import json

    db = tmp_path / "tasks.sqlite3"
    app = create_app(db, "https://workstation.example:8789")
    headers = {
        "Authorization": "Bearer " + persistent_token(db),
        "Host": "workstation.example:8789",
        "Accept": "application/json, text/event-stream",
    }
    with TestClient(app) as client:
        for number in range(4):
            response = client.post(
                "/codex/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": number,
                    "method": "tools/list",
                },
            )
            assert response.status_code == 200
    records = [
        json.loads(line)
        for path in (tmp_path / "tasks.sqlite3.traces").glob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    requests = [r for r in records if r["event"] == "request_finish"]
    assert len(requests) == 4


def test_cli_binds_loopback_and_bounds_shutdown(tmp_path, monkeypatch):
    import sys

    from task_mcp import http_service

    captured = {}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "http_service",
            "--db",
            str(tmp_path / "tasks.sqlite3"),
            "--public-origin",
            "https://workstation.example:8789",
        ],
    )
    monkeypatch.setattr(http_service.uvicorn, "run", lambda app, **kw: captured.update(kw))
    http_service.main()
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8789
    assert captured["timeout_graceful_shutdown"] == 5
