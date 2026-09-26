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
No network listener is needed for stdio. Connect this command in each client's own MCP
configuration; Task MCP does not install its connection.

## Local browser workspace

An optional browser viewer/editor works without an agent or model:

```sh
.venv/bin/task-mcp ui
# Select a different database explicitly at launch:
.venv/bin/task-mcp ui --db /absolute/path/tasks.sqlite3
# Stop that database's viewer:
.venv/bin/task-mcp ui --db /absolute/path/tasks.sqlite3 --stop
```

Open the private link printed by the command. Repeated launches reuse the live
viewer for that database. The explicit `open_task_viewer` MCP tool returns the
same link for its configured database; it does not change client approvals or
open a browser automatically. The listener stays running after the CLI or MCP
session ends, until **Stop viewer**, `ui --stop`, or the process/machine stops.
There is no automatic startup or installed OS service. The current launcher
uses POSIX file locking (Linux/macOS).

Browse projects and workstreams, search/filter task titles, inspect global
groups, and read specifications, evidence and audit history. Dedicated dialogs
record creation, edits, acceptance, questions, defer/resume/drop, human review
and sign-off. Concurrent changes retain your draft and offer reconciliation.
The app uses the existing Store and database; it has no synchronized copy.
Task text is displayed as safe, whitespace-preserving text, including Markdown
source. Full task setup, result recording and uncommon workflow operations
remain available through MCP.

The listener binds only `127.0.0.1` on an ephemeral port. Its private launch link
is a bearer credential: keep it private. Host/Origin checks, a custom token
header, strict content policy and no external assets protect browser access.
The token is removed from the address bar and kept in per-tab session storage.
Reads still append Store audit events. Whole-server pre-approval now includes
the ability to explicitly launch this local listener and expose the configured
database to a token-holding browser; it is not a read-only viewer. Nothing here
changes Codex's approval settings.

For a disposable visual demo, run `.venv/bin/python examples/viewer_demo.py`.
See [viewer usage, security and lifecycle](docs/viewer.md) for details.

## Performance and client approvals

Measure the server separately from the client that hosts it:

```sh
.venv/bin/python examples/benchmark.py --samples 20
```

The benchmark uses disposable databases and compares direct Store calls with
real subprocess stdio MCP calls. `--source /absolute/path/tasks.sqlite3` measures
SQLite-consistent, read-only-source backups; `--project ID --workstream ID`
select an existing scope in those copies. `--work-dir /existing/directory`
places the disposable copies on a chosen filesystem. Startup and per-call
samples are reported separately. It never benchmarks writes against the source
database or changes the client's approval policy.

Tool annotations describe actual behavior, not a performance preference.
Creation, additive membership/gates and new result attempts are non-destructive
writes. Overwrites, removals, lifecycle decisions and `init` (which can rebind an
existing workstream) retain destructive annotations. Audited reads are still
declared writes because they append events; only the static skill catalog is
read-only. Revision checks, workflow gates and audit history apply regardless
of the client's approval decisions.

In dogfooding, local mutations took milliseconds while the host's synchronous
approval review added seconds before execution. Accurate additive annotations
avoid incorrectly classifying creation as destruction; they do not guarantee
that a client will omit approval, nor eliminate approval for genuine edits.
See [the performance investigation](docs/performance.md) for measurements and
the distinction between server validation and end-to-end verification. After
an annotation change, an existing client connection must refresh its discovered
tool descriptors; a fast fresh subprocess alone does not verify that refresh.

For diagnosis and optional, user-chosen per-tool or whole-server pre-approval,
see [troubleshooting slow client calls](docs/performance.md#troubleshooting-slow-client-calls).
Those settings belong to the client, not the task database, and never replace
the workflow's required user decisions or human sign-off.

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
concrete tasks owned by that workstream's project; `scoped_count` equals their
sum. `groups` and `referenced_groups` identify explicitly included groups and
their whole-group progress separately, even when this project has no members.
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

Workstream scope is an explicit set of local concrete tasks and group references. `none` begins
empty. A workstream name or ID as the first expression term snapshots its scope.
`+task-id` and `-task-id` then add or remove references. Titles are accepted
only when unambiguous. A group reference remains live: later group members from
the workstream's own project enter its scoped queue, but begin pending acceptance.
Members from other projects never enter the local queue. An explicit `-task-id`
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
complete. An empty group is incomplete and mutable. `create_group(workstream_id,
title, ...)` creates the same kind of group directly, without selecting a home
project; it is included in that workstream's explicit group scope. The group has
one global ID and can hold concrete tasks from several projects. `list_groups`
discovers it globally or through any member/scoped project; `get_tasks` on its ID
shows every member's project and whole-group progress. All public group details
show `project_id=null`; `origin_project_id` is optional legacy provenance for
groups created before this storage change, not an ownership boundary.

Pass `group_id` and the group's last read `group_expected_revision` to
`create_task` to create a new member in its own project, or call
`add_group_member` with both the group and existing task revisions. Membership
changes are atomic. Existing members keep their independent acceptance,
implementation, review and sign-off. A group becomes complete only when it has
at least one member and every member is signed off; pending, deferred or dropped
members keep it incomplete. Completed groups cannot gain members or be edited.
A concrete task may depend on a group in another project, and that gate clears
only on whole-group completion; concrete task-to-task prerequisites remain within
one project. Resolve unresolved items and proposed gates before decomposition;
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

For a feature whose design is still open, use the two-phase path:

```text
init → feature-capture → feature-design → accepted implementation → review → human sign-off
```

Say **"add a design task for X"** or **"capture this feature for later"** to use
`feature-capture`. The agent does bounded preliminary research and saves the
desired outcome, motivation, current context, tentative scope, assumptions,
possible directions and material open questions. You do not need to answer all
of those questions during capture. The ordinary task starts pending and gets a
`Feature design required (feature-design): ...` unresolved item; neither the
capture request nor the intermediate create call makes it implementation-ready.

Say **"let's design X"**, **"review design tasks"**, or **"designrev"** to use
`feature-design`. The agent refreshes its code understanding, explains what
exists, compares approaches and tradeoffs, recommends a path, and works through
decisions with you. It saves the resulting specification and acceptance
criteria, preserving unsettled questions. Larger features can become a group of
concrete implementation tasks. Acceptance records your informed decision on
the exact resulting scope; design discussion alone does not authorize building
it. An already given decision is sufficient when it covers that scope.
"Review design tasks" includes captured ideas in the project inbox by default;
an explicit workstream request narrows that search. Accepted inbox work enters
a workstream only when placement is part of the agreed plan.

"Design task" is conversational shorthand for this workflow, not a stored task
type. The design-gate prefix is a readable skill convention, not parsed server
metadata. Pending briefs can display as `pending_acceptance` even with a design
gate; the design skill reads their details as well as tasks in `unresolved_items`.
Ordinary proposals and unrelated blockers still use `proposal-review`.

The canonical reference skill files are packaged under
`src/task_mcp/reference_skills/`. `get_default_skills` returns their exact text,
version and SHA-256 hashes. MCP startup instructions and the catalog tool route
these phrases to the matching skill, so a fresh connected session can fetch and
follow it without prior chat history or installing client skills. Existing
connections may need to reconnect to receive changed server instructions.
Agents may also use the primitives directly. Copying
them to a client's native skill location requires explicit user authorization;
the server does not install or update clients. Single-agent devloop and guided
task flows are possible using the same primitives, but reference adapters for
those rare workflows are deferred.

## MCP tools

| Area | Tools |
| --- | --- |
| Project and workstream | `init`, `list_projects`, `list_workstreams`, `workstream_status`; compatibility: `init_project`, `attach_checkout`, `init_workstream`, `rebind_workstream`, `preflight` |
| Scope and queue | `set_scope`, `list_tasks`, `get_tasks`, `reorder_tasks`, `get_next_task` |
| Groups | `create_group`, `list_groups`, `add_group_member`, `decompose_task` |
| Specification and gates | `create_task`, `update_task`, `accept_task`, `set_disposition`, `add_unresolved`, `resolve_unresolved`, `add_prerequisite`, `propose_prerequisite`, `accept_gate_proposal`, `dismiss_gate_proposal`, `decompose_task` |
| Delivery | `record_result`, `record_review`, `human_review`, `signoff_task` |
| Inspection | `list_events`, `export_workstream`, `get_default_skills` |
| Local browser | `open_task_viewer` (explicit loopback listener/editor launch) |

Mutations that change a task or workstream require the last revision read.
`reorder_tasks` takes the previously read complete project order as
`expected_order` for the same concurrency check.
Concurrent writes to one revision permit one winner and return
`revision_conflict` to the other. Task creation is not deduplicated: inspect the
board before retrying an uncertain response. Every handler call except the
static skill catalog appends a local audit event, including reads and domain
errors. Task mutations retain before/after snapshots. The configured actor label
is not authenticated. `list_events` supports a stable pagination ceiling.

## Text exports

`export_workstream(workstream_id, include_closed=true, format="markdown")`
returns a human-readable Markdown snapshot (`task-mcp/v2`) and its SHA-256 hash.
It starts with project/checkout identity and an ordered workflow overview, then
shows specifications, acceptance criteria, questions, prerequisites, evidence
and review history in text. Workflow labels match `list_tasks` for that
workstream: "Awaiting sign-off" is distinct from the stored "open" disposition.
Shared-group progress is global; detailed task entries stay in the local scope.

The output is deterministic for unchanged state and is export-only: editing it
does not update the service. The CLI prints exactly the same content to stdout:

```sh
.venv/bin/task-mcp --export-workstream wst_your_workstream_id
.venv/bin/task-mcp --export-workstream wst_your_workstream_id --exclude-closed
.venv/bin/task-mcp --export-workstream wst_your_workstream_id --export-format legacy
```

`include_closed=false` / `--exclude-closed` omits done and dropped tasks, not
deferred tasks. The previous embedded-JSON layout remains available as
`format="legacy"` / `--export-format legacy` (`task-mcp/v1`); consumers of that
layout should select it explicitly. Export itself never creates a file.
See [the format details](docs/exports.md) and a
[synthetic example report](docs/export-example.md).

The service transactionally upgrades the earlier task table to allow global
groups without project ownership, preserving existing IDs and related rows.
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
