#!/usr/bin/env python3
"""Test-only stand-in for systemctl, installed as `systemctl` on PATH by
test_run_script.py.

Only `--user` commands are accepted. Unit files are read from
$XDG_CONFIG_HOME/systemd/user. `start` runs the unit's ExecStart as a detached
process and `stop` sends it SIGTERM (SIGKILL after a wait), like the user manager
would; `restart`, `enable`, `disable`, `is-active`, `is-enabled` and `daemon-reload`
keep the unit's state in the FAKE_SYSTEMCTL_STATE directory. Every call is logged
to FAKE_SYSTEMCTL_LOG.
"""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path


def state_dir():
    path = Path(os.environ["FAKE_SYSTEMCTL_STATE"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def unit_file(unit):
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "systemd/user" / unit


def exec_start(unit):
    for line in unit_file(unit).read_text().splitlines():
        if line.startswith("ExecStart="):
            return shlex.split(line.removeprefix("ExecStart="))
    sys.exit(f"fake systemctl: {unit} has no ExecStart")


def pid_of(unit):
    try:
        pid = int((state_dir() / f"{unit}.pid").read_text())
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
    except (OSError, ValueError):
        return None
    return pid if "task_mcp.viewer" in argv else None


def start(unit):
    if pid_of(unit):
        return
    if not unit_file(unit).exists():
        print(f"Failed to start {unit}: Unit {unit} not found.", file=sys.stderr)
        sys.exit(5)
    child = subprocess.Popen(
        exec_start(unit),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    (state_dir() / f"{unit}.pid").write_text(str(child.pid))


def stop(unit):
    pid = pid_of(unit)
    if pid:
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            if not pid_of(unit):
                break
            time.sleep(0.05)
        else:
            os.kill(pid, signal.SIGKILL)
    (state_dir() / f"{unit}.pid").unlink(missing_ok=True)


def main():
    argv = sys.argv[1:]
    with open(os.environ["FAKE_SYSTEMCTL_LOG"], "a") as log:
        log.write(json.dumps(argv) + "\n")
    if argv[:1] != ["--user"]:
        sys.exit(f"fake systemctl: only --user commands are supported, not {argv}")
    quiet = "--quiet" in argv
    argv = [arg for arg in argv[1:] if arg != "--quiet"]
    command, units = argv[0], argv[1:]
    if command == "daemon-reload":
        return
    if len(units) != 1:
        sys.exit(f"fake systemctl: {command} takes one unit, not {units}")
    unit = units[0]
    enabled = state_dir() / f"{unit}.enabled"
    if command == "start":
        start(unit)
    elif command == "stop":
        stop(unit)
    elif command == "restart":
        stop(unit)
        start(unit)
    elif command == "enable":
        if not unit_file(unit).exists():
            print(f"Failed to enable unit: Unit file {unit} does not exist.", file=sys.stderr)
            sys.exit(1)
        enabled.touch()
    elif command == "disable":
        enabled.unlink(missing_ok=True)
    elif command == "is-active":
        active = bool(pid_of(unit))
        if not quiet:
            print("active" if active else "inactive")
        sys.exit(0 if active else 3)
    elif command == "is-enabled":
        if not quiet:
            print("enabled" if enabled.exists() else "disabled")
        sys.exit(0 if enabled.exists() else 1)
    else:
        sys.exit(f"fake systemctl: unsupported command {command}")


if __name__ == "__main__":
    main()
