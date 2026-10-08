"""run.sh: --remote through a stand-in ssh (tests/fake_ssh.py) and --dev in a stand-in
checkout whose "live" database sits under a temporary XDG_DATA_HOME."""

import json
import os
import re
import shutil
import signal
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


def test_dev_replaces_a_killed_viewer(dev, tmp_path):
    """A dev viewer that dies without a clean stop (a reboot, a logout, SIGTERM) leaves
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
    os.kill(viewer_pid(copy), signal.SIGTERM)
    for _ in range(100):
        if not alive(port, token):
            break
        time.sleep(0.05)
    assert not alive(port, token) and state.exists()

    result = run_dev(env, checkout)
    new_port, new_token = link(result)
    assert f"Dev database: {copy} (fresh copy of {live})" in result.stdout
    assert "kept" not in result.stdout
    assert (new_port, new_token) != (port, token) and ping(new_port, new_token) == 200
    assert json.loads(state.read_text())["token"] == new_token
    assert workstreams(copy) == ["live"] and workstreams(live) == ["live"]


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
