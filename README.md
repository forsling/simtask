# Task MCP

**Task MCP** is a local task tracker for agent-driven coding, served over MCP. One private SQLite database holds the tasks, what was built for each, what an independent reviewer said and what you signed off. Agents read and write it through MCP tools and follow the bundled workflows; you steer from a web viewer.

## How it works

A **project** is a repository. A **workstream** is one checkout and branch of it, with its own ordered task list. A **task** is captured, designed, implemented, reviewed and signed off, and every step is recorded against the task:

```
init(path, branch)            → project, workstream and its task cards
get_next_action(workstream)   → the next task to implement or review, with its full spec
... build it ...
record_result(...)            → what was built, how it was verified, which commits
record_review(...)            → an independent reviewer's pass or rework
signoff_task(...)             → your verdict: approve, rework, revise or drop
```

Results and reviews are local to the workstream they were recorded in, so two branches can try the same task without seeing each other's proof. Every write carries the revision it was based on; a stale revision fails instead of overwriting. Nothing an agent records completes a task: only your sign-off does.

### Task states

| State | Meaning |
|-------|---------|
| `ready` | Eligible for `get_next_action` (includes `rework`, sent back by a reviewer or by you) |
| `question` | An open question gates it; answer it with `task-design` or `proposal-review` |
| `blocked` | A prerequisite task or group is not done yet |
| `review` | A result awaits an independent review |
| `signoff` | A passed review awaits your verdict |
| `inbox` | In no workstream yet (captured ideas land here) |
| `done`, `deferred`, `dropped` | Closed; hidden from boards unless asked for |

### Workflows

The server ships its workflows as skills, read with `get_default_skills`. Copy them into your agent's skill directory or let the agent read them from the server.

| Skill | What it does |
|-------|--------------|
| `init` | Set up or resume the binding between a checkout, a branch and a workstream |
| `task-capture` | Save a task, bug or feature idea with its open design questions; go through saved ideas |
| `task-design` | Research and design a captured feature with you, one decision at a time |
| `proposal-review` | Go through inbox proposals and open questions and record your decisions |
| `superdevloop` | Work through a workstream: a fresh implementer and a fresh reviewer per task, until sign-off |
| `task-signoff` | Walk you through a reviewed result and record your verdict |

## Connecting a client

Requires Python 3.11 or newer.

```sh
git clone https://github.com/forsling/simtask.git task-mcp
cd task-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Point each MCP client at `.venv/bin/task-mcp`. It speaks stdio; there is no network listener.

Claude Code (`~/.claude.json` or a project `.mcp.json`):

```json
{
  "mcpServers": {
    "tasks": {
      "type": "stdio",
      "command": "/path/to/task-mcp/.venv/bin/task-mcp",
      "env": { "TASK_MCP_ACTOR": "claude-code" }
    }
  }
}
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.tasks]
command = "/path/to/task-mcp/.venv/bin/task-mcp"

[mcp_servers.tasks.env]
TASK_MCP_ACTOR = "codex"
```

| Variable | Meaning |
|----------|---------|
| `TASK_MCP_DB` | Database path. Default `$XDG_DATA_HOME/task-mcp/tasks.sqlite3`, falling back to `~/.local/share/task-mcp/tasks.sqlite3` |
| `TASK_MCP_ACTOR` | Label recorded on everything this client writes. Default `local-agent` |
| `TASK_MCP_REMOTE_HOST` | Default host for `./run.sh --remote` |

Running processes keep the code they started with. After pulling new code, restart every connected server and the viewer; `init` reports the runtime it reached under `runtime`.

## Web viewer

A browser view of every project, workstream and task, with the task's spec, activity and notes. It shows and steers; agents do the writing.

```sh
./run.sh                    # start or reuse the viewer, print its private link
./run.sh --restart          # restart it, e.g. after pulling new viewer code
./run.sh --stop             # stop it
./run.sh --remote [host]    # the same for a viewer on another machine, over an SSH tunnel
TASK_MCP_DB=/path/tasks.sqlite3 ./run.sh    # a viewer on another database
```

The link carries a private token; the viewer binds to loopback only. `open_task_viewer` returns the same link to an agent. See [docs/viewer.md](docs/viewer.md).

## Tools

All 31 tools take and return JSON. IDs are stable public IDs; writes take the `expected_revision` the last read or write returned.

| Tool | Description |
|------|-------------|
| **Session** | |
| `init` | Bind a checkout and branch to a workstream and return its task cards |
| `list_projects`, `list_workstreams`, `workstream_status` | Discover projects and workstreams and page one workstream's cards |
| `archive_workstream` | Hide a stale workstream, keeping its history |
| `open_task_viewer` | Start or reuse the web viewer and return its link |
| `get_default_skills` | Read the workflow skill index or one skill |
| **Tasks** | |
| `create_task` | Create a task, or a group with `kind=group`; omit the workstream to leave it in the inbox |
| `list_tasks`, `get_tasks` | Slim cards in workstream order; `get_tasks` with `specification=true` reads the full spec |
| `update_task` | Edit title, summary, body or acceptance criteria at the current revision and spec etag |
| `decompose_task` | Turn a task into a group and create its member tasks |
| `set_disposition` | Defer, drop or reopen |
| `add_to_workstream`, `remove_from_workstream`, `reorder_tasks` | Membership and order per workstream |
| **Gates** | |
| `add_unresolved`, `resolve_unresolved` | Open questions that block a task until you answer |
| `add_prerequisite`, `remove_prerequisite` | Blockers on other tasks or groups, in any project |
| **Work** | |
| `get_next_action` | The next implement or review action in workstream order, with the full spec |
| `record_result` | What was built, how it was verified, which artifacts and commits |
| `record_review` | An independent reviewer's pass or rework, with findings and concerns |
| `signoff_task` | Your verdict: approve, rework, revise or drop |
| `list_task_attempts`, `get_attempt` | Attempt history and one complete attempt |
| **Notes and history** | |
| `create_note`, `update_note`, `get_notes`, `list_notes` | Titled notes referencing tasks, groups, workstreams or projects |
| `list_events` | The audit history of a project, task or note |

## CLI reference

```
task-mcp [command] [options]
```

| Command or option | Description |
|-------------------|-------------|
| *(none)* | Serve MCP over stdio |
| `ui` | Start or reuse the web viewer and print its link; `ui --stop` stops it |
| `trace-report` | Report on the private usage traces the server collects (see [docs/usage-traces.md](docs/usage-traces.md)) |
| `--db <path>` | Database to use |
| `--actor <label>` | Attribution label, same as `TASK_MCP_ACTOR` |
| `--export-workstream <id>` | Print a workstream as Markdown (`--export-format legacy` for the old layout, `--exclude-closed` to omit done and dropped tasks) |
| `--no-trace`, `--trace-dir <path>` | Disable or relocate usage trace collection |

## Data and upgrades

The database is one SQLite file in WAL mode. Each client runs its own server process; they share the file and serialise writes through short transactions and revision checks, so any number of sessions can work at once. Picked-up tasks show as in progress for four hours or until a result lands; that marker never locks anything.

A server with a newer schema migrates the database the first time it opens it, after taking a verified backup next to it. Servers already running keep working until they stop, but cannot reopen the migrated file on old code, so restart every server and the viewer when you roll out new code. Details in [docs/reference.md](docs/reference.md#notes).

## Development

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests examples
.venv/bin/ruff format --check src tests examples
.venv/bin/python examples/demo.py      # a real stdio session against a disposable database
```

### A dev checkout beside the live setup

Every client and `./run.sh` run the main checkout's working tree, on the live database. To change code without touching them, work in a Git worktree: it gets its own venv, and `./run.sh --dev` there runs the viewer and the server on a copy of the live database.

```sh
git worktree add -b my-change ../task-mcp-my-change   # a worktree on its own branch
cd ../task-mcp-my-change
./run.sh --dev              # create .venv if missing, copy the live database to .dev/tasks.sqlite3, start a dev viewer on the copy
./run.sh --dev --keep       # reuse the existing copy
./run.sh --dev --restart    # restart the dev viewer on a fresh copy (--keep --restart: on the existing one)
./run.sh --dev --stop       # stop the dev viewer; the copy stays
```

`--dev` prints the dev viewer's link, the copy's path, the dev stop command and a `tasks-dev` MCP entry (the worktree's `.venv/bin/task-mcp` with `TASK_MCP_DB` set to the copy). Add that entry beside `tasks` to run the dev server in a client; a session on `tasks-dev` writes to the copy only. `--dev` only reads the live database and leaves the live viewer and running servers alone, so dogfooding through the live `tasks` entry keeps recording there, and `init` from the worktree binds a workstream to the worktree's path and branch as usual. `--dev` runs on the machine that holds the live database and cannot be combined with `--remote` or `TASK_MCP_DB`.

Roll out as above: merge to `main`, then restart every live server and the viewer (`./run.sh --restart` in the main checkout); the first server with a newer schema migrates the live database after a backup. Nothing reloads while it runs.

| Document | Contents |
|----------|----------|
| [DESIGN.md](DESIGN.md) | The model and the reasoning behind it |
| [docs/reference.md](docs/reference.md) | Complete behaviour notes for every tool and response shape |
| [docs/viewer.md](docs/viewer.md) | The web viewer |
| [docs/continuation.md](docs/continuation.md) | Resuming and integrating work across workstreams |
| [docs/exports.md](docs/exports.md) | The Markdown export format |
| [docs/runtime.md](docs/runtime.md) | Runtime identity and reconnecting after an upgrade |
| [docs/performance.md](docs/performance.md) | Call latency and client approval prompts |
| [docs/usage-traces.md](docs/usage-traces.md) | Private usage traces and `trace-report` |
