"""run.sh, including --remote through a stand-in ssh (tests/fake_ssh.py)."""

import json
import os
import re
import shutil
import signal
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

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
