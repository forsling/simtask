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

## Runtime identity and reconnecting

`runtime_info()` is a read-only diagnostic that writes no task state or audit
event. Every successful `init` response also includes the same identity under
`runtime`. It reports package version, a startup-frozen SHA-256 source identifier,
Task MCP protocol/schema revision, process startup timestamp and PID, interpreter
and package paths, and the database's persisted schema revision. Use it after a
server update to verify which runtime the client actually reached.

An editable install updates files on disk; an existing Python process retains
its imported code. A client can also retain an old tool catalog after a new
process starts. These require different checks and both can happen together.
See [runtime fields and the reconnect procedure](docs/runtime.md). Reinstalling,
toggling a connection, or passing a fresh subprocess demo alone does not prove
that the existing client has refreshed its process and discovered tools.

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

Browse projects and workstreams, search/filter task titles, and read
specifications, evidence and audit history. Each project's **Task groups** page shows
its discoverable groups, including shared groups and empty groups included in
that project's scope. **Shared task groups** shows only groups with task members in
more than one project. Both show whole-group progress and project counts;
**Project group**, **Shared group** and **Empty group** labels distinguish them.
Group details list workstreams that explicitly include the group, with
project/branch links to their scoped queues. Independently scoped member tasks
do not imply that the whole group is included in a workstream.
Dedicated dialogs
record draft or explicitly approved creation, edits, acceptance or withdrawal,
questions, defer/resume/drop, human review and sign-off. Concurrent changes retain your draft and offer reconciliation.
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
declared writes because they append events; the static skill catalog and runtime
diagnostic are read-only. Revision checks, workflow gates and audit history apply
regardless of the client's approval decisions.

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
`overlapping_gate_diagnostics` counts active gates on open/rework tasks and can overlap
for tasks with several gates. Queue `gate_diagnostics` uses the same active-gate
meaning: done, dropped and deferred tasks have an empty list. Deferred tasks'
gates become active again when resumed. Full `get_tasks` details and audit events
retain unresolved items, prerequisite links and attempt/review history; retained
history does not create actionable gates on inactive tasks.
The single `view` shown in `list_tasks` and status drill-down prioritizes
terminal disposition, then acceptance, review/sign-off and unresolved/
prerequisite gates; use diagnostics to see every simultaneous gate.
`recorded_state=registered` means only that the binding exists;
`agent_liveness=not_tracked` means the service intentionally does not observe
agents, run heartbeats or maintain leases; it is not a failed observation. Recorded
task views and active gates describe durable workflow state, not running agents. IDs support
durable resume; offset pages are for browsing current state, not a snapshot of a
changing board. Completed tasks in different repositories remain separate;
these views do not claim an integrated feature is complete.

Continue on the same durable workstream ID. Before relying on recorded proof,
check the actual checkout, affected files and commits against the current full
specification, especially after rebinding. Another workstream's implementation
does not gate local alternatives. Deliberate merge/cherry-pick integration of an
open task uses a new target-local `record_result` with origin attempt/workstream,
source/target commits and target verification, followed by fresh independent
review. Completed proof stays immutable; later integration needs a new task.
See [continuation and integration guidance](docs/continuation.md).

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
another workstream by creating it. Project order is shared across workstreams;
each filters it by explicit scope and branch-local eligibility. Preconditions
remain hard gates. `get_next_task` returns the first full eligible task without
claiming it, or compact reasons why the scope has no eligible work.

## Task lifecycle

There is one task model. Origin (`source="user"`, `"agent"` or `"unknown"`) and
`user_request` are descriptive, persisted independently even on pending tasks.
They never accept a specification. `create_task` without `approval` creates
pending work; supplying `approval={"basis": "specific", "note": "actual supporting
instruction"}` atomically accepts that exact created specification. `specific`
means the user's request/decision covers the exact scope; `delegated` means real
authority to select work within a stated goal, recorded in the note. New approval
must be classified and have a nonempty note. Standalone
`accept_task(task_id, expected_revision, approval)` uses the same payload.
`update_task(task_id, expected_revision, changes, approval?, specification_etag?)`
saves a specification amendment and its optional approval in one transaction.
Title, body and acceptance criteria changes advance the spec revision; without
approval the new spec becomes pending. Supply the same specific/delegated
payload when an actual decision already covers the resulting exact scope. An
unchanged patch is a no-op unless approval changes; approving an unchanged
pending spec advances only the task revision. No edit is inferred to be editorial.

Body and criteria are whole-field replacements. First call `get_tasks(ids=[...])`
for the complete spec and pass its `specification_etag` with the current task
revision. The token binds task ID, spec revision, full body and criteria; it is a
stale-read/data-loss guard, not authority. Title-only edits need no full read.
On conflict, re-read the full spec and reconcile before retrying. Full detail
remains the `get_tasks` default; there is no `specification=true` flag. The planned
summary-only field is separate future work and is not available in this version.
Resolving an unresolved item,
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
A concrete task may depend on a canonical task or group in any project, without
shared-group membership. Concrete blockers clear only when human-signed-off
done; a group clears only when every member is done. Review alone, deferred and
dropped work do not satisfy completion. This is ledger completion, not proof
that another branch's code was integrated. Links never expand workstream scope
or transfer attempts, reviews or code. Self-edges and cycles through prerequisites
and implicit group-to-member completion edges fail atomically, including during
observer-proposal acceptance and membership changes. Linking changes the
dependent task revision, leaving both specifications and their acceptance intact.
Resolve unresolved items and proposed gates before decomposition;
existing prerequisites move to the concrete members. Use related
pending proposals for optional work. A session actively handling a task may add
an unresolved item or prerequisite. An observer can submit a nonblocking gate
proposal for an active session or the user to accept. `propose_prerequisite`
atomically creates and links a pending prerequisite in the current scope or
project inbox. The service records the asserted handling role but does not
authenticate agent identity.

Full task reads, scoped queues and project lists include compact `prerequisites`
references with ID, title, project ID/name, canonical `state`, `complete` and
`blocking` facts. A global group's project identity is null and its state is
complete/incomplete. These references do not expand remote specifications,
attempts, evidence, history or queues; use explicit task reads to inspect them.
The viewer renders these references directly and opens remote details only on
deliberate navigation. Verify actual IDs and meaning before replacing a known
prose gate: add the real links first, then resolve the old unresolved item.
There is no automatic prose parsing. Evaluate the retained human-sign-off
milestone after rollout through existing usage/audit evidence, without routine
reporting calls or a new completion milestone.

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
not self-issue it. One informed `signoff_task` decision judges task purpose and
result separately and selects the exact reviewed attempt. `approve` completes the
canonical task; `rework` retains purpose approval and returns the implementation
for repair and fresh review; `revise` withdraws approval and opens a concrete
specification question without pretending requirements were edited; `drop`
withdraws approval while keeping proof; `defer` retains approval while paused.
Direction-only decisions leave human technical quality unjudged unless a
separate actual judgment is supplied. Specific purpose approval can be reused;
delegated/unknown cases need an actual purpose judgment at signoff. One informed
approval can cover both. Completed tasks are immutable; changed requirements
become new tasks. The service records assertions and cannot authenticate
reviewer independence or the actual human verdict. Workflows must obtain and
record them truthfully.

The ordinary path is:

```text
init → proposal/unresolved review → superdevloop → human sign-off
```

For a feature whose design is still open, use the two-phase path:

```text
init → feature-capture → feature-design → accepted implementation → review → human sign-off
```

An ordinary **"add a task to do X"** request can authorize a concrete specification
without a separate acceptance keyword or another approval. Record that scope
accepted when it faithfully reflects your request; queueing it does not start
implementation. Explicit requests to leave work unaccepted take precedence.
Agent-suggested additions and ideas with material unresolved scope stay pending.

Say **"add a design task for X"** or ask to save an exploratory idea to use
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
| Specification and gates | `create_task`, `update_task`, `accept_task`, `withdraw_acceptance`, `set_disposition`, `add_unresolved`, `resolve_unresolved`, `add_prerequisite`, `propose_prerequisite`, `accept_gate_proposal`, `dismiss_gate_proposal`, `decompose_task` |
| Delivery | `record_result`, `record_review`, `human_review`, `signoff_task` |
| Inspection | `list_events`, `export_workstream`, `get_default_skills`, `runtime_info` |
| Local browser | `open_task_viewer` (explicit loopback listener/editor launch) |

Mutations that change a task or workstream require the last revision read.
Board/queue responses (`list_tasks`, `workstream_status`, successful `init`,
`get_next_task`) expose `project_order_revision`. Move one task immediately before
or after a concrete task in the same project:

```text
reorder_tasks(project, task_id, anchor_id, position="before"|"after",
              expected_order_revision=board.project_order_revision,
              instruction="actual supporting scheduling instruction/authority")
```

The compact acknowledgement contains project/task/anchor IDs, the order revision
and `changed`. Stale moves, self-anchors and invalid/cross-project/group anchors
fail atomically. A move already in place returns `changed:false` and keeps the
order revision. Successful moves advance it once; new tasks append at the end
and advance it on insertion. Decomposition also advances it for removal of the
concrete parent and each new member. Migrated projects begin at order revision 0.
Ordering changes no task/spec revisions, acceptance or proof, even when completed
rows shift position. Every workstream sees the same order through its own scope;
local attempts and all execution gates remain local. Reads and selection never
reorder or normalize work. Use moves only for actual scheduling intent, recorded
alongside the configured audit actor (which is not authenticated). Assess
excessive use after rollout with existing audit events, without routine reporting
or automatic reshuffling. The browser's **Move in project order** form uses the
same anchor contract and preserves the draft on conflict for explicit review.

Concurrent writes to one revision permit one winner and return
`revision_conflict` to the other. Task creation is not deduplicated: inspect the
board before retrying an uncertain response. Every handler call except the
static skill catalog and runtime diagnostic appends a local audit event, including
reads and domain errors. Task mutations retain before/after snapshots. The configured actor label
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

Creation, amendment, acceptance and withdrawal return compact acknowledgements with IDs,
task/spec revisions, acceptance, disposition and applicable gate diagnostics;
creation also returns changed workstream/group revisions for continuation.
Amendments return `changed`, `spec_changed` and `approval_changed`; the latter
includes activation/invalidation of current approval or a changed approval
basis/note. No-op acknowledgements retain the current revisions.
Fetch `get_tasks` for full specification and proof. Approval does not clear
unresolved/prerequisite/disposition/scope gates or begin implementation. Correct
mistaken acceptance with `withdraw_acceptance(task_id, expected_revision, note)`:
the reason is audited, the specification and its revision remain unchanged,
and prior decisions, attempts and reviews survive. Withdrawal gates both result
recording and sign-off. Reapproving the same spec may reuse an applicable review;
a real spec change leaves prior attempts tied to their original revision.
Completed tasks remain immutable.

Database schema revision 2 separates origin from approval. Schema revision 3
adds `projects.order_revision` for shared-order concurrency, with a default of 0
on existing projects. Protocol revision 4 replaces whole-order
replacement with the atomic move contract. Current candidate protocol 5 adds
global concrete prerequisites and compact prerequisite references; this uses the
existing global foreign keys and requires no new schema migration.
Protocol 3's purpose/result signoff
judgments continue to use existing immutable audit records.
Migration preserves all existing rows, IDs, notes, acceptance/completion and history. Legacy origin
and unclassified approval are `unknown`; old audit text is never parsed to infer
authority. Before upgrading an existing schema 0/1/2 database, the service takes a
fresh SQLite online backup under the migration writer lock and verifies its
integrity, foreign keys and source revision. The private adjacent
`*.pre-schema-3.*.sqlite3` backup is retained; failure aborts the transaction.
An empty new database needs no migration backup. For rollback, stop all writers
before restoring a verified backup with SQLite's backup API, including WAL
state; reconnect clients only to the matching protocol/schema revision. Do not
run an older server against schema 3 or overlay a backup onto active writers.
Test candidate upgrades on disposable copies and keep incompatible code, client
workflows and databases isolated until every serving client can be refreshed.

Signoff uses one `decision` (`approve`, `rework`, `revise`, `drop`, `defer`),
not a second rejection selector. Send the actual user's `user_note`, exact
`attempt_id`, last-read task `expected_revision` and `expected_attempt_revision`.
Present purpose/approval basis and its actual supporting `approval_decision`
separately from result/review evidence. Specific approval can be reused;
delegated/unknown cases need an actual purpose judgment, which an informed
`approve` or `rework` decision covers without a second confirmation. Direction
changes leave human technical quality `not_judged`; only a separately supplied
actual judgment uses optional `result_judgment` plus `result_note`. Independent
review remains distinct. `revise` requires a concrete `specification_question`
and advances no spec revision until a real edit. Approval needs current
acceptance, current-spec passed/human-reviewed result and clear completion gates.

Signoff and `set_disposition` return compact continuation state; use `get_tasks`
for complete proof, approval decision references and structured
`signoff_decisions`, or detailed audit reads for historical snapshots. Drop
clears active approval and preserves proof; defer keeps approval. Ordinary
status changes need no reviewed result. Leaving dropped status requires actual
`authorization`; restoration leaves approval inactive until explicitly accepted.
Dropped/deferred tasks never satisfy done prerequisites. Completed work remains
immutable.

## Deferred work

V1 does not import TASKS.md, support nested groups, per-workstream ordering,
persistent dynamic scope filters, mandatory claims or leases, dedicated native
client packages, automatic client configuration, or editable export
synchronization. A complete Codex dogfood trial and then Claude Code, OpenCode
and Pi access/catalog validation remain product proof beyond the unit and stdio
suite. See [DESIGN.md](DESIGN.md) for the current design and boundary.
