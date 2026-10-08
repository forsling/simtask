"""run.sh: --remote through a stand-in ssh (tests/fake_ssh.py), --dev in a stand-in
checkout whose "live" database sits under a temporary XDG_DATA_HOME, --tailscale
through a stand-in tailscale (tests/fake_tailscale.py) and the viewer services
through stand-in systemctl and loginctl (tests/fake_systemctl.py,
tests/fake_loginctl.py) with the units under a temporary XDG_CONFIG_HOME."""

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import task_mcp.store
from task_mcp.store import Store

REPO = Path(__file__).resolve().parents[1]
LINK = re.compile(r"http://127\.0\.0\.1:(\d+)/#([A-Za-z0-9_-]+)")
TAILNET_LINK = re.compile(r"(https?)://(fake-host\.tail\.ts\.net):(\d+)/#([A-Za-z0-9_-]+)")


@pytest.fixture
def remote(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy(REPO / "tests/fake_ssh.py", bin_dir / "ssh")
    (bin_dir / "ssh").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "TASK_MCP_DB"}
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        XDG_RUNTIME_DIR=str(tmp_path / "run"),
        FAKE_SSH_LOG=str(tmp_path / "ssh.log"),
        FAKE_REMOTE_DB=str(tmp_path / "remote.sqlite3"),
        FAKE_LOCAL_REPO=str(REPO),
        TASK_MCP_REMOTE_HOST="example-host",
    )
    yield env, tmp_path
    pid = tmp_path / "run/task-mcp/remote-example-host.sock.pid"
    if pid.exists():
        os.kill(int(pid.read_text()), signal.SIGKILL)
    if Path(env["FAKE_REMOTE_DB"] + ".viewer.json").exists():
        subprocess.run(
            [REPO / ".venv/bin/task-mcp", "ui", "--stop", "--db", env["FAKE_REMOTE_DB"]],
            capture_output=True,
        )


def run(env, *args):
    return subprocess.run(
        ["bash", str(REPO / "run.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def link(result):
    assert result.returncode == 0, result.stdout + result.stderr
    port, token = LINK.search(result.stdout).groups()
    return int(port), token


def remote_viewer(env):
    return json.loads(Path(env["FAKE_REMOTE_DB"] + ".viewer.json").read_text())


def ping(port, token):
    origin = f"http://127.0.0.1:{port}"
    request = urllib.request.Request(
        origin + "/api/projects",
        data=b"{}",
        headers={"X-Task-Token": token, "Origin": origin, "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return None


def alive(port, token):
    """A read-only probe: /api/ping records no event, unlike the calls ping() makes."""
    origin = f"http://127.0.0.1:{port}"
    request = urllib.request.Request(origin + "/api/ping", headers={"X-Task-Token": token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as response:
            return json.load(response).get("service") == "task-mcp-viewer"
    except OSError:
        return False


def tunnels(tmp_path):
    lines = (tmp_path / "ssh.log").read_text().splitlines()
    return [entry for entry in map(json.loads, lines) if "-M" in entry["opts"]]


def test_remote_viewer_through_tunnel(remote):
    env, tmp_path = remote
    port, token = link(run(env, "--remote"))
    viewer = remote_viewer(env)
    remote_port = int(viewer["origin"].rsplit(":", 1)[1])
    # The token reaches the laptop link only; the tunnel binds loopback on both ends.
    assert token == viewer["token"] and port != remote_port
    assert ping(port, token) == 200
    assert tunnels(tmp_path)[0]["opts"]["-L"] == [f"127.0.0.1:{port}:127.0.0.1:{remote_port}"]
    assert tunnels(tmp_path)[0]["host"] == "example-host"

    # A live tunnel and viewer are reused.
    assert link(run(env, "--remote")) == (port, token)
    assert len(tunnels(tmp_path)) == 1

    # A dead tunnel that left its control socket behind is replaced.
    socket = tmp_path / "run/task-mcp/remote-example-host.sock"
    os.kill(int(Path(f"{socket}.pid").read_text()), signal.SIGKILL)
    assert ping(port, token) is None
    port, token = link(run(env, "--remote"))
    assert ping(port, token) == 200 and len(tunnels(tmp_path)) == 2

    # --restart rotates the remote viewer's token and follows its new port.
    new_port, new_token = link(run(env, "--remote", "--restart"))
    assert new_token != token and new_token == remote_viewer(env)["token"]
    assert ping(new_port, new_token) == 200 and ping(port, token) is None

    stopped = run(env, "--remote", "--stop")
    assert stopped.returncode == 0, stopped.stderr
    assert "Tunnel to example-host closed." in stopped.stdout
    assert "Viewer stopped." in stopped.stdout
    assert ping(new_port, new_token) is None
    assert not Path(env["FAKE_REMOTE_DB"] + ".viewer.json").exists()
    assert not socket.exists()


def test_remote_errors(remote):
    env, tmp_path = remote
    result = run({**env, "FAKE_SSH_UNREACHABLE": "1"}, "--remote", "other-host")
    assert result.returncode == 255
    assert "Cannot reach other-host over SSH" in result.stderr
    assert "Could not resolve hostname other-host" in result.stderr

    (tmp_path / "empty").mkdir()
    result = run({**env, "FAKE_REMOTE_REPO": str(tmp_path / "empty")}, "--remote")
    assert result.returncode == 97
    expected = "task-mcp is not installed on example-host: expected a checkout with .venv at "
    assert expected + str(REPO) in result.stderr
    assert "Task MCP viewer" not in result.stdout and not tunnels_or_empty(tmp_path)

    no_host = {k: v for k, v in env.items() if k != "TASK_MCP_REMOTE_HOST"}
    result = run(no_host, "--remote")
    assert result.returncode == 2 and "set TASK_MCP_REMOTE_HOST" in result.stderr

    result = run({**env, "TASK_MCP_DB": str(tmp_path / "local.sqlite3")}, "--remote")
    assert result.returncode == 2 and "TASK_MCP_DB selects a local database" in result.stderr
    assert not (tmp_path / "local.sqlite3").exists()


def tunnels_or_empty(tmp_path):
    return tunnels(tmp_path) if (tmp_path / "ssh.log").exists() else []


@pytest.mark.parametrize(
    "args",
    [
        ["extra"],
        ["--stop", "--restart"],
        ["--restart", "--restart"],
        ["--remote", "a", "b"],
        ["--remote", "--remote"],
        ["--remote", "--bogus"],
        ["--dev", "--remote"],
        ["--remote", "--dev"],
        ["--dev", "--dev"],
        ["--keep"],
        ["--dev", "--keep", "--keep"],
        ["--dev", "--keep", "--stop"],
        ["--tailscale", "--remote"],
        ["--remote", "--tailscale"],
        ["--tailscale", "--remote", "a"],
        ["--tailscale", "--tailscale"],
        ["--tailscale", "--keep"],
        ["--install-service"],
        ["--remove-service"],
        ["--dev", "--install-service"],
        ["--remote", "--install-service"],
        ["--tailscale", "--install-service", "--remove-service"],
        ["--tailscale", "--install-service", "--install-service"],
        ["--tailscale", "--install-service", "--stop"],
        ["--tailscale", "--restart", "--install-service"],
        ["--tailscale", "--remove-service", "--restart"],
        ["--dev", "--tailscale", "--keep", "--remove-service"],
    ],
)
def test_argument_checks(remote, args):
    env, _ = remote
    result = run(env, *args)
    assert result.returncode == 2 and result.stderr.startswith("Usage: ./run.sh")


def test_local_viewer_unchanged(tmp_path):
    env = {**os.environ, "TASK_MCP_DB": "local.sqlite3"}
    started = subprocess.run(
        ["bash", str(REPO / "run.sh")], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    try:
        port, token = link(started)
        assert started.stdout.startswith("Task MCP viewer: http://127.0.0.1:")
        assert (tmp_path / "local.sqlite3.viewer.json").exists()
        assert ping(port, token) == 200
    finally:
        stopped = subprocess.run(
            ["bash", str(REPO / "run.sh"), "--stop"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
    assert stopped.stdout == "Viewer stopped.\n"


@pytest.fixture
def dev(tmp_path):
    """A stand-in dev checkout: run.sh beside a .venv link to the real one, and a seeded
    "live" database where the server's default path resolves under XDG_DATA_HOME."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copy(REPO / "run.sh", checkout / "run.sh")
    (checkout / ".venv").symlink_to(REPO / ".venv")
    live = tmp_path / "data/task-mcp/tasks.sqlite3"
    Store(live).init(
        str(tmp_path / "repo"),
        "main",
        workstream_name="live",
        action="create_project",
        confirmed=True,
    )
    env = {k: v for k, v in os.environ.items() if k != "TASK_MCP_DB"}
    env["XDG_DATA_HOME"] = str(tmp_path / "data")
    yield env, checkout, live
    for database in (live, checkout / ".dev/tasks.sqlite3"):
        if Path(str(database) + ".viewer.json").exists():
            subprocess.run(
                [REPO / ".venv/bin/task-mcp", "ui", "--stop", "--db", database],
                capture_output=True,
            )


def run_dev(env, checkout, *args):
    return subprocess.run(
        ["bash", str(checkout / "run.sh"), "--dev", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def user_version(database):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def workstreams(database):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        return [row[0] for row in db.execute("SELECT name FROM workstreams ORDER BY rowid")]


def contents(database):
    """Schema version and every row, read-only: the live file's bytes change with WAL
    checkpoints, so this is the view the live viewer and servers must keep seeing."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        return db.execute("PRAGMA user_version").fetchone()[0], list(db.iterdump())


def mcp_entry(stdout):
    return json.loads("{" + stdout[stdout.index('"tasks-dev"') :] + "}")["tasks-dev"]


def test_dev_viewer_on_a_copy(dev, monkeypatch, tmp_path):
    env, checkout, live = dev
    copy = checkout / ".dev/tasks.sqlite3"
    live_state = Path(str(live) + ".viewer.json")
    live_port, live_token = link(
        subprocess.run(
            [REPO / ".venv/bin/task-mcp", "ui", "--db", live], capture_output=True, text=True
        )
    )
    state_before = (live_state.read_bytes(), live_state.stat().st_mtime_ns)
    live_before = contents(live)

    result = run_dev(env, checkout)
    port, token = link(result)
    assert result.stdout.startswith("Task MCP dev viewer: http://127.0.0.1:")
    assert f"Dev database: {copy} (fresh copy of {live})" in result.stdout
    assert "Stop it with ./run.sh --dev --stop." in result.stdout
    assert "./run.sh --stop." not in result.stdout
    assert mcp_entry(result.stdout) == {
        "type": "stdio",
        "command": f"{checkout}/.venv/bin/task-mcp",
        "env": {"TASK_MCP_DB": str(copy)},
    }
    # The dev viewer has its own port and token, on the copy, beside the live viewer.
    assert (port, token) != (live_port, live_token)
    assert ping(port, token) == 200 and alive(live_port, live_token)
    assert json.loads(Path(str(copy) + ".viewer.json").read_text())["token"] == token
    assert copy.stat().st_mode & 0o777 == 0o600
    assert user_version(copy) == user_version(live) and workstreams(copy) == ["live"]

    # A server with a newer schema migrates the copy only (the dev viewer, started
    # earlier, keeps serving it).
    monkeypatch.setattr(task_mcp.store, "DATABASE_SCHEMA_REVISION", user_version(live) + 1)
    Store(copy)
    assert user_version(copy) == user_version(live) + 1
    assert contents(live) == live_before

    # A running dev viewer and its copy are reused; --keep says so explicitly.
    again = run_dev(env, checkout)
    assert link(again) == (port, token) and "(kept while the dev viewer runs;" in again.stdout
    kept = run_dev(env, checkout, "--keep")
    assert link(kept) == (port, token) and f"Dev database: {copy} (kept)" in kept.stdout
    assert user_version(copy) == user_version(live) + 1

    # --restart stops the dev viewer, takes a fresh copy and starts a new viewer;
    # --keep --restart restarts on the existing copy.
    old = (port, token)
    port, token = link(run_dev(env, checkout, "--restart"))
    assert (port, token) != old and ping(*old) is None and ping(port, token) == 200
    assert user_version(copy) == user_version(live) and workstreams(copy) == ["live"]
    monkeypatch.undo()
    Store(copy).init(
        str(tmp_path / "other"),
        "main",
        workstream_name="dev-only",
        action="create_project",
        confirmed=True,
    )
    restarted = run_dev(env, checkout, "--keep", "--restart")
    new_port, new_token = link(restarted)
    assert new_token != token and ping(port, token) is None
    assert f"Dev database: {copy} (kept)" in restarted.stdout
    assert workstreams(copy) == ["live", "dev-only"] and workstreams(live) == ["live"]

    # --stop stops the dev viewer only and leaves the copy.
    stopped = run_dev(env, checkout, "--stop")
    assert stopped.returncode == 0, stopped.stderr
    assert stopped.stdout == f"Viewer stopped.\n(The dev copy stays at {copy}.)\n"
    assert ping(new_port, new_token) is None and alive(live_port, live_token)
    assert copy.exists() and not Path(str(copy) + ".viewer.json").exists()
    assert (live_state.read_bytes(), live_state.stat().st_mtime_ns) == state_before
    assert contents(live) == live_before
    assert run_dev(env, checkout, "--stop").stdout.startswith("Viewer is not running.\n")

    copy.unlink()
    missing = run_dev(env, checkout, "--keep")
    assert missing.returncode == 1 and f"No dev copy at {copy};" in missing.stderr


def viewer_pid(database):
    """The process serving the viewer on database, found through /proc: the state
    file records its origin and token, not its pid."""
    tail = ["-m", "task_mcp.viewer", "--serve", "--db", str(database)]
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().decode().split("\0")
        except OSError:
            continue
        if argv[-6:-1] == tail:
            return int(proc.name)
    raise LookupError(f"no viewer is serving {database}")


def wait_dead(port, token):
    for _ in range(100):
        if not alive(port, token):
            return
        time.sleep(0.05)


def test_dev_replaces_a_killed_viewer(dev, tmp_path):
    """A dev viewer that dies without a clean stop (a reboot, a logout, SIGKILL) leaves
    its state file behind; plain --dev must not take the file for a running viewer."""
    env, checkout, live = dev
    copy = checkout / ".dev/tasks.sqlite3"
    state = Path(str(copy) + ".viewer.json")
    port, token = link(run_dev(env, checkout))
    Store(copy).init(
        str(tmp_path / "other"),
        "main",
        workstream_name="dev-only",
        action="create_project",
        confirmed=True,
    )
    os.kill(viewer_pid(copy), signal.SIGKILL)
    wait_dead(port, token)
    assert not alive(port, token) and state.exists()

    result = run_dev(env, checkout)
    new_port, new_token = link(result)
    assert f"Dev database: {copy} (fresh copy of {live})" in result.stdout
    assert "kept" not in result.stdout
    assert (new_port, new_token) != (port, token) and ping(new_port, new_token) == 200
    assert json.loads(state.read_text())["token"] == new_token
    assert workstreams(copy) == ["live"] and workstreams(live) == ["live"]


def test_sigterm_stops_the_viewer_cleanly(dev):
    """systemctl stop (and a logout) send SIGTERM: the viewer ends like /api/stop and
    takes its state file with it."""
    env, checkout, live = dev
    copy = checkout / ".dev/tasks.sqlite3"
    state = Path(str(copy) + ".viewer.json")
    port, token = link(run_dev(env, checkout))
    pid = viewer_pid(copy)
    os.kill(pid, signal.SIGTERM)
    wait_dead(port, token)
    assert not alive(port, token) and not state.exists()
    for _ in range(100):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.05)
    assert not Path(f"/proc/{pid}").exists()
    assert run_dev(env, checkout, "--stop").stdout.startswith("Viewer is not running.\n")


def test_dev_creates_venv(dev, tmp_path):
    env, checkout, live = dev
    (checkout / ".venv").unlink()
    log = tmp_path / "python.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A stand-in python3: "-m venv DIR" installs a stub venv whose python logs its
    # arguments, answers pip by linking the real task-mcp and otherwise runs the real
    # interpreter. Anything else goes straight to the real interpreter.
    real = REPO / ".venv/bin/python"
    stub = tmp_path / "stub-python"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> {log}\n'
        'case "$*" in\n'
        f'  "-m pip "*) ln -s {real.parent}/task-mcp "$(dirname "$0")/task-mcp" ;;\n'
        f'  *) exec {real} "$@" ;;\n'
        "esac\n"
    )
    (bin_dir / "python3").write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1 $2" = "-m venv" ]; then\n'
        f'  echo "venv $3 in $PWD" >> {log}\n'
        '  mkdir -p "$3/bin"\n'
        f'  cp {stub} "$3/bin/python" && chmod +x "$3/bin/python"\n'
        "  exit\n"
        "fi\n"
        f'exec {real} "$@"\n'
    )
    (bin_dir / "python3").chmod(0o755)
    env = {**env, "PATH": f"{bin_dir}:{env['PATH']}"}

    result = run_dev(env, checkout)
    port, token = link(result)
    assert f"No .venv in {checkout}; creating one" in result.stderr
    lines = log.read_text().splitlines()
    assert lines[0] == f"venv .venv in {checkout}"
    assert lines[1] == "-m pip install -q --disable-pip-version-check -e .[dev]"
    # The viewer probe and the copy run through the new venv.
    assert lines[2].startswith("-c import sys;") and lines[2].endswith(".viewer.json")
    assert lines[3] == f"- {live} {checkout}/.dev/tasks.sqlite3"
    assert mcp_entry(result.stdout)["command"] == f"{checkout}/.venv/bin/task-mcp"
    assert ping(port, token) == 200 and user_version(checkout / ".dev/tasks.sqlite3") > 0

    # With the venv in place the install does not run again.
    stopped = run_dev(env, checkout, "--stop")
    assert stopped.returncode == 0 and log.read_text().splitlines() == lines


def test_dev_refuses_task_mcp_db(dev):
    env, checkout, live = dev
    result = run_dev({**env, "TASK_MCP_DB": str(checkout / "other.sqlite3")}, checkout)
    assert result.returncode == 2 and "unset TASK_MCP_DB" in result.stderr
    assert not (checkout / ".dev").exists() and not (checkout / "other.sqlite3").exists()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def tailnet(tmp_path):
    """A stand-in tailscale on PATH, a disposable database through TASK_MCP_DB and a
    free fixed port, so the live viewer and the machine's real tailscale are untouched."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy(REPO / "tests/fake_tailscale.py", bin_dir / "tailscale")
    (bin_dir / "tailscale").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "TASK_MCP_DB"}
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        FAKE_TAILSCALE_LOG=str(tmp_path / "tailscale.log"),
        FAKE_TAILSCALE_STATE=str(tmp_path / "serve.json"),
        TASK_MCP_DB=str(tmp_path / "local.sqlite3"),
        TASK_MCP_TAILSCALE_PORT=str(free_port()),
    )
    yield env, tmp_path
    if Path(env["TASK_MCP_DB"] + ".viewer.json").exists():
        subprocess.run(
            [REPO / ".venv/bin/task-mcp", "ui", "--stop", "--db", env["TASK_MCP_DB"]],
            capture_output=True,
        )


def tailnet_link(result):
    assert result.returncode == 0, result.stdout + result.stderr
    scheme, host, port, token = TAILNET_LINK.search(result.stdout).groups()
    return scheme, host, int(port), token


def tailscale_calls(tmp_path):
    log = tmp_path / "tailscale.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def serve_config(tmp_path):
    state = tmp_path / "serve.json"
    return json.loads(state.read_text()) if state.exists() else {}


def through_tailnet(port, token, public_origin):
    """What the viewer sees from a browser on the tailnet: tailscale serve forwards the
    request to loopback with the browser's Host header (the MagicDNS name and port)
    and Origin unchanged."""
    host = public_origin.split("://", 1)[1]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/projects",
        data=b"{}",
        headers={
            "Host": host,
            "Origin": public_origin,
            "X-Task-Token": token,
            "Content-Type": "application/json",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return None


def test_tailscale_publishes_the_viewer_on_a_fixed_port(tailnet):
    env, tmp_path = tailnet
    port = int(env["TASK_MCP_TAILSCALE_PORT"])
    public = f"http://fake-host.tail.ts.net:{port}"
    mapping = {
        "TCP": {str(port): {"HTTP": True}},
        "Web": {
            f"fake-host.tail.ts.net:{port}": {
                "Handlers": {"/": {"Proxy": f"http://127.0.0.1:{port}"}}
            }
        },
    }

    result = run(env, "--tailscale")
    assert tailnet_link(result)[:3] == ("http", "fake-host.tail.ts.net", port)
    token = tailnet_link(result)[3]
    assert result.stdout.startswith(f"Task MCP viewer: {public}/#")
    assert f"(http on port {port}, mapping added)" in result.stdout
    assert "Stop it with ./run.sh --tailscale --stop." in result.stdout
    state = json.loads(Path(env["TASK_MCP_DB"] + ".viewer.json").read_text())
    assert state == {"origin": f"http://127.0.0.1:{port}", "token": token, "public_origin": public}
    assert serve_config(tmp_path) == mapping
    assert tailscale_calls(tmp_path) == [
        ["status", "--json"],
        ["serve", "status", "--json"],
        ["serve", "--bg", f"--http={port}", f"http://127.0.0.1:{port}"],
    ]
    # Requests as the proxy forwards them from a tailnet browser pass; the viewer
    # still binds loopback only, still needs the token and refuses other names.
    assert through_tailnet(port, token, public) == 200
    assert ping(port, token) == 200
    assert through_tailnet(port, "wrong", public) == 401
    assert through_tailnet(port, token, f"http://other.tail.ts.net:{port}") == 403
    assert through_tailnet(port, token, f"https://fake-host.tail.ts.net:{port}") == 403

    # A second run reuses the viewer and the mapping; a plain run reuses the viewer
    # too and prints the same tailnet link.
    again = run(env, "--tailscale")
    assert tailnet_link(again)[3] == token and "mapping reused)" in again.stdout
    assert tailscale_calls(tmp_path)[3:] == [["status", "--json"], ["serve", "status", "--json"]]
    plain = run(env)
    assert tailnet_link(plain)[3] == token and "tailscale serve" not in plain.stdout

    # --restart rotates the token on the same port and keeps the mapping. With
    # certificates on the tailnet the link and the mapping become https.
    restarted = run({**env, "FAKE_TAILSCALE_CERTS": "1"}, "--tailscale", "--restart")
    scheme, _, new_port, new_token = tailnet_link(restarted)
    assert (scheme, new_port) == ("https", port) and new_token != token
    assert f"(https on port {port}, mapping added)" in restarted.stdout
    assert through_tailnet(port, new_token, f"https://fake-host.tail.ts.net:{port}") == 200
    assert through_tailnet(port, new_token, public) == 403
    assert serve_config(tmp_path)["TCP"] == {str(port): {"HTTPS": True}}
    assert tailscale_calls(tmp_path)[-1] == [
        "serve",
        "--bg",
        f"--https={port}",
        f"http://127.0.0.1:{port}",
    ]

    # --stop removes the mapping in the form it was made and stops the viewer.
    stopped = run(env, "--tailscale", "--stop")
    assert stopped.returncode == 0, stopped.stderr
    assert stopped.stdout == f"Tailnet mapping for port {port} removed.\nViewer stopped.\n"
    assert tailscale_calls(tmp_path)[-2:] == [
        ["serve", "status", "--json"],
        ["serve", f"--https={port}", "off"],
    ]
    assert serve_config(tmp_path) == {} and ping(port, new_token) is None
    assert not Path(env["TASK_MCP_DB"] + ".viewer.json").exists()
    assert run(env, "--tailscale", "--stop").stdout == "Viewer is not running.\n"
    assert not any(call[0] == "funnel" for call in tailscale_calls(tmp_path))


def test_tailscale_errors(tailnet):
    env, tmp_path = tailnet
    port = env["TASK_MCP_TAILSCALE_PORT"]
    down = run({**env, "FAKE_TAILSCALE_DOWN": "1"}, "--tailscale")
    assert down.returncode == 1 and "tailscale status --json failed" in down.stderr
    assert "doesn't appear to be running" in down.stderr
    assert not Path(env["TASK_MCP_DB"] + ".viewer.json").exists()

    denied = run({**env, "FAKE_TAILSCALE_DENIED": "1"}, "--tailscale")
    assert denied.returncode == 1 and "Access denied" in denied.stderr
    assert "sudo tailscale set --operator=$USER" in denied.stderr
    assert f"the viewer runs on 127.0.0.1:{port} but is not published" in denied.stderr
    assert serve_config(tmp_path) == {}
    # The viewer it started is reused once serve works, and stopped as usual.
    assert "mapping added)" in run(env, "--tailscale").stdout
    assert run(env, "--tailscale", "--stop").returncode == 0

    bad = run({**env, "TASK_MCP_TAILSCALE_PORT": "80a"}, "--tailscale")
    assert bad.returncode == 2 and "must be port numbers" in bad.stderr
    assert tailscale_calls(tmp_path)[-1] == ["serve", f"--http={port}", "off"]


def test_dev_tailscale_uses_its_own_port(dev, tmp_path):
    env, checkout, live = dev
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy(REPO / "tests/fake_tailscale.py", bin_dir / "tailscale")
    (bin_dir / "tailscale").chmod(0o755)
    port = free_port()
    env = {
        **env,
        "PATH": f"{bin_dir}:{env['PATH']}",
        "FAKE_TAILSCALE_LOG": str(tmp_path / "tailscale.log"),
        "FAKE_TAILSCALE_STATE": str(tmp_path / "serve.json"),
        "TASK_MCP_TAILSCALE_PORT": "1",  # the live port is not the dev viewer's
        "TASK_MCP_DEV_TAILSCALE_PORT": str(port),
    }
    copy = checkout / ".dev/tasks.sqlite3"
    public = f"http://fake-host.tail.ts.net:{port}"

    result = run_dev(env, checkout, "--tailscale")
    assert tailnet_link(result)[:3] == ("http", "fake-host.tail.ts.net", port)
    token = tailnet_link(result)[3]
    assert result.stdout.startswith(f"Task MCP dev viewer: {public}/#")
    assert f"Dev database: {copy} (fresh copy of {live})" in result.stdout
    assert f"(http on port {port}, mapping added)" in result.stdout
    assert "Stop it with ./run.sh --dev --tailscale --stop." in result.stdout
    assert mcp_entry(result.stdout)["env"] == {"TASK_MCP_DB": str(copy)}
    assert json.loads(Path(str(copy) + ".viewer.json").read_text())["public_origin"] == public
    assert through_tailnet(port, token, public) == 200
    assert list(serve_config(tmp_path)["TCP"]) == [str(port)]

    kept = run_dev(env, checkout, "--tailscale", "--keep")
    assert tailnet_link(kept)[3] == token and "mapping reused)" in kept.stdout

    stopped = run_dev(env, checkout, "--tailscale", "--stop")
    assert stopped.returncode == 0, stopped.stderr
    assert stopped.stdout == (
        f"Tailnet mapping for port {port} removed.\nViewer stopped.\n"
        f"(The dev copy stays at {copy}.)\n"
    )
    assert serve_config(tmp_path) == {} and copy.exists()
    assert tailscale_calls(tmp_path)[-1] == ["serve", f"--http={port}", "off"]


@pytest.fixture
def service(dev, tmp_path):
    """The stand-in dev checkout with stand-in tailscale, systemctl and loginctl on
    PATH and the units under a temporary XDG_CONFIG_HOME, so the real user manager,
    tailscaled and ~/.config/systemd/user are untouched. The stand-in systemctl runs
    a unit's ExecStart for real (detached) and stops it with SIGTERM."""
    env, checkout, live = dev
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("tailscale", "systemctl", "loginctl"):
        shutil.copy(REPO / f"tests/fake_{name}.py", bin_dir / name)
        (bin_dir / name).chmod(0o755)
    env = {
        **env,
        "PATH": f"{bin_dir}:{env['PATH']}",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "FAKE_TAILSCALE_LOG": str(tmp_path / "tailscale.log"),
        "FAKE_TAILSCALE_STATE": str(tmp_path / "serve.json"),
        "FAKE_SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
        "FAKE_SYSTEMCTL_STATE": str(tmp_path / "systemd"),
        "FAKE_LOGINCTL_LOG": str(tmp_path / "loginctl.log"),
        "FAKE_LOGINCTL_STATE": str(tmp_path / "linger"),
        "TASK_MCP_TAILSCALE_PORT": str(free_port()),
        "TASK_MCP_DEV_TAILSCALE_PORT": str(free_port()),
    }
    yield env, checkout, live
    for pid_file in (tmp_path / "systemd").glob("*.pid"):
        try:
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def systemctl_calls(env):
    log = Path(env["FAKE_SYSTEMCTL_LOG"])
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def loginctl_calls(env):
    log = Path(env["FAKE_LOGINCTL_LOG"])
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def unit_active(env, unit):
    """Whether the stand-in systemctl's process for unit runs, read from its state
    directory so the probe leaves no trace in the systemctl log."""
    try:
        pid = int((Path(env["FAKE_SYSTEMCTL_STATE"]) / f"{unit}.pid").read_text())
    except (OSError, ValueError):
        return False
    return Path(f"/proc/{pid}").exists()


def unit_text(python, database, port):
    return (
        "[Unit]\n"
        f"Description=Task MCP viewer on {database} (tailnet port {port})\n"
        "After=network-online.target tailscaled.service\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={python} -m task_mcp.viewer --serve --db {database} --port {port}"
        f" --public-origin http://fake-host.tail.ts.net:{port}\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def test_install_service_runs_the_live_viewer_as_a_user_unit(service, tmp_path):
    env, checkout, live = service
    port = int(env["TASK_MCP_TAILSCALE_PORT"])
    public = f"http://fake-host.tail.ts.net:{port}"
    unit = "task-mcp-viewer.service"
    unit_file = tmp_path / "config/systemd/user" / unit
    state = Path(str(live) + ".viewer.json")

    def run_checkout(*args):
        return subprocess.run(
            ["bash", str(checkout / "run.sh"), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    # A detached live viewer is running, as today; the service takes over from it.
    detached_port, detached_token = link(
        subprocess.run(
            [REPO / ".venv/bin/task-mcp", "ui", "--db", live], capture_output=True, text=True
        )
    )
    result = run_checkout("--tailscale", "--install-service")
    scheme, host, link_port, token = tailnet_link(result)
    assert (scheme, link_port) == ("http", port) and token != detached_token
    assert result.stdout.startswith(f"Linger enabled for {os.environ['USER']}:")
    assert f"Task MCP viewer: {public}/#{token}" in result.stdout
    assert f"(http on port {port}, mapping added)" in result.stdout
    assert f"Runs as the systemd user service {unit} (installed as {unit_file})." in result.stdout
    assert "Stop it with ./run.sh --tailscale --stop." in result.stdout
    # The unit runs this checkout's venv on the live database, the fixed port and the
    # tailnet origin, in the foreground.
    assert unit_file.read_text() == unit_text(checkout / ".venv/bin/python", live, port)
    assert systemctl_calls(env) == [
        ["--user", "daemon-reload"],
        ["--user", "is-active", "--quiet", unit],
        ["--user", "enable", "--quiet", unit],
        ["--user", "restart", unit],
    ]
    assert loginctl_calls(env) == [
        ["show-user", os.environ["USER"], "-p", "Linger", "--value"],
        ["enable-linger", os.environ["USER"]],
    ]
    assert Path(env["FAKE_LOGINCTL_STATE"]).read_text() == "yes"
    assert (tmp_path / "systemd" / f"{unit}.enabled").exists() and unit_active(env, unit)
    assert ping(detached_port, detached_token) is None
    assert json.loads(state.read_text()) == {
        "origin": f"http://127.0.0.1:{port}",
        "token": token,
        "public_origin": public,
    }
    assert through_tailnet(port, token, public) == 200
    assert list(serve_config(tmp_path)["TCP"]) == [str(port)]

    # Runs with and without --tailscale reuse the service and print its link.
    again = run_checkout("--tailscale")
    assert tailnet_link(again)[3] == token
    assert "mapping reused)" in again.stdout and f"service {unit} (reused)." in again.stdout
    plain = run_checkout()
    assert tailnet_link(plain)[3] == token and "tailscale serve" not in plain.stdout
    assert f"service {unit} (reused)." in plain.stdout
    assert "Stop it with ./run.sh --stop." in plain.stdout
    assert systemctl_calls(env)[4:] == [["--user", "is-active", "--quiet", unit]] * 2

    # The token rotates when systemd restarts the unit (a reboot); the link is
    # printable without restarting anything.
    subprocess.run(["systemctl", "--user", "restart", unit], env=env, check=True)
    rotated = tailnet_link(run_checkout("--tailscale"))[3]
    assert rotated != token and json.loads(state.read_text())["token"] == rotated
    assert systemctl_calls(env)[6:] == [
        ["--user", "restart", unit],
        ["--user", "is-active", "--quiet", unit],
    ]

    # --restart restarts the service; a second install keeps linger as it is.
    restarted = run_checkout("--tailscale", "--restart")
    new_token = tailnet_link(restarted)[3]
    assert new_token != rotated and f"service {unit} (restarted)." in restarted.stdout
    assert systemctl_calls(env)[-1] == ["--user", "restart", unit]
    assert through_tailnet(port, new_token, public) == 200
    reinstalled = run_checkout("--tailscale", "--install-service")
    assert tailnet_link(reinstalled)[3] != new_token and "Linger" not in reinstalled.stdout
    assert loginctl_calls(env)[-1] == ["show-user", os.environ["USER"], "-p", "Linger", "--value"]
    assert unit_file.read_text() == unit_text(checkout / ".venv/bin/python", live, port)

    # --stop stops the service and removes the mapping; the unit stays installed.
    stopped = run_checkout("--tailscale", "--stop")
    assert stopped.returncode == 0, stopped.stderr
    assert stopped.stdout == (
        f"Tailnet mapping for port {port} removed.\nViewer service stopped.\n"
        f"({unit} stays installed; ./run.sh --tailscale starts it, --remove-service removes it.)\n"
    )
    assert systemctl_calls(env)[-1] == ["--user", "stop", unit]
    assert unit_file.exists() and not unit_active(env, unit)
    assert serve_config(tmp_path) == {} and not state.exists()
    assert run_checkout("--tailscale", "--stop").stdout.startswith(
        "Viewer service is not running.\n"
    )

    # Starting again goes through the service. A detached viewer that got in first
    # (task-mcp ui on the live database) is replaced, as it holds the viewer lock.
    detached_port, detached_token = link(
        subprocess.run(
            [REPO / ".venv/bin/task-mcp", "ui", "--db", live], capture_output=True, text=True
        )
    )
    started = run_checkout("--tailscale")
    assert tailnet_link(started)[:3] == ("http", "fake-host.tail.ts.net", port)
    assert f"service {unit} (started)." in started.stdout and "mapping added)" in started.stdout
    assert ping(detached_port, detached_token) is None and unit_active(env, unit)
    assert systemctl_calls(env)[-2:] == [
        ["--user", "is-active", "--quiet", unit],
        ["--user", "start", unit],
    ]

    # --remove-service stops, disables and deletes the unit and the mapping.
    removed = run_checkout("--tailscale", "--remove-service")
    assert removed.returncode == 0, removed.stderr
    assert removed.stdout == (
        f"Tailnet mapping for port {port} removed.\nViewer service {unit} removed.\n"
    )
    assert systemctl_calls(env)[-3:] == [
        ["--user", "disable", "--quiet", unit],
        ["--user", "stop", unit],
        ["--user", "daemon-reload"],
    ]
    assert not unit_file.exists() and not (tmp_path / "systemd" / f"{unit}.enabled").exists()
    assert not unit_active(env, unit) and not state.exists() and serve_config(tmp_path) == {}
    assert run_checkout("--tailscale", "--stop").stdout == "Viewer is not running.\n"
    assert run_checkout("--tailscale", "--remove-service").stdout == (
        f"No viewer service is installed ({unit_file}).\n"
    )
    # Without a unit, --tailscale runs a detached viewer as before.
    detached = run_checkout("--tailscale")
    assert "systemd" not in detached.stdout and tailnet_link(detached)[2] == port
    assert systemctl_calls(env)[-1] == ["--user", "daemon-reload"]
    assert run_checkout("--tailscale", "--stop").returncode == 0
    assert not any(call[0] == "funnel" for call in tailscale_calls(tmp_path))


def test_install_service_without_linger(service, tmp_path):
    """A refused enable-linger (polkit) is reported; the service is still installed."""
    env, checkout, live = service
    env = {**env, "FAKE_LOGINCTL_DENIED": "1"}
    result = subprocess.run(
        ["bash", str(checkout / "run.sh"), "--tailscale", "--install-service"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert tailnet_link(result)[2] == int(env["TASK_MCP_TAILSCALE_PORT"])
    assert "Access denied" in result.stderr
    assert f"loginctl enable-linger {os.environ['USER']} failed" in result.stderr
    assert "Linger enabled" not in result.stdout
    assert unit_active(env, "task-mcp-viewer.service")
    assert (tmp_path / "config/systemd/user/task-mcp-viewer.service").exists()

    with_db = subprocess.run(
        ["bash", str(checkout / "run.sh"), "--tailscale", "--install-service"],
        env={**env, "TASK_MCP_DB": str(tmp_path / "other.sqlite3")},
        capture_output=True,
        text=True,
    )
    assert with_db.returncode == 2 and "unset TASK_MCP_DB" in with_db.stderr
    removed = subprocess.run(
        ["bash", str(checkout / "run.sh"), "--tailscale", "--remove-service"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert removed.returncode == 0, removed.stderr


def test_dev_install_service_serves_the_copy(service, tmp_path):
    env, checkout, live = service
    port = int(env["TASK_MCP_DEV_TAILSCALE_PORT"])
    public = f"http://fake-host.tail.ts.net:{port}"
    copy = checkout / ".dev/tasks.sqlite3"
    state = Path(str(copy) + ".viewer.json")
    key = hashlib.sha256(str(checkout).encode()).hexdigest()[:8]
    unit = f"task-mcp-dev-viewer-checkout-{key}.service"
    unit_file = tmp_path / "config/systemd/user" / unit

    result = run_dev(env, checkout, "--tailscale", "--install-service")
    token = tailnet_link(result)[3]
    assert tailnet_link(result)[:3] == ("http", "fake-host.tail.ts.net", port)
    assert f"Task MCP dev viewer: {public}/#{token}" in result.stdout
    assert f"Dev database: {copy} (fresh copy of {live})" in result.stdout
    assert f"Runs as the systemd user service {unit} (installed as {unit_file})." in result.stdout
    assert "Stop it with ./run.sh --dev --tailscale --stop." in result.stdout
    assert mcp_entry(result.stdout)["env"] == {"TASK_MCP_DB": str(copy)}
    assert unit_file.read_text() == unit_text(checkout / ".venv/bin/python", copy, port)
    assert systemctl_calls(env)[-1] == ["--user", "restart", unit] and unit_active(env, unit)
    assert json.loads(state.read_text())["public_origin"] == public
    assert through_tailnet(port, token, public) == 200
    assert workstreams(copy) == ["live"] and list(serve_config(tmp_path)["TCP"]) == [str(port)]

    # The service and its copy are reused; a reinstall keeps the copy too.
    Store(copy).init(
        str(tmp_path / "other"),
        "main",
        workstream_name="dev-only",
        action="create_project",
        confirmed=True,
    )
    again = run_dev(env, checkout, "--tailscale")
    assert tailnet_link(again)[3] == token and f"service {unit} (reused)." in again.stdout
    assert "(kept while the dev viewer runs;" in again.stdout
    reinstalled = run_dev(env, checkout, "--tailscale", "--install-service")
    assert tailnet_link(reinstalled)[3] != token
    assert f"Dev database: {copy} (kept; ./run.sh --dev --tailscale --restart" in reinstalled.stdout
    assert workstreams(copy) == ["live", "dev-only"]

    # --restart takes a fresh copy while the service is stopped, then starts it;
    # --keep --restart restarts it on the existing copy.
    restarted = run_dev(env, checkout, "--tailscale", "--restart")
    new_token = tailnet_link(restarted)[3]
    assert new_token != token and f"service {unit} (restarted)." in restarted.stdout
    assert f"Dev database: {copy} (fresh copy of {live})" in restarted.stdout
    assert workstreams(copy) == ["live"] and workstreams(live) == ["live"]
    assert systemctl_calls(env)[-2:] == [["--user", "stop", unit], ["--user", "start", unit]]
    assert through_tailnet(port, new_token, public) == 200 and unit_active(env, unit)
    Store(copy).init(
        str(tmp_path / "other"),
        "main",
        workstream_name="dev-only",
        action="create_project",
        confirmed=True,
    )
    kept = run_dev(env, checkout, "--tailscale", "--keep", "--restart")
    assert tailnet_link(kept)[3] != new_token and f"Dev database: {copy} (kept)" in kept.stdout
    assert systemctl_calls(env)[-1] == ["--user", "restart", unit]
    assert workstreams(copy) == ["live", "dev-only"]

    # --stop leaves the unit and the copy; a plain --dev --tailscale starts it again
    # on a fresh copy, as for a dev viewer that is not running.
    stopped = run_dev(env, checkout, "--tailscale", "--stop")
    assert stopped.returncode == 0, stopped.stderr
    assert stopped.stdout == (
        f"Tailnet mapping for port {port} removed.\nViewer service stopped.\n"
        f"({unit} stays installed; ./run.sh --dev --tailscale starts it, "
        "--remove-service removes it.)\n"
        f"(The dev copy stays at {copy}.)\n"
    )
    assert unit_file.exists() and not unit_active(env, unit) and copy.exists()
    assert not state.exists() and serve_config(tmp_path) == {}
    started = run_dev(env, checkout, "--tailscale")
    assert f"service {unit} (started)." in started.stdout and "mapping added)" in started.stdout
    assert f"Dev database: {copy} (fresh copy of {live})" in started.stdout
    assert workstreams(copy) == ["live"] and unit_active(env, unit)

    removed = run_dev(env, checkout, "--tailscale", "--remove-service")
    assert removed.returncode == 0, removed.stderr
    assert removed.stdout == (
        f"Tailnet mapping for port {port} removed.\nViewer service {unit} removed.\n"
        f"(The dev copy stays at {copy}.)\n"
    )
    assert not unit_file.exists() and not unit_active(env, unit) and copy.exists()
    assert not state.exists() and serve_config(tmp_path) == {}
    assert run_dev(env, checkout, "--tailscale", "--stop").stdout.startswith(
        "Viewer is not running.\n"
    )
