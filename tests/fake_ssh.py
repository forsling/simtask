#!/usr/bin/env python3
"""Test-only stand-in for ssh, installed as `ssh` on PATH by test_run_script.py.

Remote commands run locally in bash with TASK_MCP_DB=$FAKE_REMOTE_DB, so the
"remote" viewer uses a disposable database. `-M -S socket -L ...` starts a
forwarder with a control socket that answers `-O check` and `-O exit`.
FAKE_REMOTE_REPO replaces the checkout path to simulate a missing install;
FAKE_SSH_UNREACHABLE fails like an unreachable host. Every call is logged.
"""

import json
import os
import socket
import subprocess
import sys
import threading

FLAGS = {"-n", "-f", "-N", "-M"}
VALUED = {"-o", "-S", "-O", "-L"}


def parse(argv):
    opts, rest = {}, list(argv)
    while rest and rest[0].startswith("-"):
        flag = rest.pop(0)
        if flag in FLAGS:
            opts[flag] = True
        elif flag in VALUED:
            opts.setdefault(flag, []).append(rest.pop(0))
        else:
            sys.exit(f"fake ssh: unsupported option {flag}")
    return opts, rest[0], " ".join(rest[1:])


def pump(source, target):
    try:
        while data := source.recv(65536):
            target.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (source, target):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def forward(listener, remote_port):
    while True:
        client, _ = listener.accept()
        upstream = socket.create_connection(("127.0.0.1", remote_port))
        threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
        threading.Thread(target=pump, args=(upstream, client), daemon=True).start()


def master(control_path, spec, ready):
    bind_host, local_port, target_host, remote_port = spec.split(":")
    assert (bind_host, target_host) == ("127.0.0.1", "127.0.0.1"), spec
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((bind_host, int(local_port)))
    except OSError as exc:
        os.write(ready, f"bind {local_port}: {exc}".encode())
        sys.exit(255)
    listener.listen()
    control = socket.socket(socket.AF_UNIX)
    control.bind(control_path)
    control.listen()
    with open(control_path + ".pid", "w") as handle:
        handle.write(str(os.getpid()))
    threading.Thread(target=forward, args=(listener, int(remote_port)), daemon=True).start()
    os.write(ready, b"ok")
    os.close(ready)
    while True:
        conn, _ = control.accept()
        command = conn.recv(64)
        conn.sendall(b"ok")
        conn.close()
        if command == b"exit":
            os.unlink(control_path)
            os.unlink(control_path + ".pid")
            os._exit(0)


def control(path, command):
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.connect(path)
            sock.sendall(command.encode())
            return sock.recv(16) == b"ok"
    except OSError as exc:
        print(f"Control socket connect({path}): {exc}", file=sys.stderr)
        return False


def main():
    opts, host, command = parse(sys.argv[1:])
    with open(os.environ["FAKE_SSH_LOG"], "a") as log:
        log.write(json.dumps({"host": host, "opts": opts, "command": command}) + "\n")
    if os.environ.get("FAKE_SSH_UNREACHABLE"):
        print(f"ssh: Could not resolve hostname {host}: Name or service not known", file=sys.stderr)
        sys.exit(255)
    if "-O" in opts:
        sys.exit(0 if control(opts["-S"][0], opts["-O"][0]) else 255)
    if "-M" in opts:
        read, write = os.pipe()
        if os.fork() == 0:
            os.close(read)
            os.setsid()
            devnull = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(devnull, fd)
            master(opts["-S"][0], opts["-L"][0], write)
        os.close(write)
        status = os.read(read, 256).decode()
        if status != "ok":
            print(status, file=sys.stderr)
            sys.exit(255)
        sys.exit(0)
    repo = os.environ.get("FAKE_REMOTE_REPO")
    if repo:
        command = command.replace(os.environ["FAKE_LOCAL_REPO"], repo)
    env = {**os.environ, "TASK_MCP_DB": os.environ["FAKE_REMOTE_DB"]}
    sys.exit(subprocess.run(["bash", "-c", command], env=env, stdin=subprocess.DEVNULL).returncode)


if __name__ == "__main__":
    main()
