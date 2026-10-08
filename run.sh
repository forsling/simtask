#!/usr/bin/env bash
# Start the local Task MCP web GUI (or reuse the running one) and print its link.
#
#   ./run.sh            start or reuse the viewer, print the link
#   ./run.sh --restart  restart it, e.g. after pulling new viewer code
#   ./run.sh --stop     stop it
#
#   ./run.sh --remote [host] [--restart | --stop]
#                       the same for the viewer on host (default $TASK_MCP_REMOTE_HOST),
#                       reached through an SSH tunnel from a laptop loopback port
#
#   ./run.sh --dev [--keep] [--restart | --stop]
#                       from a dev checkout (a Git worktree): copy the live database into
#                       .dev/ in this checkout, start a dev viewer on the copy and print a
#                       tasks-dev MCP entry; --keep reuses the copy, --restart takes a
#                       fresh one, --stop stops the dev viewer. Creates .venv when missing.
#
# Uses the live task database unless TASK_MCP_DB is set (local viewer only).
set -euo pipefail

usage() {
  echo "Usage: ./run.sh [--remote [host] | --dev [--keep]] [--restart | --stop]" >&2
  exit 2
}

action=""
remote=""
host=""
dev=""
keep=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --restart | --stop)
      [ -z "$action" ] || usage
      action=$1
      ;;
    --remote)
      [ -z "$remote" ] || usage
      remote=1
      if [ "$#" -gt 1 ] && [ -n "$2" ] && [ "${2#-}" = "$2" ]; then
        host=$2
        shift
      fi
      ;;
    --dev)
      [ -z "$dev" ] || usage
      dev=1
      ;;
    --keep)
      [ -z "$keep" ] || usage
      keep=1
      ;;
    *) usage ;;
  esac
  shift
done
[ -z "$dev" ] || [ -z "$remote" ] || usage
[ -z "$keep" ] || [ -n "$dev" ] || usage
[ -z "$keep" ] || [ "$action" != --stop ] || usage

# Resolve a relative TASK_MCP_DB against the caller's directory, before changing it.
db_args=()
if [ -n "${TASK_MCP_DB:-}" ]; then
  if [ -n "$remote" ]; then
    echo "TASK_MCP_DB selects a local database; --remote always uses the host's own." >&2
    exit 2
  fi
  if [ -n "$dev" ]; then
    echo "--dev copies the live database itself; unset TASK_MCP_DB." >&2
    exit 2
  fi
  TASK_MCP_DB=$(realpath -m -- "$TASK_MCP_DB")
  export TASK_MCP_DB
  db_args=(--db "$TASK_MCP_DB")
fi

# Work from the repository, even when run through a symlink.
cd "$(dirname "$(readlink -f -- "$0")")"

remote_viewer() {
  # The host runs this same script from the same checkout path. Its viewer and
  # the tunnel's far end stay on the host's loopback interface.
  local repo status
  repo=$(printf %q "$PWD")
  set +e
  ssh -n -o ConnectTimeout=10 "$host" \
    "[ -x $repo/run.sh ] && [ -x $repo/.venv/bin/task-mcp ] || exit 97; exec $repo/run.sh $*"
  status=$?
  set -e
  case "$status" in
    0) ;;
    255) echo "Cannot reach $host over SSH (see the ssh error above)." >&2 ;;
    97) echo "task-mcp is not installed on $host: expected a checkout with .venv at $PWD." >&2 ;;
    *) echo "run.sh on $host failed (exit $status)." >&2 ;;
  esac
  return "$status"
}

tunnel_open() {
  # A dead master can leave its socket behind; -O check fails for it too.
  [ -S "$socket" ] && ssh -S "$socket" -O check "$host" 2>/dev/null
}

tunnel_close() {
  if tunnel_open; then
    ssh -S "$socket" -O exit "$host" 2>/dev/null || true
  fi
  rm -f -- "$socket" "$ports"
}

# Proves the tunnel still forwards to this viewer: a master that survived a
# network change or sleep can be alive without a working connection.
forwards() {
  TASK_MCP_TOKEN=$2 python3 - "$1" <<'EOF'
import json, os, sys, urllib.request

origin = "http://127.0.0.1:" + sys.argv[1]
request = urllib.request.Request(
    origin + "/api/ping",
    headers={"X-Task-Token": os.environ["TASK_MCP_TOKEN"], "Origin": origin},
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request, timeout=3) as response:
        sys.exit(json.load(response).get("service") != "task-mcp-viewer")
except Exception:
    sys.exit(1)
EOF
}

if [ -n "$remote" ]; then
  host=${host:-${TASK_MCP_REMOTE_HOST:-}}
  if [ -z "$host" ]; then
    echo "Give a host after --remote or set TASK_MCP_REMOTE_HOST." >&2
    exit 2
  fi
  # Per-host tunnel state: an ssh control socket and the forwarded ports. The
  # runtime directory is cleared on reboot, which also clears stale state.
  state_dir=${XDG_RUNTIME_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}}/task-mcp
  mkdir -p -m 700 -- "$state_dir"
  key=$(printf %s "$host" | tr -c 'A-Za-z0-9@._-' _)
  socket=$state_dir/remote-$key.sock
  ports=$state_dir/remote-$key.ports
  # The backgrounded tunnel (and any ProxyCommand) must not hold the caller's
  # stdout/stderr open, so its messages go here.
  log=$state_dir/remote-$key.log

  if [ "$action" = --stop ]; then
    tunnel_close
    echo "Tunnel to $host closed."
    remote_viewer --stop
    exit
  fi
  [ "$action" = --restart ] && tunnel_close

  output=$(remote_viewer "$action")
  url=$(printf '%s\n' "$output" | grep -Eo 'http://127\.0\.0\.1:[0-9]+/#[A-Za-z0-9_-]+' | head -n 1) || true
  if [ -z "$url" ]; then
    echo "run.sh on $host printed no viewer link." >&2
    exit 1
  fi
  remote_port=${url#http://127.0.0.1:}
  remote_port=${remote_port%%/*}
  token=${url#*/#}

  # Reuse the tunnel only while it forwards to the viewer's current port; a
  # restarted viewer has a new port and token.
  local_port=""
  if tunnel_open && read -r old_local old_remote 2>/dev/null <"$ports" &&
    [ "$old_remote" = "$remote_port" ] && forwards "$old_local" "$token"; then
    local_port=$old_local
  else
    tunnel_close
    # Any free laptop port: the remote port may be taken here, e.g. by a local viewer.
    local_port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
    if ! ssh -f -N -M -S "$socket" -o ControlPersist=no -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=30 -o ConnectTimeout=10 \
      -L "127.0.0.1:$local_port:127.0.0.1:$remote_port" "$host" >/dev/null 2>"$log"; then
      rm -f -- "$socket"
      cat -- "$log" >&2
      echo "Could not open the SSH tunnel to $host." >&2
      exit 1
    fi
    echo "$local_port $remote_port" >"$ports"
    if ! forwards "$local_port" "$token"; then
      tunnel_close
      echo "The SSH tunnel to $host does not reach its viewer." >&2
      exit 1
    fi
  fi
  echo "Task MCP viewer on $host: http://127.0.0.1:$local_port/#$token"
  echo "(The link includes a private access token. Stop it with ./run.sh --remote $host --stop.)"
  exit
fi

if [ ! -x .venv/bin/task-mcp ]; then
  if [ -z "$dev" ]; then
    echo "No .venv found. Set it up once with:" >&2
    echo "  python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'" >&2
    exit 1
  fi
  # A dev checkout gets its own editable install, so it runs this checkout's code.
  echo "No .venv in $PWD; creating one with an editable install of this checkout." >&2
  python3 -m venv .venv
  .venv/bin/python -m pip install -q --disable-pip-version-check -e '.[dev]'
  [ -x .venv/bin/task-mcp ] || { echo "The editable install did not produce .venv/bin/task-mcp." >&2; exit 1; }
fi

# Copies the live database with SQLite's online backup API. The source is opened
# read-only (mode=ro), so the live database is never written or migrated here.
copy_live() {
  rm -f -- "$dev_db" "$dev_db-wal" "$dev_db-shm"
  .venv/bin/python - "$live_db" "$dev_db" <<'EOF'
import os, sqlite3, sys
from contextlib import closing

source, copy = sys.argv[1:]
with (
    closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src,
    closing(sqlite3.connect(copy)) as dst,
):
    src.backup(dst)
os.chmod(copy, 0o600)
EOF
}

# True while the dev viewer answers. Its state file alone proves nothing: a
# viewer killed by a reboot, a logout or SIGTERM leaves the file behind.
dev_viewer_live() {
  .venv/bin/python -c \
    'import sys; from pathlib import Path; from task_mcp.viewer import _live; sys.exit(not _live(Path(sys.argv[1])))' \
    "$dev_state"
}

if [ -n "$dev" ]; then
  # The dev copy lives in the checkout (.dev/ is gitignored). The live database
  # is the server's default: the same resolution as task_mcp.store.default_database.
  live_db=${XDG_DATA_HOME:-$HOME/.local/share}/task-mcp/tasks.sqlite3
  dev_dir=$PWD/.dev
  dev_db=$dev_dir/tasks.sqlite3
  dev_state=$dev_db.viewer.json

  if [ "$action" = --stop ]; then
    .venv/bin/task-mcp ui --stop --db "$dev_db"
    echo "(The dev copy stays at $dev_db.)"
    exit
  fi
  mkdir -p -- "$dev_dir"
  if [ -n "$keep" ]; then
    if [ ! -f "$dev_db" ]; then
      echo "No dev copy at $dev_db; run ./run.sh --dev without --keep to take one." >&2
      exit 1
    fi
    copy_note="kept"
    [ "$action" != --restart ] || .venv/bin/task-mcp ui --stop --db "$dev_db" >/dev/null
  elif [ "$action" = --restart ] || ! dev_viewer_live; then
    # A fresh copy; the dev viewer, if any, is stopped first so nothing reads
    # the copy while it is replaced. A dev viewer that answers is otherwise
    # reused together with its copy (--restart takes a fresh one).
    if [ ! -f "$live_db" ]; then
      echo "No live database at $live_db to copy." >&2
      exit 1
    fi
    .venv/bin/task-mcp ui --stop --db "$dev_db" >/dev/null
    copy_live
    copy_note="fresh copy of $live_db"
  else
    copy_note="kept while the dev viewer runs; ./run.sh --dev --restart takes a fresh copy"
  fi

  url=$(.venv/bin/task-mcp ui --db "$dev_db")
  echo "Task MCP dev viewer: $url"
  echo "Dev database: $dev_db ($copy_note)"
  echo "(The link includes a private access token. Stop it with ./run.sh --dev --stop.)"
  echo
  echo "MCP entry for the dev server, to add beside the live \"tasks\" entry:"
  echo "  \"tasks-dev\": {"
  echo "    \"type\": \"stdio\","
  echo "    \"command\": \"$PWD/.venv/bin/task-mcp\","
  echo "    \"env\": { \"TASK_MCP_DB\": \"$dev_db\" }"
  echo "  }"
  exit
fi

case "$action" in
  "") ;;
  --restart) .venv/bin/task-mcp ui --stop ${db_args[@]+"${db_args[@]}"} >/dev/null ;;
  --stop) exec .venv/bin/task-mcp ui --stop ${db_args[@]+"${db_args[@]}"} ;;
esac

url=$(.venv/bin/task-mcp ui ${db_args[@]+"${db_args[@]}"})
echo "Task MCP viewer: $url"
echo "(The link includes a private access token. Stop it with ./run.sh --stop.)"
