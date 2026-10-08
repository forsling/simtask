#!/usr/bin/env bash
# Start the local Task MCP web GUI (or reuse the running one) and print its link.
#
#   ./run.sh            start or reuse the viewer, print the link
#   ./run.sh --restart  restart it, e.g. after pulling new viewer code
#   ./run.sh --stop     stop it
#
#   ./run.sh --tailscale [--restart | --stop]
#                       the same, published on the tailnet with tailscale serve under this
#                       machine's MagicDNS name on port $TASK_MCP_TAILSCALE_PORT (8787);
#                       --stop also removes the serve mapping. Never Funnel.
#
#   ./run.sh --tailscale --install-service
#                       run that tailnet viewer as a systemd user service that starts at
#                       boot (task-mcp-viewer.service, linger enabled). Once installed,
#                       ./run.sh, --restart and --stop act on the service, with or without
#                       --tailscale; --tailscale --remove-service removes it again.
#
#   ./run.sh --remote [host] [--restart | --stop]
#                       the same for the viewer on a host without Tailscale (default
#                       $TASK_MCP_REMOTE_HOST), reached through an SSH tunnel from a
#                       laptop loopback port
#
#   ./run.sh --dev [--keep] [--restart | --stop]
#                       from a dev checkout (a Git worktree): copy the live database into
#                       .dev/ in this checkout, start a dev viewer on the copy and print a
#                       tasks-dev MCP entry; --keep reuses the copy, --restart takes a
#                       fresh one, --stop stops the dev viewer. Creates .venv when missing.
#                       With --tailscale, the dev viewer is published on port
#                       $TASK_MCP_DEV_TAILSCALE_PORT (8788) beside the live one, and
#                       --install-service installs a service per dev checkout
#                       (task-mcp-dev-viewer-<checkout>-<hash>.service) on the copy.
#
# Uses the live task database unless TASK_MCP_DB is set (local viewer only).
set -euo pipefail

usage() {
  echo "Usage: ./run.sh [--remote [host] | [--dev [--keep]] [--tailscale [--install-service | --remove-service]]] [--restart | --stop]" >&2
  exit 2
}

action=""
remote=""
host=""
dev=""
keep=""
tailscale=""
install=""
remove=""
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
    --tailscale)
      [ -z "$tailscale" ] || usage
      tailscale=1
      ;;
    --install-service)
      [ -z "$install" ] || usage
      install=1
      ;;
    --remove-service)
      [ -z "$remove" ] || usage
      remove=1
      ;;
    *) usage ;;
  esac
  shift
done
[ -z "$dev" ] || [ -z "$remote" ] || usage
[ -z "$tailscale" ] || [ -z "$remote" ] || usage
[ -z "$keep" ] || [ -n "$dev" ] || usage
[ -z "$keep" ] || [ "$action" != --stop ] || usage
[ -z "$install$remove" ] || [ -n "$tailscale" ] || usage
[ -z "$install" ] || [ -z "$remove" ] || usage
[ -z "$install$remove" ] || [ -z "$action" ] || usage
[ -z "$install$remove" ] || [ -z "$keep" ] || usage

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
  if [ -n "$install$remove" ]; then
    echo "The viewer service serves the live database; unset TASK_MCP_DB." >&2
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
  # restarted viewer may have a new port; its database token stays the same.
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

# --tailscale: the viewer listens on a fixed loopback port that tailscaled proxies
# to under this machine's MagicDNS name, so the laptop needs no SSH tunnel. The
# viewer accepts that public origin besides loopback and prints the tailnet link.
# The tailnet encrypts the hop either way; https needs certificates enabled for the
# tailnet (then status lists CertDomains). Funnel is never touched.
tailnet_origin() {
  tailscale status --json | python3 -c '
import json, sys

text = sys.stdin.read()
if not text.strip():
    sys.exit(1)  # tailscale printed its own error
status = json.loads(text)
name = (status.get("Self") or {}).get("DNSName", "").rstrip(".")
if not name:
    sys.exit("This machine has no MagicDNS name; is Tailscale up?")
print("https" if status.get("CertDomains") else "http", name)
'
}

# The scheme tailscaled currently serves our port with ("https", "http" or nothing).
serve_scheme() {
  tailscale serve status --json | python3 -c '
import json, sys

handler = ((json.load(sys.stdin) or {}).get("TCP") or {}).get(sys.argv[1]) or {}
print("https" if handler.get("HTTPS") else "http" if handler.get("HTTP") else "")
' "$port"
}

# True while tailscaled proxies the wanted scheme on our port to the viewer, Funnel off.
serve_mapped() {
  tailscale serve status --json | python3 -c '
import json, sys

scheme, host, port = sys.argv[1:]
config = json.load(sys.stdin) or {}
tcp = (config.get("TCP") or {}).get(port) or {}
web = (config.get("Web") or {}).get(f"{host}:{port}") or {}
proxy = ((web.get("Handlers") or {}).get("/") or {}).get("Proxy")
ok = tcp.get("HTTPS" if scheme == "https" else "HTTP") and proxy == f"http://127.0.0.1:{port}"
sys.exit(0 if ok and not (config.get("AllowFunnel") or {}).get(f"{host}:{port}") else 1)
' "$scheme" "$public_host" "$port"
}

serve_map() {
  if serve_mapped; then
    mapping="reused"
  elif tailscale serve --bg "--$scheme=$port" "http://127.0.0.1:$port" >/dev/null; then
    mapping="added"
  else
    echo "tailscale serve failed; the viewer runs on 127.0.0.1:$port but is not published." >&2
    echo "(An 'Access denied' needs 'sudo tailscale set --operator=\$USER' once.)" >&2
    exit 1
  fi
}

serve_unmap() {
  local current
  if ! current=$(serve_scheme); then
    echo "Could not read the tailscale serve status; any mapping for port $port stays." >&2
  elif [ -n "$current" ]; then
    tailscale serve "--$current=$port" off
    echo "Tailnet mapping for port $port removed."
  fi
}

ui_args=()
if [ -n "$tailscale" ]; then
  if [ -n "$dev" ]; then
    port=${TASK_MCP_DEV_TAILSCALE_PORT:-8788}
  else
    port=${TASK_MCP_TAILSCALE_PORT:-8787}
  fi
  case "$port" in
    '' | *[!0-9]* | 0*) port=70000 ;;
  esac
  if [ "$port" -gt 65535 ]; then
    echo "TASK_MCP_TAILSCALE_PORT and TASK_MCP_DEV_TAILSCALE_PORT must be port numbers." >&2
    exit 2
  fi
  if [ "$action" != --stop ] && [ -z "$remove" ]; then
    read -r scheme public_host < <(tailnet_origin) || true
    if [ -z "${public_host:-}" ]; then
      echo "Cannot read this machine's tailnet name (tailscale status --json failed)." >&2
      exit 1
    fi
    ui_args=(--port "$port" --public-origin "$scheme://$public_host:$port")
  fi
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

# The live database is the server's default: the same resolution as
# task_mcp.store.live_database, with symlinks resolved like the viewer does.
live_db=$(realpath -m -- "${XDG_DATA_HOME:-$HOME/.local/share}/task-mcp/tasks.sqlite3")

# True while the viewer with state file $1 answers. The file alone proves nothing:
# a viewer killed by a reboot, a logout or SIGKILL leaves it behind.
viewer_live() {
  .venv/bin/python -c \
    'import sys; from pathlib import Path; from task_mcp.viewer import _live; sys.exit(not _live(Path(sys.argv[1])))' \
    "$1"
}

# --install-service: the tailnet viewer runs as a systemd user service, started at
# boot by the user manager (linger keeps it running without a login session). The
# unit runs this checkout's .venv in the foreground on the fixed port; systemd stops
# it with SIGTERM, which the viewer ends cleanly. Once a unit is installed, run.sh
# starts, reuses, restarts and stops the service instead of a detached viewer.
unit_path=""
service_vars() {
  service_db=$1
  service_state=$service_db.viewer.json
  unit=$2
  unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
  unit_path=$unit_dir/$unit
}

service_installed() {
  [ -n "$unit_path" ] && [ -f "$unit_path" ]
}

service_active() {
  systemctl --user is-active --quiet "$unit"
}

# Stops a detached viewer on the service's database: it would hold the viewer
# lock, and the service would exit at once.
service_takeover() {
  service_active || .venv/bin/task-mcp ui --stop --db "$service_db" >/dev/null
}

service_install() {
  local user
  mkdir -p -- "$unit_dir"
  cat >"$unit_path" <<EOF
[Unit]
Description=Task MCP viewer on $service_db (tailnet port $port)
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
ExecStart=$PWD/.venv/bin/python -m task_mcp.viewer --serve --db $service_db --port $port --public-origin $scheme://$public_host:$port
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  user=$(id -un)
  if [ "$(loginctl show-user "$user" -p Linger --value 2>/dev/null)" != yes ]; then
    if loginctl enable-linger "$user"; then
      echo "Linger enabled for $user: user services run without a login session."
    else
      echo "loginctl enable-linger $user failed: the service stops at logout until you run it by hand." >&2
    fi
  fi
  serve_map
  service_takeover
  systemctl --user enable --quiet "$unit"
  systemctl --user restart "$unit"
  service_note="installed as $unit_path"
}

service_start() {
  if service_active && viewer_live "$service_state"; then
    service_note="reused"
  else
    service_takeover
    systemctl --user start "$unit"
    service_note="started"
  fi
}

service_restart() {
  systemctl --user restart "$unit"
  service_note="restarted"
}

# A detached viewer (task-mcp ui or open_task_viewer on the same database) can
# answer instead of a stopped unit; --stop ends that one too.
service_stop() {
  if service_active; then
    systemctl --user stop "$unit"
    echo "Viewer service stopped."
  elif [ "$(.venv/bin/task-mcp ui --stop --db "$service_db")" = "Viewer stopped." ]; then
    echo "Viewer service is not running; the detached viewer on its database stopped."
  else
    echo "Viewer service is not running."
  fi
  echo "($unit stays installed; ./run.sh${dev:+ --dev} --tailscale starts it, --remove-service removes it.)"
}

service_remove() {
  if ! service_installed; then
    echo "No viewer service is installed ($unit_path)."
    return
  fi
  systemctl --user disable --quiet "$unit"
  systemctl --user stop "$unit"
  rm -f -- "$unit_path"
  systemctl --user daemon-reload
  echo "Viewer service $unit removed."
}

# The service's link, once its viewer answers; a service takes a moment to start.
service_url() {
  url=$(.venv/bin/python - "$service_state" <<'EOF'
import sys, time
from pathlib import Path

from task_mcp.viewer import _link, _live

state = Path(sys.argv[1])
for _ in range(100):
    if info := _live(state):
        print(_link(info))
        sys.exit()
    time.sleep(0.05)
sys.exit(1)
EOF
  ) || {
    echo "The viewer service did not come up; see: systemctl --user status $unit" >&2
    exit 1
  }
}

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

if [ -n "$dev" ]; then
  # The dev copy lives in the checkout (.dev/ is gitignored). A dev service is
  # named after the checkout, with a hash of its path to keep worktrees apart.
  dev_dir=$PWD/.dev
  dev_db=$dev_dir/tasks.sqlite3
  dev_state=$dev_db.viewer.json
  checkout=$(basename -- "$PWD")
  service_vars "$dev_db" \
    "task-mcp-dev-viewer-${checkout//[^A-Za-z0-9_-]/_}-$(printf %s "$PWD" | sha256sum | cut -c1-8).service"

  if [ "$action" = --stop ]; then
    [ -z "$tailscale" ] || serve_unmap
    if service_installed; then
      service_stop
    else
      .venv/bin/task-mcp ui --stop --db "$dev_db"
    fi
    echo "(The dev copy stays at $dev_db.)"
    exit
  fi
  if [ -n "$remove" ]; then
    serve_unmap
    service_remove
    echo "(The dev copy stays at $dev_db.)"
    exit
  fi
  mkdir -p -- "$dev_dir"
  # A fresh copy; any viewer on the copy is stopped first so nothing reads it
  # while it is replaced.
  fresh_copy() {
    if [ ! -f "$live_db" ]; then
      echo "No live database at $live_db to copy." >&2
      exit 1
    fi
    ! service_installed || systemctl --user stop "$unit"
    .venv/bin/task-mcp ui --stop --db "$dev_db" >/dev/null
    copy_live
    copy_note="fresh copy of $live_db"
  }
  if [ -n "$keep" ] && [ ! -f "$dev_db" ]; then
    echo "No dev copy at $dev_db; run ./run.sh --dev without --keep to take one." >&2
    exit 1
  fi
  if [ -n "$install" ]; then
    # The service serves the existing copy; one is taken when there is none.
    if [ -f "$dev_db" ]; then
      copy_note="kept; ./run.sh --dev --tailscale --restart takes a fresh copy"
    else
      fresh_copy
    fi
    service_install
  elif service_installed; then
    if [ -n "$keep" ]; then
      copy_note="kept"
      if [ "$action" = --restart ]; then service_restart; else service_start; fi
    elif [ "$action" = --restart ] || ! viewer_live "$dev_state"; then
      fresh_copy
      systemctl --user start "$unit"
      if [ "$action" = --restart ]; then service_note="restarted"; else service_note="started"; fi
    else
      # The service is reused while it runs; a detached viewer on the copy (a
      # stopped unit, then task-mcp ui or the tasks-dev entry) is taken over.
      copy_note="kept while the dev viewer runs; ./run.sh --dev --restart takes a fresh copy"
      service_start
    fi
  elif [ -n "$keep" ]; then
    copy_note="kept"
    [ "$action" != --restart ] || .venv/bin/task-mcp ui --stop --db "$dev_db" >/dev/null
  elif [ "$action" = --restart ] || ! viewer_live "$dev_state"; then
    # A dev viewer that answers is otherwise reused together with its copy
    # (--restart takes a fresh one).
    fresh_copy
  else
    copy_note="kept while the dev viewer runs; ./run.sh --dev --restart takes a fresh copy"
  fi

  if service_installed; then
    service_url
  else
    url=$(.venv/bin/task-mcp ui --db "$dev_db" ${ui_args[@]+"${ui_args[@]}"})
  fi
  # service_install has already ensured the mapping.
  [ -n "$install" ] || [ -z "$tailscale" ] || serve_map
  echo "Task MCP dev viewer: $url"
  echo "Dev database: $dev_db ($copy_note)"
  if [ -n "$tailscale" ]; then
    echo "Published on the tailnet by tailscale serve ($scheme on port $port, mapping $mapping)."
  fi
  [ -z "${service_note:-}" ] || echo "Runs as the systemd user service $unit ($service_note)."
  if [ -n "$tailscale" ]; then
    echo "(The link includes a private access token. Stop it with ./run.sh --dev --tailscale --stop.)"
  else
    echo "(The link includes a private access token. Stop it with ./run.sh --dev --stop.)"
  fi
  echo
  echo "MCP entry for the dev server, to add beside the live \"tasks\" entry:"
  echo "  \"tasks-dev\": {"
  echo "    \"type\": \"stdio\","
  echo "    \"command\": \"$PWD/.venv/bin/task-mcp\","
  echo "    \"env\": { \"TASK_MCP_DB\": \"$dev_db\" }"
  echo "  }"
  exit
fi

# The live service; TASK_MCP_DB selects another database, which never has one.
[ -n "${TASK_MCP_DB:-}" ] || service_vars "$live_db" task-mcp-viewer.service

if [ -n "$remove" ]; then
  serve_unmap
  service_remove
  exit
fi
if [ -n "$install" ]; then
  service_install
  service_url
elif service_installed; then
  case "$action" in
    "") service_start ;;
    --restart) service_restart ;;
    --stop)
      [ -z "$tailscale" ] || serve_unmap
      service_stop
      exit
      ;;
  esac
  service_url
  [ -z "$tailscale" ] || serve_map
else
  case "$action" in
    "") ;;
    --restart) .venv/bin/task-mcp ui --stop ${db_args[@]+"${db_args[@]}"} >/dev/null ;;
    --stop)
      [ -z "$tailscale" ] || serve_unmap
      exec .venv/bin/task-mcp ui --stop ${db_args[@]+"${db_args[@]}"}
      ;;
  esac
  url=$(.venv/bin/task-mcp ui ${db_args[@]+"${db_args[@]}"} ${ui_args[@]+"${ui_args[@]}"})
  [ -z "$tailscale" ] || serve_map
fi
echo "Task MCP viewer: $url"
if [ -n "$tailscale" ]; then
  echo "Published on the tailnet by tailscale serve ($scheme on port $port, mapping $mapping)."
fi
[ -z "${service_note:-}" ] || echo "Runs as the systemd user service $unit ($service_note)."
if [ -n "$tailscale" ]; then
  echo "(The link includes a private access token. Stop it with ./run.sh --tailscale --stop.)"
else
  echo "(The link includes a private access token. Stop it with ./run.sh --stop.)"
fi
