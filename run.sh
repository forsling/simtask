#!/usr/bin/env bash
# Start the local Task MCP web GUI (or reuse the running one) and print its link.
#
#   ./run.sh            start or reuse the viewer, print the link
#   ./run.sh --restart  restart it, e.g. after pulling new viewer code
#   ./run.sh --stop     stop it
#
#   ./run.sh --remote [host] [--restart | --stop]
#                       the same for the viewer on host (default example-host),
#                       reached through an SSH tunnel from a laptop loopback port
#
# Uses the live task database unless TASK_MCP_DB is set (local viewer only).
set -euo pipefail

usage() {
  echo "Usage: ./run.sh [--remote [host]] [--restart | --stop]" >&2
  exit 2
}

action=""
remote=""
host=""
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
    *) usage ;;
  esac
  shift
done

# Resolve a relative TASK_MCP_DB against the caller's directory, before changing it.
db_args=()
if [ -n "${TASK_MCP_DB:-}" ]; then
  if [ -n "$remote" ]; then
    echo "TASK_MCP_DB selects a local database; --remote always uses the host's own." >&2
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
  host=${host:-example-host}
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
  echo "No .venv found. Set it up once with:" >&2
  echo "  python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'" >&2
  exit 1
fi

case "$action" in
  "") ;;
  --restart) .venv/bin/task-mcp ui --stop ${db_args[@]+"${db_args[@]}"} >/dev/null ;;
  --stop) exec .venv/bin/task-mcp ui --stop ${db_args[@]+"${db_args[@]}"} ;;
esac

url=$(.venv/bin/task-mcp ui ${db_args[@]+"${db_args[@]}"})
echo "Task MCP viewer: $url"
echo "(The link includes a private access token. Stop it with ./run.sh --stop.)"
