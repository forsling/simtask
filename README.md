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

## Session init and workstreams

Call `init` with the absolute path of the target checkout and its branch, or an
explicit `workstream_name` for detached/non-Git work. The target is independent
of the MCP server process cwd and the agent session cwd. For example, a session
in a parent directory can call `init(path="/work/service-a", branch="main")`
and then `init(path="/work/service-b", branch="feature")`. Keep both returned
project/workstream IDs for subsequent task calls; there is no global current
project or persistent session entity. Repeating either call returns its own
`ready` context and scoped queue without changing the other.

An exact existing path/branch binding returns `ready` with project and
workstream identities, revision, binding and compact scoped queue. No confirmation
or separate preflight is needed for ordinary resume. A known checkout on an
unbound branch returns `new_branch` and that project's registered workstream
candidates. An unknown checkout returns `unregistered_checkout` with project and
workstream candidates and the choices `create_project`, `attach_workstream`, and
`rebind_workstream`. A branch bound to another checkout returns `mismatch`.
Candidates are recorded bindings, not scanned Git refs or running agents. The
initial call may append an audit event but changes no project, task, scope or
workstream state.

After choosing setup, call `init` again with `confirmed=true` and `action`:
`create_project` creates a project and its first workstream;
`new_workstream` creates a branch/name in the already attached checkout;
`attach_workstream` atomically attaches an unknown checkout to the specified
existing project and creates its workstream; `rebind_workstream` moves a selected
durable workstream binding to this path/branch, preserving scope and history.
Rebind requires `workstream_id` and its last read `expected_revision`. Failed
setup calls roll back attachment and workstream changes together. An exact
binding reached by retry returns `ready` without duplicating state. New
workstreams take an explicit `scope_expression` (default `none`); no project
relationship or scope is inferred from directory/branch names. `list_projects`
is a global administrative catalog, not a current-project selector. The old
setup primitives and `preflight` remain for compatibility.
When a new workstream snapshots an existing workstream as its first scope
expression term, pass that source's last read revision as `expected_revision`.

`list_workstreams(project?, limit?, offset?)` lists registered workstreams
globally or within one project, with binding, revision and derived scoped task
counts. `workstream_status(workstream_id, limit?, offset?)` returns that
workstream's compact scoped queue and count diagnostics without init or rebind.
Its `counts` are disjoint task views (ready, pending acceptance, unresolved,
prerequisites, review, sign-off, done, deferred, dropped). They count only
scoped tasks and groups; `scoped_count` equals their sum.
The separate
`overlapping_gate_diagnostics` counts can overlap for tasks with several gates.
The single `view` shown in `list_tasks` and status drill-down prioritizes
terminal disposition, then review/sign-off, then acceptance/unresolved/
prerequisite gates; use diagnostics to see every simultaneous gate.
`recorded_state=registered` means only that the binding exists;
`agent_liveness=unknown` explicitly makes no running-agent claim. IDs support
durable resume; offset pages are for browsing current state, not a snapshot of a
changing board. Completed tasks in different repositories remain separate;
these views do not claim an integrated feature is complete.

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

An active-session coordinator can accept a gate proposal, or dismiss it with a
decision note when the proposal is stale or unwanted. Dismissal removes the
pending proposal without applying its gate, advances the task revision, and
preserves the proposal and decision in the audit history. It remains possible
after a proposed prerequisite target is dropped. A completed task is immutable,
including its pending proposals.

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
| Project and workstream | `init`, `list_projects`, `list_workstreams`, `workstream_status`; compatibility: `init_project`, `attach_checkout`, `init_workstream`, `rebind_workstream`, `preflight` |
| Scope and queue | `set_scope`, `list_tasks`, `get_tasks`, `reorder_tasks`, `get_next_task` |
| Specification and gates | `create_task`, `update_task`, `accept_task`, `set_disposition`, `add_unresolved`, `resolve_unresolved`, `add_prerequisite`, `propose_prerequisite`, `accept_gate_proposal`, `dismiss_gate_proposal`, `decompose_task` |
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
