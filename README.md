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
record creation, edits, branch queue placement or moves to the inbox,
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

The stdio server automatically collects bounded private usage traces for offline
evaluation. See [usage traces](docs/usage-traces.md) for `task-mcp trace-report`,
capture/retention settings and what the measurements can establish.

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
Init returns at most ten queue cards with total/next-page information. Task/group
lists and status queues default to twenty; events default to twenty metadata entries.
Workstream scope/group references are bounded; `workstream_status(include_scope=true)`
pages explicit members, group references and exclusions using limit/offset.
Its `counts` are disjoint task views (ready, unresolved,
prerequisites, review, sign-off, done, deferred, dropped). They count only
concrete tasks owned by that workstream's project; `scoped_count` equals their
sum. `groups` and `referenced_groups` identify explicitly included groups and
their whole-group progress separately, even when this project has no members.
The separate
`overlapping_gate_diagnostics` counts active gates on open/rework tasks and can overlap
for tasks with several gates. Queue `gate_diagnostics` uses the same active-gate
meaning: done, dropped and deferred tasks have an empty list. Deferred tasks'
gates become active again when resumed. Explicit `get_tasks(specification=true)` retains full requirements/gates;
`get_attempt`, paged `list_task_attempts` and audit events retain proof/history; retained
history does not create actionable gates on inactive tasks.
The single `view` shown in `list_tasks` and status drill-down prioritizes
terminal disposition, then inbox, review/sign-off and unresolved/
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

Workstream queues have one explicit owner per concrete task. `set_scope` supports
bulk placement: `none`, a workstream-base snapshot, and +/-task/group references.
Adding a group queues its current local members; future members keep their own
placement. A base snapshots its actual queue. Selected tasks move from their
previous branch; individual exclusions apply to that operation. Group references
are context, and remote members never enter a local branch's queue.

## Task lifecycle

There is one task model. Origin and `user_request` are descriptive metadata.
Create with a workstream to queue on that branch; omit it for the inbox. Queueing
an existing task moves it to that one workstream, and unqueueing moves it to the
inbox. Edits retain placement. Title/body/criteria requirement edits advance the
specification revision; older proof remains historical. A summary-only correction
preserves the spec revision. No-op edits preserve all revisions. Queue placement,
open questions, prerequisites, disposition and current-spec local attempts govern
readiness, without a separate purpose-decision gate.

`get_tasks(ids=[...])` returns bounded cards: title, optional summary, revisions,
queue placement/disposition, gate counts and references. Cards contain no body preview,
acceptance criteria or replacement token. Scoped cards expose a current-spec local
attempt reference; unscoped cards label aggregate counts and imply no branch readiness.
Call `get_tasks(ids=[...], specification=true, workstream_id=..., attempt_ids=[...])`
when you need complete current requirements and exactly chosen proof together.
No preliminary card read is needed. Pending proposals and parent context are complete;
current-spec attempt summaries are limited to three, actionable first, with totals
and `has_more`. Page additional attempts with `list_task_attempts(states=[...],
current_spec_only=true)`; `get_attempt(attempt_id)` retrieves one complete proof.
Explicit proof retains its original task, workstream and specification provenance.

Body and criteria are whole-field replacements. Use the `specification_etag` from
that full read or from a create acknowledgement for the complete specification you
authored. A valid token-bearing update returns its refreshed token. Unchanged specs
retain the same token. Other updates expose no token. Continue with the last returned
entity revision, even after a user pause; successful writes need no confirming read.
Fetch and reconcile only on conflicts, uncertainty or missing information. The token
is a stale-read/data-loss guard. Title and summary-only edits need no full-spec read.

Task/group `summary` is optional intent or settled constraints, at most 240 Unicode
characters on one line. Overlong, multiline and whitespace-only values fail; no
truncation or generated fallback exists. Omission preserves it; null clears it.
Summary-only edits advance the entity revision while retaining spec revision,
queue placement and proof, including completed tasks/groups. A later spec edit makes
`summary_stale=true`; simultaneous summary edits stamp the resulting spec revision.
Reaffirming the same stale text refreshes it; current identical text is a no-op.
Summary freshness does not certify correctness; full requirements remain authority.

Resolving an unresolved item,
changing order, or adding evidence does not change queue placement.
Unresolved items are live gates; resolve them after settling the matter and
record any material decision in the task description. A `blocked_by` prerequisite
clears by default once the blocker is done or has a current-spec passed
independent review (`passed`) or recorded human review (`human_review`). A task
may depend on a group; every member of a nonempty group must satisfy the
link's required milestone.

Use `decompose_task` to turn a task into a first-class group while retaining its
ID, description and audit history. The call atomically
creates required members in the parent queue, or inbox members from an inbox parent. Groups cannot nest, have no implementation
attempts or execution gates, and derive completion from all members being
complete. An empty group is incomplete and mutable. `create_group(workstream_id,
title, ...)` creates the same kind of group directly, without selecting a home
project; it is included in that workstream's explicit group scope. The group has
one global ID and can hold concrete tasks from several projects. `list_groups`
discovers it globally or through any member/scoped project; `get_tasks(specification=true)` on its ID
shows whole-group progress and at most three member references; `list_group_members`
pages every member's card. All public group details
show `project_id=null`; `origin_project_id` is optional legacy provenance for
groups created before this storage change, not an ownership boundary.

Pass `group_id` and the group's last read `group_expected_revision` to
`create_task` to create a new member in its own project, or call
`add_group_member` with both the group and existing task revisions. Membership
changes are atomic. Existing members keep their independent specifications,
implementation, review and sign-off. A group becomes complete only when it has
at least one member and every member is signed off; pending, deferred or dropped
members keep it incomplete. Completed groups cannot gain members or be edited.
A concrete task may depend on a canonical task or group in any project, without
shared-group membership. `add_prerequisite` and `propose_prerequisite` default
to `milestone="review"`, satisfied by done or any current-spec passed/human-reviewed
attempt, including another workstream. Use `milestone="signoff"` only as a rare
exception when proceeding before the user's verdict would very likely waste work;
it waits for done (every member for a group). Dropped/deferred work remains
unsatisfied, even with a retained passed review. Satisfaction is computed: rework
or a spec change with no current-spec satisfying attempt blocks review links
again; existing dependent results are retained. A canonical milestone does not
prove that another branch's code was integrated. Links never expand workstream
scope or transfer attempts, reviews or code. Self-edges and cycles through prerequisites
and implicit group-to-member completion edges fail atomically, including during
observer-proposal acceptance and membership changes. Linking changes the
dependent task revision, leaving both specifications and queue placement intact.
Use `remove_prerequisite(task_id, expected_revision, blocked_by_id, note)` for a
mistaken or obsolete link, with the dependent task's last revision and the actual
decision note. It removes either milestone link atomically, immediately recalculates
the gate, and audits the configured actor, note and removed link. A real deletion
advances the dependent revision once; an absent link returns `changed=false`
without advancing it. Stale revisions still fail, and completed tasks are immutable.
Removal preserves specifications, queue placement, attempts, reviews and other links.
Resolve unresolved items and proposed gates before decomposition;
existing prerequisites move to the concrete members. Use related
pending proposals for optional work. A session actively handling a task may add
an unresolved item or prerequisite. An observer can submit a nonblocking gate
proposal for an active session or the user to accept. `propose_prerequisite`
atomically creates and links a pending prerequisite in the current scope or
project inbox. The service records the asserted handling role but does not
authenticate agent identity.

Full task reads, scoped queues and project lists include compact `prerequisites`
references with ID, title, project ID/name, canonical `state`/`complete`, required
`milestone`, computed `satisfied` and `blocking` facts. `complete` still means
human-signed-off completion; `satisfied` can become true earlier. Workstream
diagnostics include the same link facts. Duplicate links with a different
milestone fail rather than silently replacing it. A global group's project
identity is null and its state is complete/incomplete. These references do not
expand remote specifications,
attempts, evidence, history or queues; use explicit task reads to inspect them.
The viewer renders these references directly and opens remote details only on
deliberate navigation. Verify actual IDs and meaning before replacing a known
prose gate: add the real links first, then resolve the old unresolved item.
There is no automatic prose parsing. Review milestone satisfaction does not
expand execution scope or inherit review proof into a local attempt.

An active-session coordinator can accept a gate proposal, or dismiss it with a
decision note when the proposal is stale or unwanted. Dismissal removes the
pending proposal without applying its gate, advances the task revision, and
preserves the proposal and decision in the audit history. It remains possible
after a proposed prerequisite target is dropped. A completed task is immutable,
including its pending proposals.

Reading or selecting a task does not create an attempt. The implementer calls
`record_result` only on leaving or recovering an actual durable result. Read the
full current spec, check the actual checkout/artifacts, and send the last-read
`expected_revision` and `specification_etag`, actual `implementer`, `summary`,
context `evidence`, concrete `artifacts=[{kind: "commit"|"artifact", reference: ...}]`
and `verification` describing actual checks/outcomes and limits. It records a local
unreviewed attempt even when queue, unresolved or prerequisite gates remain,
or the task is deferred/dropped. This factual record never grants approval,
resumes work, clears a gate or satisfies prerequisites. Groups and completed tasks
are protected. The concise ACK includes attempt `id`/`revision`, `task_revision`,
unchanged `queue_workstream_id`/`status` and gate diagnostics; retrieve full proof deliberately.
The server stores declarations; callers verify artifacts and reviewer provenance.
New proof uses a structured envelope in the existing evidence TEXT column, with
legacy text proof unchanged and no schema migration. Dispatch review actions to a
fresh independent reviewer and implementation actions to an implementer. Other
workstreams can produce independent attempts
on the same task. An independent reviewer named differently from the implementer
returns a verdict; the coordinator records it with `record_review`. A recorded
`human_review` can satisfy the review gate when the user actually reviews the
result or explicitly directs that further review is unnecessary. Agents must
not self-issue it. One informed `signoff_task` decision selects the exact reviewed attempt.
`approve` completes it; `rework` requests repair and fresh review; `revise` returns
to design with the reasons as an open question; `drop` closes without approval.
The single `reasons` field is required for rework/revise and optional for
approve/drop. Queue placement and factual history survive every verdict.
Deferral is an ordinary status change. The service stores the actual verdict and
reasons, without separate agent or quality judgments.
Completed tasks are immutable; changed requirements
become new tasks. The service records assertions and cannot authenticate
reviewer independence or the actual human verdict. Workflows must obtain and
record them truthfully.

Implementers and independent reviewers may supply `concerns=[{kind: "value"|"design", text: ...}]`
to `record_result` and `record_review`. A concern is a value or design doubt that
cannot be fixed without changing what the task says. Concerns never change gates,
review verdicts or prerequisite satisfaction. Omission preserves all existing
contributions; reviewer additions retain the implementer's concerns and label
both sources/authors. The write ACK returns a count, without concern prose.
`get_attempt` and chosen attempt proof show complete concerns. Full specification
reads show concern prose from the same three applicable attempt window as their
attempt summaries, with totals and `concerns_has_more`; proof included in the same
response carries its own concerns once. Older concerns remain available through
explicit attempt/history reads. `get_next_action` includes the chosen local
review proof and its implementer concerns in one call. `workstream_status` exposes
`concern_tasks`, a separate page at its `limit`/`offset`, prioritizing tasks awaiting
sign-off. These references/counts include only current-spec attempts on that
workstream. The viewer shows full concerns on results and in the sign-off dialog;
exports retain them in labelled attempt history.

The ordinary path is:

```text
init → proposal/unresolved review → superdevloop → human sign-off
```

For a feature whose design is still open, use the two-phase path:

```text
init → feature-capture → feature-design → queued implementation → review → human sign-off
```

An ordinary **"add a task to do X"** request can authorize a concrete specification
without another confirmation. Create it with the intended `workstream_id` to queue
it there, or call `queue_task` for an existing task. Queueing starts no implementation.
Explicit inbox/design-first requests take precedence. Agent-suggested additions
and material unresolved scope stay in the inbox with appropriate design gates.

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
concrete implementation tasks. Queue the exact resulting scope only when the user's decision covers building it
on that branch. Design discussion alone does not authorize implementation. Keep
unsettled questions blocking, and retain inbox placement when the user only asked
to design. Queue moves are one atomic action; no separate scope step is needed.

"Design task" is conversational shorthand for this workflow, not a stored task
type. The design-gate prefix is a readable skill convention, not parsed server
metadata. Inbox briefs display as `inbox` even with a design
gate; the design skill reads their details as well as tasks in `unresolved_items`.
Ordinary proposals and unrelated blockers still use `proposal-review`.

The canonical reference skill files are packaged under
`src/task_mcp/reference_skills/`. `get_default_skills()` returns a names/versions/hashes/descriptions index;
`get_default_skills(name="feature-design")` returns that complete skill in one call. MCP startup instructions and the catalog tool route
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
| Scope and queue | `queue_task`, `unqueue_task`, `set_scope`, `list_tasks`, `get_tasks`, `list_task_attempts`, `get_attempt`, `reorder_tasks`, `get_next_action` |
| Groups | `create_group`, `list_groups`, `list_group_members`, `add_group_member`, `decompose_task` |
| Specification and gates | `create_task`, `update_task`, `set_disposition`, `add_unresolved`, `resolve_unresolved`, `add_prerequisite`, `remove_prerequisite`, `propose_prerequisite`, `accept_gate_proposal`, `dismiss_gate_proposal`, `decompose_task` |
| Delivery | `record_result`, `record_review`, `human_review`, `signoff_task` |
| Inspection | `list_events`, `export_workstream`, `get_default_skills`, `runtime_info` |
| Local browser | `open_task_viewer` (explicit loopback listener/editor launch) |

Mutations that change a task or workstream use the last returned entity revision, checked atomically; user pauses do not invalidate it.
Board/queue responses (`list_tasks`, `workstream_status`, successful `init`,
`get_next_action`) expose `project_order_revision`. Move one task immediately before
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
Ordering changes no task/spec revisions, queue placement or proof, even when completed
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
returns a human-readable Markdown snapshot (`task-mcp/v5`) and its SHA-256 hash.
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

Creation, amendments and queue/unqueue return compact IDs, task/spec revisions,
`queue_workstream_id`, disposition and gate diagnostics. Create with a workstream
to queue there; omit it for the inbox. Queueing on another branch moves ownership.
Edits preserve placement. Actual requirement changes advance `spec_revision`, so
older attempts/reviews remain historical and cannot satisfy current-spec signoff.
Whole-field body/criteria edits require the full `specification_etag`. Failed
revision checks or audit writes roll back atomically; unchanged patches and
placement no-ops preserve revisions. Fetch full requirements and chosen proof with
`get_tasks(specification=true, attempt_ids=[...])` only when needed.

`set_scope` is bulk queue placement using `none`, a workstream base, and +/-task/group.
Adding a group snapshots its current local members. Future membership does not
change queues. A base snapshots actual ownership, and selected tasks move from
other branches. Explicit exclusions apply to that operation. Group references
remain context; only concrete tasks can be queued. Decomposition transfers a
queued parent's ownership to its new members. Completed placement is immutable;
a bulk move that would alter a completed task fails entirely. A real queue move
advances its task revision and affected workstream revisions once. Bulk operations
advance every changed task and affected workstream revision once.

Protocol 10/schema 6 replace the separate purpose-decision tools and payloads
with `queue_task`/`unqueue_task`; the catalog advertises 42 tools. The reference
catalog is 1.14.0, and Markdown export is `task-mcp/v5`. Schema 6 uses
`queue_members` with unique task ownership. Migration moves scoped mutable work
without a current-spec legacy decision into the inbox. Multiple eligible scopes
choose the most recent attempt's workstream, then oldest workstream creation time
and ID. Completed rows, selected proof, all attempts and events remain unchanged.
Private `legacy_queue_migration` archives every original authority/scope fact and
reports candidates, owner and reason. Legacy task authority columns and
`scope_members`/`scope_exclusions` remain frozen private history, never current
payloads or execution gates. Earlier schema migrations retain descriptive origin,
summary, shared ordering and review/signoff prerequisite milestones.

Before upgrading an existing database, the service takes and verifies a fresh
private SQLite online backup under its writer lock, including committed WAL data.
The adjacent `*.pre-schema-6.*.sqlite3` backup survives success or rollback.
Failure rolls back schema and data. Stop all writers before restoring a verified
backup with SQLite's backup API; reconnect only matching protocol/schema clients.
Never run an older server against schema 6 or overwrite active writers. Test on
copied/disposable databases and coordinate code/client/schema rollout together.

`signoff_task` accepts exactly `approve`, `rework`, `revise` and `drop`. Send the
actual user's `reasons`, exact reviewed `attempt_id`, and last returned task and
attempt revisions. Reasons are required for rework/revise, optional for
approve/drop. Current-spec passed/human-reviewed proof is required; approve also
needs clear unresolved and prerequisite gates. Rework returns the attempt to
implementation and requires fresh review. Revise copies the reasons into an open
question without inventing a specification revision. Drop closes without approval;
creating a removal task when delivered code must go is a workflow decision.
Deferring uses `set_disposition`, rather than a signoff verdict. Leaving `dropped`
needs actual revival `authorization`. Completed work stays immutable.

Protocol 11/schema 7 simplify that contract and expose `latest_rejection` on full
task reads and `get_next_action`: source (`review`/`signoff`), verdict, reasons,
originating attempt/workstream/specification, timestamp and decision reference.
Each actual reviewer rework or signoff rework/revise replaces that context; later
passes and approvals do not fabricate a new rejection. Compact cards omit the
reasons and retain the provenance flag. Current execution/proof remains local to
its branch. Earlier rejection rounds remain in audit/signoff history, including
old defer decisions and judgment fields unchanged. Schema 7 adds only an indexed
projection over factual audit records, with no business-row rewrite. Existing
databases use the same verified online backup/transactional migration mechanism,
now with a `*.pre-schema-7.*.sqlite3` backup. Markdown export v5 includes latest
rejection context and signoff history. The reference workflow skill update is a
separate change.

## Deferred work

V1 does not import TASKS.md, support nested groups, per-workstream ordering,
persistent dynamic scope filters, mandatory claims or leases, dedicated native
client packages, automatic client configuration, or editable export
synchronization. A complete Codex dogfood trial and then Claude Code, OpenCode
and Pi access/catalog validation remain product proof beyond the unit and stdio
suite. See [DESIGN.md](DESIGN.md) for the current design and boundary.

Protocol 12 retains schema 7 and the same 42 tools. Optional concerns live in the
existing attempt evidence envelope, with no database migration. Legacy context
is preserved exactly when reviewer concerns require an envelope; omitted or empty
concerns leave stored evidence untouched. Refresh client discovery to see the
optional inputs and concern projections. The fuller workflow skills guidance is
separate from this tool contract.
