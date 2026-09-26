# Task MCP design

Task MCP is an opt-in, local task-state service. It owns durable task state,
workstream scope, concurrency checks, deterministic queries, and audit history.
It does not run agents or authenticate users. Reference workflow skills provide
the recommended orchestration, while raw MCP calls and custom skills remain
usable.

## Identity and scope

Projects are initialized explicitly at a canonical path. A project may attach
other checkout paths. Each workstream has a durable ID and a mutable branch or
explicit name binding. Each session calls `init` with its explicit target path
and branch/name; an exact binding returns its scoped queue idempotently.
Discovery returns `new_branch`, `unregistered_checkout`, or `mismatch` when
setup needs a choice. Confirmed create, attach and rebind actions are atomic;
rebind checks the workstream revision and preserves its scope and history. The
old preflight and setup primitives remain available to existing clients. A new
workstream chooses its scope explicitly; branch names and Git history do not
imply scope. There is no cwd fallback to the sole project.

A session can retain multiple returned workstream IDs, one per repository or
branch as needed. Separate repositories keep independent task specifications,
attempts, reviews and sign-off. No global current-project state or persisted
session object is needed. Global `list_workstreams` can be project-filtered and
paged; `workstream_status` drills into one scoped queue without binding the
caller. Workstream summaries expose registered identity/binding/revision and
disjoint task view counts, plus explicitly overlapping gate diagnostics. They
make no claim about agent liveness or integrated feature completion. Page
offsets browse current state; stable IDs support durable resumption.

Concrete tasks are canonical project objects. One global group model holds
context and membership across projects. A workstream's explicit scope consists
of local tasks, group references, and exclusions. A group inclusion is live:
future members of the workstream's own project enter it unless individually excluded. An
explicit exclusion takes precedence over group expansion. A workstream can
snapshot another workstream's current expression, then add or remove references.
Project task order is global and advisory; scope and prerequisites are gates.
Every explicit scope change increments the workstream revision atomically.
Local queues, status counts and exports include only concrete tasks from the
workstream's project. Group references and whole-group progress are separate;
an empty local slice does not imply global completion.

## Tasks and groups

An agent-created task begins pending. A user-requested task can be created
accepted when the request is recorded. Acceptance binds to the current task
specification. Editing the title, description, or acceptance criteria
invalidates it; clearing an unresolved item or recording evidence does not.
Readiness is derived from acceptance, unresolved items, prerequisites, scope,
and implementation attempts. Completed tasks are immutable. A new requirement
after completion is a new task.

Groups store overarching context and aggregate completion. They are never
implementable and carry no unresolved items, disposition, attempts, or execution
gates. `create_group` creates an empty globally identified group in the caller's
workstream scope; it does not require a home project. `add_group_member` or
`create_task(group_id=..., group_expected_revision=...)` attaches a concrete
task from any project with revision checks. `list_groups` discovers one group
globally or through member/scoped projects. Existing decomposition converts a
concrete task in place after resolving its unresolved items and proposals;
existing prerequisites move to the new concrete members. Legacy group IDs and
their stored project origins survive migration, but origin is metadata rather
than task ownership. Public group details expose `project_id=null` and optional
`origin_project_id` provenance for old rows. All groups use the same membership,
scope and completion rules.

An empty group remains incomplete and mutable. A nonempty group completes only
when every member is signed off; completed groups are immutable. Each member
keeps its own accepted specification, attempt, review and sign-off. A concrete
task may depend on whole-group completion, including a group with members in
other projects; direct concrete task prerequisites remain project-local. Cycle
checks include prerequisite links and implicit group-to-member completion
edges. SQLite write serialization and revision checks protect a race between
membership changes and last-member sign-off.

## Delivery and authority

Reading/selecting a task does not claim it. An implementer creates an attempt
only when recording a durable result. Parallel workstreams may produce
alternative attempts on one canonical task. An independent declared reviewer
or an explicit human review must pass an attempt before human sign-off. Sign-off
selects a reviewed attempt. Rejection chooses rework on the current accepted
specification or revision that invalidates acceptance and adds an unresolved
item. The service cannot authenticate the reviewer or the user's verdict;
workflow clients must obtain and accurately record those decisions.

An active handler may add a task gate directly. An observer proposes a gate for
review; the proposal is nonblocking until accepted. A coordinator may dismiss
an unwanted or stale proposal with an audited reason, including when its target
was later dropped. The default is agent autonomy. Unresolved items are for
questions that materially risk wasted work,
expand authorization, require user-only information, or exhaust normal recovery.
Cheap research and reversible choices proceed without ceremony.

## Surface and boundary

The optional local browser companion invokes a narrow allowlist of the same
Store operations as MCP. It adds no task state model, business transition
logic, synchronization or agent dependency. Project/workstream queues retain
their derived views; stored disposition and global group progress are labelled
separately. Human decision dialogs require a note and explicit confirmation;
attempt review revisions and task sign-off revisions remain distinct. Text is
rendered as text, never trusted HTML. Failed concurrent writes preserve drafts
and require explicit reconciliation with the current version.

Only an explicit CLI `ui` command or `open_task_viewer` MCP call launches the
companion. A POSIX lock and private per-database sidecar discover and reuse its
loopback-only ephemeral listener. A bearer token, exact Host/Origin checks,
custom request header and restrictive CSP protect access. The browser cannot
choose a database path or dispatch arbitrary Store methods. The detached
process persists until explicitly stopped or terminated; no service, automatic
startup, remote assets or public hosting is installed. This is a new local
listener capability under the MCP server's existing trust boundary, not an
approval-policy change. Read requests keep the Store's existing audited-write
semantics. The service does not authenticate the human behind a token.

The initial recommended path is `init -> proposal/unresolved review ->
superdevloop -> sign-off`. Features with unsettled design use `feature-capture`
then `feature-design` before implementation. Capture does bounded research and
saves a pending feature brief with an active unresolved design gate. Pending
creation precedes adding the gate so there is no accepted, ungated intermediate
task. The human-readable `Feature design required (feature-design):` prefix
identifies the workflow by convention; no schema, task type or parser is added.
Design resumes those briefs, researches current behavior, compares alternatives,
recommends an approach and records the user's decisions. Selection includes
pending-acceptance tasks whose unresolved gates are hidden by their derived view.
Unrelated unresolved items do not automatically imply feature design.

Design saves the agreed specification before resolving settled gates, keeping
unfinished decisions blocked. Decomposition keeps the parent pending while
clearing settled gates and creates pending members; only exact specifications
covered by the user's informed acceptance become eligible. Prior informed
authorization remains usable; a capture/design request alone is not permission
to implement. Finishing design creates no implementation attempt or review.
MCP instructions route familiar design-task language to the two packaged skills
through `get_default_skills`, making them discoverable without client installation.
The database enforces the gates; interpreting language, researching choices and
obtaining real user decisions remain agent workflow responsibilities.

Canonical packaged skill files are returned verbatim
with version and hash by a read-only catalog. Export is a versioned,
deterministic workstream view, never an editable synchronized ledger. The server
writes its private SQLite database; it does not edit project files, client
configuration, or installed skills.

The default `task-mcp/v2` export is human-readable Markdown with the same derived
workflow views as the scoped queue, readable specifications/gates/result history,
and a separate global group summary. Stored disposition is labelled separately.
Only local scoped concrete tasks receive full entries. Other-workstream and
superseded-specification attempts remain visible as labelled history, not as
current delivery claims. The legacy `task-mcp/v1` embedded-JSON layout remains an
explicit compatibility option; neither export is an import or database backup.

Deferred: TASKS.md import, nested groups, per-workstream ordering, persistent
dynamic filters, claims or leases, native client installers, automatic client
configuration, editable export synchronization, peer group links, arbitrary
cross-project concrete task prerequisites, joint attempts, synchronized lifecycle, and full
rare-workflow parity.
Product proof requires a complete Codex dogfood cycle plus access and catalog
validation in Claude Code, OpenCode, and Pi.
