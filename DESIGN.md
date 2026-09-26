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

Tasks are canonical project objects. A workstream's explicit scope consists of
included tasks, included groups, and excluded task IDs. A group inclusion is
live: future members enter that workstream unless individually excluded. An
explicit exclusion takes precedence over group expansion. A workstream can
snapshot another workstream's current expression, then add or remove references.
Project task order is global and advisory; scope and prerequisites are gates.
Every explicit scope change increments the workstream revision atomically.

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
gates. Decomposition converts a concrete task in place only after its unresolved
items are cleared and its gate proposals handled. Existing prerequisites move
to the new concrete members. A group completes when all required members are
complete. A concrete task may depend on a group; that dependency clears only
when all members complete. Cycle checks include both prerequisite links and
the implicit group-to-member completion edges.

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

The initial recommended path is `init -> proposal/unresolved review ->
superdevloop -> sign-off`. Canonical packaged skill files are returned verbatim
with version and hash by a read-only catalog. Export is a versioned,
deterministic workstream view, never an editable synchronized ledger. The server
writes its private SQLite database; it does not edit project files, client
configuration, or installed skills.

Deferred: TASKS.md import, nested groups, per-workstream ordering, persistent
dynamic filters, claims or leases, native client installers, automatic client
configuration, editable export synchronization, and full rare-workflow parity.
Product proof requires a complete Codex dogfood cycle plus access and catalog
validation in Claude Code, OpenCode, and Pi.
