# Task MCP v1

Task MCP is a local, opt-in task-state service for coding workflows. It stores
project tasks, workstream scopes, implementation attempts, review and human
sign-off in a private SQLite database. It exposes MCP primitives over stdio;
agents may use those primitives directly or follow the bundled reference
workflows. The service never edits repositories, client configuration or
installed skills. It writes an export file only when a caller explicitly saves
the returned export at a chosen destination.

## Install and verify

Requires Python 3.11 or newer. From this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests examples
.venv/bin/ruff format --check src tests examples
.venv/bin/python examples/demo.py
```

The demo uses a disposable database and a real subprocess MCP stdio connection.
It records only synthetic decisions. The configured executable for a client is
`/path/to/task-mcp/.venv/bin/task-mcp`; set `TASK_MCP_ACTOR` to a
descriptive attribution label if useful. The default database is
`$XDG_DATA_HOME/task-mcp/tasks.sqlite3`, falling back to
`~/.local/share/task-mcp/tasks.sqlite3`. Override with `TASK_MCP_DB` or `--db`.
No network listener is needed. Connect this command in each client's own MCP
configuration; Task MCP does not install its connection.

## First project and workstream

The reference `init` flow detects the canonical checkout path and Git branch,
shows them and the proposed scope to the user, and obtains confirmation before
calling `init_project`. Unknown projects are never registered by task creation.
For another checkout of an existing project, call `attach_checkout`, then
`init_workstream` if it needs its own scope. For a new branch in an attached
checkout, call `init_workstream` with an explicit set expression. Detached HEAD
and non-Git contexts need a workstream name. A workstream has a durable ID, while
its branch name is a mutable binding. `rebind_workstream` preserves history when
the branch or checkout changes. Call `preflight` before autonomous work; a
mismatched or unknown binding is an actionable stop.

Workstream scope is an explicit set of canonical project tasks. `none` begins
empty. A workstream name or ID as the first expression term snapshots its scope.
`+task-id` and `-task-id` then add or remove references. Titles are accepted
only when unambiguous. A group reference remains live: later group members enter
its scoped workstreams, but begin pending acceptance. An explicit `-task-id`
exclusion persists and overrides membership inherited from a scoped group;
`+task-id` removes that exclusion. Every explicit scope change advances the
workstream revision. A task proposed from a
workstream may enter that scope or the project inbox. It cannot be delegated to
another workstream by creating it. Project order is global and advisory;
prerequisites are hard gates. `get_next_task` returns the first full eligible
task without claiming it, or compact reasons why the scope has no eligible work.

## Task lifecycle

There is one task model. `create_task(source="agent")` creates a pending proposal.
`source="user"` can create an accepted task in one call when `user_request`
records the explicit request. `accept_task` binds user acceptance to the current
specification. Editing title, body or acceptance criteria increments the hidden
specification revision and invalidates acceptance. Resolving an unresolved item,
changing scope or order, or adding evidence does not invalidate acceptance.
Unresolved items are live gates; resolve them after settling the matter and
record any material decision in the task description. A `blocked_by` prerequisite
clears as an eligibility constraint when its prerequisite is signed off. A task
may depend on a group; that gate clears after every required member completes.

Use `decompose_task` to turn a task into a first-class group while retaining its
ID, description, acceptance history and audit history. The call atomically
creates required pending member tasks. Groups cannot nest, have no implementation
attempts or execution gates, and derive completion from all members being
complete. Resolve unresolved items and proposed gates before decomposition;
existing prerequisites move to the concrete members. Use related
pending proposals for optional work. A session actively handling a task may add
an unresolved item or prerequisite. An observer can submit a nonblocking gate
proposal for an active session or the user to accept. `propose_prerequisite`
atomically creates and links a pending prerequisite in the current scope or
project inbox. The service records the asserted handling role but does not
authenticate agent identity.

Reading or selecting a task does not create an attempt. The implementer calls
`record_result` only on leaving a durable result and evidence. This creates an
attempt for that workstream. Other workstreams can produce independent attempts
on the same task. An independent reviewer named differently from the implementer
returns a verdict; the coordinator records it with `record_review`. A recorded
`human_review` can satisfy the review gate when the user actually reviews the
result or explicitly directs that further review is unnecessary. Agents must
not self-issue it. One informed `signoff_task` verdict chooses a reviewed
attempt. Approval completes the canonical task and records the selected attempt
ID. Completed tasks are immutable; changed requirements become new tasks.
Rejection chooses `rework` for the accepted specification or `revise` to
invalidate acceptance and add an unresolved item. The service cannot
authenticate reviewer independence or the
human verdict, so reference workflows must accurately obtain and record them.

The ordinary path is:

```text
init → proposal/unresolved review → superdevloop → human sign-off
```

The canonical reference skill files are packaged under
`src/task_mcp/reference_skills/`. `get_default_skills` returns their exact text,
version and SHA-256 hashes. Agents may fetch and follow them directly. Copying
them to a client's native skill location requires explicit user authorization;
the server does not install or update clients. Single-agent devloop and guided
task flows are possible using the same primitives, but reference adapters for
those rare workflows are deferred.

## MCP tools

| Area | Tools |
| --- | --- |
| Project and workstream | `list_projects`, `init_project`, `attach_checkout`, `init_workstream`, `list_workstreams`, `rebind_workstream`, `preflight` |
| Scope and queue | `set_scope`, `list_tasks`, `get_tasks`, `reorder_tasks`, `get_next_task` |
| Specification and gates | `create_task`, `update_task`, `accept_task`, `set_disposition`, `add_unresolved`, `resolve_unresolved`, `add_prerequisite`, `propose_prerequisite`, `accept_gate_proposal`, `decompose_task` |
| Delivery | `record_result`, `record_review`, `human_review`, `signoff_task` |
| Inspection | `list_events`, `export_workstream`, `get_default_skills` |

Mutations that change a task or workstream require the last revision read.
`reorder_tasks` takes the previously read complete project order as
`expected_order` for the same concurrency check.
Concurrent writes to one revision permit one winner and return
`revision_conflict` to the other. Task creation is not deduplicated: inspect the
board before retrying an uncertain response. Every handler call except the
static skill catalog appends a local audit event, including reads and domain
errors. Task mutations retain before/after snapshots. The configured actor label
is not authenticated. `list_events` supports a stable pagination ceiling.

`export_workstream` returns a versioned Markdown document with a structured JSON
block for each scoped task, including gates and attempts, plus a SHA-256 hash.
The output is deterministic for unchanged state and is export-only: editing it
does not update the service. The CLI can print the same view:

```sh
.venv/bin/task-mcp --export-workstream wst_your_workstream_id
```

The service creates its current SQLite schema in a private database. For a live
WAL database, use SQLite's backup API instead of copying only the main
`.sqlite3` file.

## Deferred work

V1 does not import TASKS.md, support nested groups, per-workstream ordering,
persistent dynamic scope filters, mandatory claims or leases, dedicated native
client packages, automatic client configuration, or editable export
synchronization. A complete Codex dogfood trial and then Claude Code, OpenCode
and Pi access/catalog validation remain product proof beyond the unit and stdio
suite. See [DESIGN.md](DESIGN.md) for the current design and boundary.
