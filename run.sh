#!/usr/bin/env bash
# Start the local Task MCP web GUI (or reuse the running one) and print its link.
#
#   ./run.sh            start or reuse the viewer, print the link
#   ./run.sh --restart  restart it, e.g. after pulling new viewer code
#   ./run.sh --stop     stop it
#
# Uses the live task database unless TASK_MCP_DB is set.
set -euo pipefail

usage() {
  echo "Usage: ./run.sh [--restart | --stop]" >&2
  exit 2
}
[ "$#" -le 1 ] || usage

# Resolve a relative TASK_MCP_DB against the caller's directory, before changing it.
db_args=()
if [ -n "${TASK_MCP_DB:-}" ]; then
  TASK_MCP_DB=$(realpath -m -- "$TASK_MCP_DB")
  export TASK_MCP_DB
  db_args=(--db "$TASK_MCP_DB")
fi

# Work from the repository, even when run through a symlink.
cd "$(dirname "$(readlink -f -- "$0")")"

if [ ! -x .venv/bin/task-mcp ]; then
  echo "No .venv found. Set it up once with:" >&2
  echo "  python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'" >&2
  exit 1
fi

case "${1:-}" in
  "") ;;
  --restart) .venv/bin/task-mcp ui --stop ${db_args[@]+"${db_args[@]}"} >/dev/null ;;
  --stop) exec .venv/bin/task-mcp ui --stop ${db_args[@]+"${db_args[@]}"} ;;
  *) usage ;;
esac

url=$(.venv/bin/task-mcp ui ${db_args[@]+"${db_args[@]}"})
echo "Task MCP viewer: $url"
echo "(The link includes a private access token. Stop it with ./run.sh --stop.)"
