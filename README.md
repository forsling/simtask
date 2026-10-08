# simtask

**simtask** is a local task tracker for agent-driven coding, served over MCP. One private SQLite database holds the tasks, what was built for each, what an independent reviewer said and what you signed off. Agents read and write it through MCP tools and follow the bundled workflows; you steer from a web viewer.

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
git clone https://github.com/forsling/simtask.git simtask
cd simtask
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Point each MCP client at `.venv/bin/simtask`. The default entry point speaks stdio. The optional persistent HTTPS service is described below.

`simtask` replaces the former `simtask` name. The old command, Python imports
and `SIMTASK_` settings remain compatible; `SIMTASK_` settings take precedence.
The data directory is `~/.local/share/simtask`. Before data is moved, an existing
`~/.local/share/simtask/tasks.sqlite3` is used automatically. The workstation's
old data-directory path is an alias to the same relocated data.

The workstation has two checkouts: `~/workspace/simtask` for live use and
`~/workspace/simtask-dev` for development. Each has its own editable environment.
The dev database stays under `simtask-dev/.dev/` and is a disposable copy.

The personal laptop/workstation installation, single-source skill sync, and
refusing laptop MCP selection are documented in [docs/workstation-setup.md](docs/workstation-setup.md).

Claude Code (`~/.claude.json` or a project `.mcp.json`):

```json
{
  "mcpServers": {
    "tasks": {
      "type": "stdio",
      "command": "/path/to/simtask/.venv/bin/simtask",
      "env": { "SIMTASK_ACTOR": "claude-code" }
    }
  }
}
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.tasks]
command = "/path/to/simtask/.venv/bin/simtask"

[mcp_servers.tasks.env]
SIMTASK_ACTOR = "codex"
```

| Variable | Meaning |
|----------|---------|
| `SIMTASK_DB` | Database path. Default `$XDG_DATA_HOME/simtask/tasks.sqlite3`, falling back to `~/.local/share/simtask/tasks.sqlite3` |
| `SIMTASK_ACTOR` | Label recorded on everything this client writes. Default `local-agent` |
| `SIMTASK_TAILSCALE_PORT`, `SIMTASK_DEV_TAILSCALE_PORT` | Fixed ports of `./run.sh --tailscale` and `./run.sh --dev --tailscale`. Defaults 8787 and 8788 |
| `SIMTASK_REMOTE_HOST` | Default host for `./run.sh --remote` |

Running processes keep the code they started with. After pulling new code, restart every connected server and the viewer; `init` reports the runtime it reached under `runtime`.

## Web viewer

A browser view of every project, workstream and task, with the task's spec, activity and notes. It shows and steers; agents do the writing.

```sh
./run.sh                    # start or reuse the viewer, print its private link
./run.sh --restart          # restart it, e.g. after pulling new viewer code
./run.sh --stop             # stop it
./run.sh --tailscale        # the same, reachable from your other devices over Tailscale (see below)
./run.sh --tailscale --stop # stop it and remove its tailscale serve mapping
./run.sh --tailscale --install-service  # run the tailnet viewer as a systemd user service that starts at boot
./run.sh --tailscale --remove-service   # stop, disable and delete that service and its serve mapping
./run.sh --remote [host]    # a viewer on a machine without Tailscale, over an SSH tunnel
SIMTASK_DB=/path/tasks.sqlite3 ./run.sh    # a viewer on another database
```

The link carries a private token; the viewer binds to loopback only. `open_task_viewer` returns the same link to an agent. See [docs/viewer.md](docs/viewer.md).

When `SIMTASK_DB` selects another database, the printed stop command includes its resolved, shell-quoted path. Copy that command to stop the same viewer, including when the path contains spaces.

`--tailscale` is the way to reach the viewer from another machine. It runs the viewer on a fixed loopback port (`SIMTASK_TAILSCALE_PORT`, default 8787) and publishes that port on your tailnet with `tailscale serve` under this machine's MagicDNS name, so the link becomes `https://<machine>.<tailnet>.ts.net:8787/#<token>` and opens from any device on the tailnet without a tunnel. The viewer accepts that name besides loopback, still needs the token and is never published outside the tailnet (no Funnel). The link is `https` when HTTPS certificates are enabled for the tailnet (Tailscale admin console, DNS page, **Enable HTTPS**) and `http` otherwise; the tailnet encrypts both. Run `sudo tailscale set --operator=$USER` once so `tailscale serve` works without root. Repeated runs reuse the viewer and the mapping; `--restart` restarts the viewer with the same token on the same port.

`--tailscale --install-service` makes that viewer survive reboots: it writes `~/.config/systemd/user/simtask-viewer.service` (this checkout's `.venv` serving the live database on the fixed port with the tailnet origin), enables linger for your user with `loginctl enable-linger` so user services run without a login session, ensures the serve mapping and starts the unit. Once installed, `./run.sh`, `--restart` and `--stop` (with or without `--tailscale`) act on the service through `systemctl --user`: a run prints the current link (the token stays the same across service starts), `--stop` leaves the unit installed, and `--tailscale --remove-service` removes it. No system (root) unit is involved.

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
simtask [command] [options]
```

| Command or option | Description |
|-------------------|-------------|
| *(none)* | Serve MCP over stdio |
| `ui` | Start or reuse the web viewer and print its link; `ui --stop` stops it. `--port <n>` fixes its loopback port and `--public-origin <url>` names the origin a proxy on this machine publishes it under (what `./run.sh --tailscale` passes); a running viewer that does not match these is replaced |
| `trace-report` | Report on the private usage traces the server collects (see [docs/usage-traces.md](docs/usage-traces.md)) |
| `--db <path>` | Database to use |
| `--actor <label>` | Attribution label, same as `SIMTASK_ACTOR` |
| `--export-workstream <id>` | Print a workstream as Markdown (`--export-format legacy` for the old layout, `--exclude-closed` to omit done and dropped tasks) |
| `--no-trace`, `--trace-dir <path>` | Disable or relocate usage trace collection |

## Data and upgrades

The database is one SQLite file in WAL mode. Each client runs its own server process; they share the file and serialise writes through short transactions and revision checks, so any number of sessions can work at once. Picked-up tasks show as in progress for four hours or until a result lands; that marker never locks anything.

A server with a newer schema migrates the database the first time it opens it, after taking a verified backup next to it. Servers already running keep working until they stop, but cannot reopen the migrated file on old code, so restart every server and the viewer when you roll out new code (restart the viewer service after a rollout: `./run.sh --tailscale --restart`). Details in [docs/reference.md](docs/reference.md#notes).

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
git worktree add -b my-change ../simtask-my-change   # a worktree on its own branch
cd ../simtask-my-change
./run.sh --dev              # create .venv if missing, copy the live database to .dev/tasks.sqlite3, start a dev viewer on the copy
./run.sh --dev --keep       # reuse the existing copy
./run.sh --dev --restart    # restart the dev viewer on a fresh copy (--keep --restart: on the existing one)
./run.sh --dev --stop       # stop the dev viewer; the copy stays
./run.sh --dev --tailscale  # the dev viewer on the tailnet too, on SIMTASK_DEV_TAILSCALE_PORT (8788), beside the live one
./run.sh --dev --tailscale --install-service  # that dev viewer as its own user service (simtask-dev-viewer-<checkout>-<hash>.service) on the existing copy
./run.sh --dev --tailscale --remove-service   # remove the dev service; the copy stays
```

The dev viewer shows a `DEV` badge in its header (the copy's path is in its tooltip) and a `[DEV]` tab title, so it is never mistaken for the live one, on the tailnet too. `--dev` prints the dev viewer's link, the copy's path, the dev stop command and a `tasks-dev` MCP entry (the worktree's `.venv/bin/simtask` with `SIMTASK_DB` set to the copy). Add that entry beside `tasks` to run the dev server in a client; a session on `tasks-dev` writes to the copy only. `--dev` only reads the live database and leaves the live viewer and running servers alone, so dogfooding through the live `tasks` entry keeps recording there, and `init` from the worktree binds a workstream to the worktree's path and branch as usual. `--dev` runs on the machine that holds the live database and cannot be combined with `--remote` or `SIMTASK_DB`.

Roll out as above: merge to `main`, then restart every live server and the viewer; restart the viewer service after a rollout (`./run.sh --tailscale --restart` in the main checkout, or `./run.sh --restart` without a service). The first server with a newer schema migrates the live database after a backup. Nothing reloads while it runs. A dev service keeps its copy across restarts of the unit; `./run.sh --dev --tailscale --restart` takes a fresh copy.

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


The viewer generates its access token once per database and stores it privately in
`<database>.viewer.token` (mode 0600). Restarts, service reinstallations, and fresh
dev database copies at the same location reuse it. Bookmark the full launch link;
with Tailscale Serve its hostname, port, and token remain stable. After the first
launch, the browser remembers the token for this origin, so bookmarks of normal
location URLs work in new tabs and after browser restarts too. The token is
viewer authentication, separate from Tailscale access. Each database has its own
token; local viewers with randomly assigned ports still need the current address.

### Persistent MCP connections through Tailscale Serve

For laptop clients, run the live MCP server on the workstation independently of
SSH sessions. `python -m simtask.http_service --db /path/to/tasks.sqlite3
--port 8789 --public-origin https://workstation.tailnet.ts.net:8789` binds only to
`127.0.0.1`. Publish it with `tailscale serve --bg --https=8789
http://127.0.0.1:8789`. Keep the existing viewer Serve ports. Do not use Funnel.

Run that command in a systemd user service with `Restart=on-failure`, enable it
under `default.target`, and enable user lingering so it starts before login.
Enable the system `tailscaled` service as well. The MCP service does not need
Tailscale to be ready when it starts listening; clients can connect once the
network and Serve are ready.

The private credential is generated once at `<database>.mcp.token` (mode 0600).
It survives service restarts and must be retained when moving the installation.
Send it as `Authorization: Bearer <credential>`. It is separate from viewer
bookmark tokens. Requests without the credential are rejected. HTTP Host and
Origin checks allow the configured public origin and loopback transport.

Claude uses `https://workstation.tailnet.ts.net:8789/claude/mcp` (actor
`claude-code`). Codex uses `/codex/mcp` (actor `codex-coordinator`). Configure
Claude's `tasks` server with `type: "http"`, `url`, and the Authorization entry
in `headers`. Configure Codex's `[mcp_servers.tasks]` with `url`,
`http_headers = { Authorization = "Bearer <credential>" }`, and
`startup_timeout_sec = 60`. Keep client configuration files private. These
connections need neither an SSH agent nor a shell-exported credential.

Both endpoints use stateless Streamable HTTP, so service restarts do not leave
clients holding invalid server session IDs. Existing stdio usage is unchanged.
