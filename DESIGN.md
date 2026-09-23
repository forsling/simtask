# Task MCP: v1 boundary and design history

## Implemented v1 boundary (2026-09-23)

The consolidated v1 design in the private Task MCP design record is the
authoritative current decision. The service is opt-in: unknown projects require
explicit initialization, and a checkout/branch must match a durable workstream
binding before autonomous work. Tasks are canonical project objects; workstream
scope is an explicit set relation. A scoped group keeps future required members
visible. Project order is global and advisory; prerequisites are hard gates.

There is one task model. User acceptance binds to the current specification
revision; agent proposals begin pending. Readiness derives from acceptance,
unresolved items, prerequisites, scope and implementation/review facts. Groups
are one-level, non-implementable objects with completion derived from members.
Execution attempts are durable only when an implementer records a result, so
there is no mandatory start, claim or lease operation. Attempts are per
workstream, may be parallel, and need an independent declared reviewer or
explicit human review before the user's informed sign-off verdict. Rejection
records rework or revision of the specification. The server enforces observable
state invariants but cannot authenticate humans or agent independence.

The initial reference path is `init -> proposal/unresolved review ->
superdevloop -> sign-off`. Canonical packaged skill files are returned verbatim
with version and hash by a read-only catalog. Workstream export is versioned,
deterministic and export-only. The SQLite v1-to-v2 schema migration preserves
existing IDs, descriptions and audit history. The legacy prototype discussion
below is historical and its former auto-registration, Design/Auto kinds, tracks,
and mutable status semantics are superseded.

Deferred: TASKS.md import, nested groups, per-workstream ordering, persistent
dynamic filters, claims/leases, native client installers, automatic client
configuration, editable export synchronization, and full rare-workflow parity.
Full product proof still requires one complete Codex dogfood trial and access
and catalog validation in Claude Code, OpenCode and Pi. Test success alone is
not a claim of parity in those clients.

## Historical prototype discussion

This document preserves the discussion that led to the prototype on 2026-09-15.
It explains the intended problem, agreed direction, and unresolved decisions.
Prototype implementation choices below are experiments, not approval to redesign
every existing workflow or migrate project data.

## Objective

Build a development system that uses tokens and human attention efficiently while
producing quality code that solves the user's actual problem. Optimize total
delivery cost: investigation, context loading, implementation, review, unwanted
work, rework, and informed human approval. Reducing prompt length alone is not
success if the result is wrong or adds unwanted features.

The normal process must be understandable without memorizing its internals:
agree on an outcome, implement it, independently review it, present it for human
sign-off. New failure cases should not automatically add permanent ceremony.

## How we got here

The original workflow used one TASKS.md, with Design and Auto tasks and a short
archive at the bottom. Real failures led to more rules, multiple ledgers,
independent review, sign-off, evidence, dependencies, and a task CLI. The user can
no longer comfortably keep all the process details in their head.

The current system is used in projects including:

- two internal service repositories, one of them the most heavily used instance

The starting template is
the user's `TASK_TEMPLATE_v2.md` notes file. These sources were reviewed as
examples; they are not instructions to import or mutate their current tasks.

## Problems we intend to solve

1. **Context pollution and repeated work.** Large ledgers mix instructions,
   specifications, review evidence, and deferred work. Agents repeatedly read
   irrelevant text and re-investigate the same files. Splitting files helped but
   introduced more places to maintain. A CLI was added reluctantly and has not
   been installed consistently.
2. **Concurrent edits and ownership.** Multiple agents became entangled through
   shared ledger edits. Separate worktrees can be appropriate for independent
   development tracks, but task identity and track ownership need to be clear.
3. **Private workflow in shared Git history.** Committing every ledger update
   produced hundreds of bookkeeping commits. Preparing shared PRs required
   removing private workflow files and rewriting branch history. Untracked files
   avoid those commits but are harder to discover and recover.
4. **Feature creep and overengineering.** Agents built things the user had not
   requested. Passing acceptance checks did not necessarily establish that the
   implementation solved the intended problem or stayed within its scope.
5. **Expensive and opaque sign-off.** Human sign-off was one of the most useful
   additions. However, approving tens of tasks individually takes too much time.
   The first batched experiment saved presentation space at the expense of
   understanding: the user could not tell what they were approving.
6. **Loss of deferred design context.** Backlog formatting favors concise entries.
   Moving a partially resolved Design task there can lose decisions, rejected
   alternatives, rationale, and open questions, causing later agents to start over.
7. **Accumulating process rules.** Evidence, dependencies, rework limits, and
   archival rules address real problems but increase the cost of understanding
   and operating the system. Template, project, CLI, and agent-specific skill
   definitions already disagree in places.
8. **Project bootstrapping.** Creating files, copying templates, and adding tools
   in each new project is friction. A private service could register a project
   automatically when its first task is created.

## Principles emerging from the discussion

- Keep private workflow state out of shared repository commits.
- Preserve information while it can affect future work. Deferral changes timing;
  it must not silently discard decisions or reduce a rich design to a one-liner.
- Preserve human sign-off as an informed decision about the actual result.
- Share research and verification across related tasks while retaining an
  understandable explanation and individual outcome for every presented task.
- Make scope boundaries visible before implementation, and review added behavior,
  operational obligations, and complexity as well as acceptance criteria.
- Capture new ideas for discussion without treating capture as authorization to
  implement them.
- Prefer one coordinator owning task selection and state changes per development
  track. Workers implement bounded assignments; reviewers return independent
  findings. This direction is supported, but the agent workflow is not migrated.
- Evaluate simplification by user-visible burden and total token cost. Moving
  complexity into code is worthwhile only if it materially reduces that burden.

## Why explore MCP

MCP tools can read and write data. A local server can expose structured task
operations while keeping storage private and returning only relevant context.
The user proposed automatic registration from a project path or name and a
timestamped audit trail of operations, then explicitly requested this prototype.

This could replace both Markdown ledgers and per-project CLI executables. The
connection is configured once per agent application; new projects need no files.
MCP itself does not provide task storage, scheduling, approval authority, backup,
or concurrency control. Those remain application design choices.

## Target system beyond the storage prototype

The replacement must support the named workflows people actually use:
`devloop`, `superdevloop`, `guidedtask`, `designrev`, `signoff`, backlog triage,
and idea/defect review. Storage alone is not the intended product. The workflows
remain invocable by name and operate on this service's task data instead of
requiring a Markdown ledger or per-project CLI. In particular, independent
review and informed human sign-off remain required parts of completion.

The MCP server owns durable task state, queries, revision checks, and auditable
transitions. Agent workflow instructions own task selection, implementation,
review, and the conversation that obtains a human verdict. MCP initialization
instructions can provide brief rules shared by all tools; they do not install
named Codex skills. For Codex, the intended distribution is one installable
package containing both the MCP connection and adapted workflow skills. A new
project registers on first task use and needs no copied skill files. Other agent
clients may need their own workflow adapters while using the same service.

The current implementation proves only the storage/interface slice. Its eight
tools and local dogfooding task do not yet exercise the named workflows end to
end. Before calling this a replacement, adapt and exercise the workflows on
real work, including task ordering, blocked work, Design decisions, independent
review, and human sign-off. The heavily used `xot-compose` system is the
behavioral reference; migration of its live ledger remains a separate decision.

## Prototype boundary

The prototype uses Python, the official MCP SDK, stdio transport, and one local
SQLite database outside repositories. It requires no listening network service.
Multiple MCP processes may use the same database, so transactions and revision
checks protect updates even when separate agent applications are connected.

It exercises a deliberately small set of operations: create and discover projects,
create tasks, filter compact task lists, fetch full tasks in batches, update a
task, record a sign-off verdict, inspect audit history, and export a readable
project view. These are storage operations; they do not run coding agents.

Tasks retain rich Markdown bodies. Kind (`design` or `auto`) and status are
separate. Deferring an entry changes its status while retaining its kind and
body. Groups support filtered review preparation. The default track is `main`,
a workflow label rather than automatic Git branch discovery. Lower numeric
priority sorts first, with rework preferred. This ordering is provisional.

Projects have stable generated IDs. An absolute path is canonicalized; a name
can also be used. Names matching multiple projects require an ID or path. Paths
are identity hints, not directories the service scans or modifies. Worktrees are
not automatically merged into one project. Rename/alias reconciliation is open.

Every successful service operation, including reads, writes an event with UTC
time, sequence number, server-configured actor label, and request details.
Task mutations include complete before/after snapshots. Domain errors are also
recorded. Successful mutations and their events commit in the same transaction.
Protocol/schema failures rejected before the service runs, and storage failures
that prevent an event from being saved, are outside this initial audit guarantee.
Actor labels are attribution aids, not authenticated identities. Audit data is
local history, not a tamper-proof compliance log.

Lists exclude deferred/completed/dropped tasks unless explicitly filtered for
them. History is queried separately with compact output by default. Export is a
generated view, not a second editable source of truth. Closed tasks are retained
in storage; there is no ARCHIVE.md to maintain and no history pruning in v0.1.

The prototype includes a small sign-off guard: Auto completion uses a dedicated
operation for a task in `signoff`, with the user's decision recorded. This avoids
accidental completion through generic updates. It does not prove the caller is
human or independently verify that review or approval actually occurred. The
coordinator must obtain and accurately record the user's verdict.

## Explicitly outside the prototype

- Migrating or deleting existing TASKS.md/BACKLOG.md/ARCHIVE.md files.
- Editing installed agent skills or automatically connecting this server to them.
- A web application, cloud synchronization, authentication system, or team SaaS.
- An autonomous scheduler, agent launcher, or dependency graph engine.
- A mandatory elaborate task template or exhaustive state-transition framework.
- Automatic scope expansion, design approval, or human sign-off.
- Filesystem artifact ingestion, automatic project alias detection, and custom
  backup infrastructure. The local database should be included in normal backup;
  readable export provides inspectability, not a complete history backup.

## Decisions still open

- **MCP versus private Markdown plus a shared CLI:** MCP is the current prototype
  direction, not yet a proven replacement. Measure actual use before migration.
- **Human inspection:** is generated Markdown sufficient, or is a simple direct
  view/editor necessary? Avoid requiring an agent for every act of understanding.
- **Sign-off batches:** existing groups are the proposed default. A research batch
  may span several smaller presentations. Specify how exceptions and per-task
  explanations work before changing the skills.
- **Scope control:** how should intended outcomes and boundaries be captured with
  minimal ceremony? Which additions require discussion rather than implementation?
- **Dependencies and blockers:** which relationships deserve structured fields,
  and which can remain a concise explanation? The prototype does not auto-unblock.
- **Project and track identity:** how should moved repositories, multiple clones,
  and deliberately independent worktrees map to shared or separate task sets?
- **Retention and recovery:** completed tasks and operation history can be retained
  without prompt pollution. Decide the value of retention and restore operations
  from real usage, not from a desire to reproduce the old archive machinery.
- **Workflow state and authority:** final statuses, review invalidation rules,
  coordinator ownership, and stronger human-verdict enforcement remain unsettled.
- **Workflow packaging and parity:** determine which existing skill behaviors
  belong in the shared service versus client workflow instructions, then package
  and test the named workflows with the MCP connection as one installation.
  Preserve human sign-off and independent review while avoiding unnecessary
  per-project setup.
- **Idempotency:** stale update retries are rejected using revisions; retried task
  creation is not deduplicated automatically in this first prototype.

## How to judge the experiment

Exercise a fresh project, a rich design deferred and resumed without loss, an
implementation/review/sign-off cycle, a group prepared for sign-off, and two
callers attempting to update the same revision. Inspect the audit trail and
readable export without loading an entire project's history into agent context.

Compare tool calls, returned context, repeated code reads, user time spent on
sign-off, and unwanted scope against the current process. Useful evidence must
include whether the user understands the results they are approving. Test counts
alone cannot validate this product goal.
