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
disjoint task view counts, plus explicitly overlapping active gate diagnostics.
Only open/rework tasks contribute active gates; done, dropped and deferred tasks retain
their context and attempt/review history without presenting it as actionable.
Resuming a deferred task restores its applicable gates. The registered binding
and derived task views describe recorded state. `agent_liveness=not_tracked`
states that agent activity is intentionally unobserved; there are no heartbeats
or leases. These summaries make no claim about integrated feature completion. Page
offsets browse current state; stable IDs support durable resumption.

Concrete tasks are canonical project objects. One global group model holds
context and membership across projects. A workstream's explicit scope consists
of local tasks, group references, and exclusions. A group inclusion is live:
future members of the workstream's own project enter it unless individually excluded. An
explicit exclusion takes precedence over group expansion. A workstream can
snapshot another workstream's current expression, then add or remove references.
Project task order is shared; scope and prerequisites remain gates. Scheduling
metadata is separate from task revisions and immutable specifications/proof.
An atomic `reorder_tasks` moves one concrete task immediately before/after a
same-project anchor using the last board/queue `project_order_revision` and the
actual supporting scheduling instruction. Stale/invalid/self/cross-project moves
fail atomically. A no-op does not advance the order revision or normalize keys;
a changed move advances it once. Insertions append and advance it; decomposition
advances it for parent removal and member insertions. Existing projects start at
order revision 0 during the explicit schema 3 migration. Completed rows may
shift without touching acceptance, selected delivery or review. Each workstream
filters shared order by local scope and eligibility. Reads/selection never
reorder. Actual scheduling intent and actor attribution live in existing audit
events; no automatic priority rules, normalization or usage-tracking calls exist.
Every explicit scope change increments the workstream revision atomically.
Local queues, status counts and exports include only concrete tasks from the
workstream's project. Group references and whole-group progress are separate;
an empty local slice does not imply global completion.

## Tasks and groups

Task origin (`source`) and the original `user_request` are descriptive metadata,
independent of approval and retained on pending tasks. Creation without approval
is pending. A common `approval={basis: specific|delegated, note: actual supporting
instruction}` payload atomically accepts the exact created spec, and is also
required by standalone acceptance. Specific approval covers the exact scope;
delegated approval requires actual authority to select work within a stated
goal. Explicit pending/design-first requests take precedence. Queueing starts
no implementation; approval clears no other gates. Legacy origin and approvals
are unknown without guessing from historic text or changing accepted/completed
state. Database schema revision 2 introduced this data; candidate protocol 6
retains the purpose/result signoff contract described below.

Acceptance binds to the current specification. `update_task` atomically saves
title/body/criteria amendments and optional exact-scope specific/delegated
approval. An actual edit without approval becomes pending; all three fields
belong to the spec, without inferred editorial exceptions. Unchanged fields
are no-ops unless approval changes; unchanged pending specs can be approved
without advancing their spec revision. Whole-field body/criteria replacement
requires the `specification_etag` from a full detail read, tied to task ID,
spec revision, body and criteria, alongside the expected task revision.
`get_tasks` still returns full details in one call; compact cards and a separate
descriptive summary field are deferred. Approval is committed with the resulting
spec so no intermediate accepted state is exposed. Failed concurrency or audit
writes roll back both together. Clearing an unresolved item or recording
evidence does not. Revision-checked `withdraw_acceptance` audits its reason and
clears current acceptance without a dummy edit, preserving spec revision,
decisions and proof history. It gates implementation and sign-off. Reapproval
of the same spec may reuse applicable review; genuine spec changes retain old
attempts on their old revision. Readiness also depends on unresolved items,
prerequisites, disposition, scope and attempts. Completed tasks are immutable;
a new requirement after completion is a new task.

Existing schemas migrate transactionally after a fresh verified SQLite online
backup under the writer lock. Backup includes committed WAL data and remains
available after success or rollback. Migration preserves all existing rows and
history; it never infers origin or approval basis from prior notes. Fresh empty
databases create schema 3 directly. Candidate code, databases and companion
workflow updates must remain isolated until client rollout can happen together.
Create/update/accept/withdraw acknowledgements return continuation IDs/revisions,
acceptance and accurate gates without echoing specs or proof history.

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
task may depend on any canonical task or whole-group completion in any project,
without requiring shared membership. Concrete blockers require human-signed-off
done; reviewed, dropped or deferred work remains unsatisfied. The link changes
only the dependent gate revision, never specification acceptance, local scope,
attempts, reviews or code integration. Compact prerequisite references expose
ID, title, project identity and canonical blocking/completion facts in full
details and queues, without fetching remote proof/history. Cycle checks include
prerequisite links and implicit group-to-member completion edges for additions,
observer acceptance and membership/decomposition changes. SQLite write
serialization and revision checks protect a race between membership changes and
last-member sign-off. Global foreign keys already support these links; protocol
5 changes no schema or migration. Evaluate the human-sign-off milestone after
rollout using existing audit/workflow evidence, without routine progress calls.
Replace known prose gates only after verifying actual IDs/meaning and adding
real links before resolving the old item; no automatic parsing or state repair.

## Delivery and authority

Reading/selecting a task does not claim it. An implementer creates an attempt
only when recording a durable result. Parallel workstreams may produce
alternative attempts on one canonical task. An independent declared reviewer
or an explicit human review must pass an attempt before human sign-off. Sign-off
selects a reviewed attempt. Rejection chooses rework on the current accepted
specification or revision that invalidates acceptance and adds an unresolved
item. The service cannot authenticate the reviewer or the user's verdict;
workflow clients must obtain and accurately record those decisions.

Continuation keeps the durable workstream ID, scope and history through
`init`/rebind. Recorded state does not prove checkout applicability: callers
inspect the actual tree and relevant commits against the current full spec,
especially after moving a binding. Ordinary execution uses only current-spec
local attempts, so another workstream's pending or passed result does not block
parallel alternatives. Deliberate merge/cherry-pick integration of an open
task produces an ordinary new target-local result citing origin attempt and
workstream, actual source/target commits and target verification, followed by
fresh independent review. Origin reviews are never inherited. Completed tasks
retain selected human-approved proof; later integration is a new task. No
adoption API, shared working state, schema expansion or routine checkpoints are
needed. See [the focused workflow](docs/continuation.md); compact default task reads
remain separately deferred.

Protocol 6 treats `record_result` as factual recording for any mutable scoped
concrete task, including pending, unresolved, prerequisite-blocked, deferred and
dropped tasks. Revision and full `specification_etag` bind the checked current
requirements. Structured artifact/commit references and actual verification are
required alongside implementer/context proof; the new proof envelope lives in
the existing evidence TEXT column, with old text evidence/history preserved.
Recording changes only the attempt and task record revision, never authority,
disposition, gates or completion. ACKs omit proof and expose attempt/task revisions
plus unchanged acceptance/disposition and active gate diagnostics.

`get_next_action` replaces the old implementation-only selector with no alias or
extra tool. It follows shared order across eligible implementation and review,
requiring current approval, active disposition and clear unresolved/prerequisite
gates. Within a task, pending current-spec local review precedes implementation;
newest first then ID ascending is deterministic. Passed/human_review waits for
human sign-off; rework implementation carries its attempt/findings. One selected
full spec includes the read token, pending proposals and local gates; review
includes exactly one complete local proof and review provenance, without history.
Null selection gives bounded counts/waiting reasons. Reads keep existing audit
semantics and do not claim, reorder or write progress. Explicit manual reviews
retain their existing authority and cannot clear remaining gates. Workflow clients
check the actual checkout/artifacts and send reviews to fresh independent reviewers.
No working/start/lease/checkpoint lifecycle or routine extra repository scan exists.

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
configuration, editable export synchronization, peer group links, cross-project scheduling,
joint attempts, synchronized lifecycle, and full
rare-workflow parity.
Product proof requires a complete Codex dogfood cycle plus access and catalog
validation in Claude Code, OpenCode, and Pi.

Candidate purpose/result signoff (introduced in protocol 3; current protocol 6,
schema 3) uses one human
`approve`/`rework`/`revise`/`drop`/`defer` decision. Specific current-scope purpose
approval references its actual classified audit decision; delegated/unknown
purpose is judged by the user's informed signoff decision. Direction-only
changes preserve sound attempts and leave human technical quality unjudged
unless separately supplied. Revise withdraws approval and opens a concrete
question without a fabricated spec revision; drop withdraws approval and needs
actual authority for revival; defer retains approval. Both remain unsatisfied
prerequisites. Exact task/spec/attempt revisions and separate judgments live in
immutable audit records, while full task reads deliberately expose proof and
decision references. No additional DDL is needed for signoff judgments.
