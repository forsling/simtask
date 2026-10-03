# Task MCP design

Task MCP is an opt-in, local task-state service. It owns durable task state,
workstream scope, concurrency checks, deterministic queries, and audit history.
It does not run agents or authenticate users. Reference workflow skills provide
the recommended orchestration, while raw MCP calls and custom skills remain
usable.

The stdio boundary also collects bounded private operational traces outside the
task database, enabled by default for early usage evaluation. Generated
connection/call IDs, observed request/results, timestamps and defined JSON byte
counts support offline reports without consumer tracking calls. Collection
failures never change task outcomes. This observes MCP requests, not agent
liveness, conversations, host delays or actual model-context tokens; see
[usage traces](docs/usage-traces.md).

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

Concrete tasks are canonical project objects. Each task is in the inbox or has
one owning workstream queue in its project. Queueing on another branch moves it;
unqueueing returns it to the inbox. A global group holds context and membership
across projects. Group references are context only: explicitly adding a group
with `set_scope` queues a snapshot of its current local members, respecting
individual exclusions in that expression. Future membership preserves each
member's placement. A workstream-base expression snapshots its actual queue;
it moves selected tasks rather than duplicating ownership. Decomposition replaces
a queued parent with concrete members in its queue. Completed placement is
immutable; bulk moves that would change a completed task fail atomically.
Project task order is shared; scope and prerequisites remain gates. Scheduling
metadata is separate from task revisions and immutable specifications/proof.
An atomic `reorder_tasks` moves one concrete task immediately before/after a
same-project anchor using the last board/queue `project_order_revision` and the
actual supporting scheduling instruction. Stale/invalid/self/cross-project moves
fail atomically. A no-op does not advance the order revision or normalize keys;
a changed move advances it once. Insertions append and advance it; decomposition
advances it for parent removal and member insertions. Existing projects start at
order revision 0 during the explicit schema 3 migration. Completed rows may
shift without touching queue placement, selected delivery or review. Each workstream
filters shared order by local scope and eligibility. Reads/selection never
reorder. Actual scheduling intent and actor attribution live in existing audit
events; no automatic priority rules, normalization or usage-tracking calls exist.
Every explicit scope change increments the workstream revision atomically.
Local queues, status counts and exports include only concrete tasks from the
workstream's project. Group references and whole-group progress are separate;
an empty local slice does not imply global completion.

## Tasks and groups

Task origin (`source`) and the original `user_request` are descriptive metadata.
`create_task` with a workstream creates queued work; without one it creates an
inbox task. `queue_task(task_id, workstream_id, expected_revision)` atomically
chooses its one branch; `unqueue_task(task_id, expected_revision)` moves it to
the inbox. No note or separate purpose decision is required. Workflow skills
choose which concrete agreed tasks to queue; raw calls remain available.
Queueing starts no implementation and clears no unresolved or prerequisite gates.

`update_task` saves title/body/criteria/summary amendments without changing
placement. Genuine requirement edits advance `spec_revision`; old attempts and
reviews remain historical on their original revision. Whole-field body/criteria
replacement requires the full `specification_etag` and expected task revision.
Summary-only corrections preserve spec revision and proof. No-op mutations retain
revisions. Queue moves check the task revision, advance it once, and advance each
affected workstream revision once. Bulk scope operations also advance every
changed task revision and affected workstream revision once. Failed concurrency
or audit writes roll back the entire operation. Completed specifications and
proof are immutable; new requirements after completion become new tasks.

Schema 6 adds a unique `queue_members.task_id` and a private
`legacy_queue_migration` report/archive. Migration preserves every existing task
row, event, attempt, review and selected completion. Legacy authority columns in
tasks and legacy `scope_members`/`scope_exclusions` remain frozen private historical
storage; current readers and writers never expose or change them. The archive
records their original authority and scope references for every existing entity.
Mutable scoped tasks without a current-spec legacy decision move to the inbox.
Eligible tasks with multiple scopes keep the owner with the most recent attempt
among eligible scopes, breaking ties by oldest workstream creation time then ID.
Completed tasks retain all history regardless of legacy decision classification;
only the new unique owning queue is chosen. Each report row records candidates,
chosen owner and reason. Group references survive as context but no longer expand
live. Fresh databases create the current schema directly. Existing databases take a fresh
verified private SQLite online backup under the migration writer lock; transaction
failure rolls back schema and data, retaining the backup. Candidate code, copied
databases and client workflows remain isolated until coordinated rollout.
Create/update/queue/unqueue acknowledgements return IDs, revisions, owning queue
and accurate gates without echoing specifications or proof.

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
keeps its own current specification, attempt, review and sign-off. A concrete
task may depend on any canonical task or whole-group completion in any project,
without requiring shared membership. The default `review` prerequisite milestone
is satisfied by done or any current-spec `passed`/`human_review` attempt across
workstreams; every member must satisfy it for a nonempty group. Explicit
`milestone="signoff"` links require done for every member. Reserve that exception
for cases where proceeding before the human verdict would very likely waste work.
Dropped/deferred blockers remain unsatisfied regardless of retained reviews.
Satisfaction is computed from current state: rework or a genuine specification
change can block a review link again, while dependent results survive. Canonical
completion still requires human sign-off; passing a prerequisite is no proof of
code integration into the dependent checkout. The link changes
only the dependent gate revision, never specification revision, local scope,
attempts, reviews or code integration. Compact prerequisite references expose
ID, title, project identity, required milestone, satisfaction and canonical
blocking/completion facts in full
details and queues, without fetching remote proof/history. Cycle checks include
prerequisite links and implicit group-to-member completion edges for additions,
observer acceptance and membership/decomposition changes. SQLite write
serialization and revision checks protect a race between membership changes and
last-member sign-off. Protocol 8/schema 5 persist link milestones, including
observer proposals, and migrate every existing link/proposal to `review`. A
duplicate link with a different milestone fails rather than silently changing
the gate. Decomposition preserves each inherited milestone. Protocol 9 adds
`remove_prerequisite(task_id, expected_revision, blocked_by_id, note)`: a required
actual decision note and configured actor are audited with the removed link in
the same transaction. It immediately recomputes the gate and advances only the
dependent task revision, once for a real deletion. An absent link returns
`changed=false` with the original revision/timestamp, while stale revisions and
completed/group targets still fail under the same mutability rules as additions.
Specifications, queue placement, attempts, reviews and other link milestones
survive. Schema 5 already supports removal; no migration is needed.
Replace known prose gates only after verifying actual IDs/meaning and adding
real links before resolving the old item; no automatic parsing or state repair.

## Delivery and authority

Reading/selecting a task does not claim it. An implementer creates an attempt
only when recording a durable result. Parallel workstreams may produce
alternative attempts on one canonical task. An independent declared reviewer
or an explicit human review must pass an attempt before human sign-off. Sign-off
selects a reviewed attempt. Rejection chooses rework on the current specification or opens an unresolved
specification question. Queue placement stays unchanged. The service cannot authenticate the reviewer or the user's verdict;
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
needed. See [the focused workflow](docs/continuation.md); full-spec and exact-proof reads are explicit.

Protocol 6 treats `record_result` as factual recording for any mutable scoped
concrete task, including pending, unresolved, prerequisite-blocked, deferred and
dropped tasks. Revision and full `specification_etag` bind the checked current
requirements. Structured artifact/commit references and actual verification are
required alongside implementer/context proof; the new proof envelope lives in
the existing evidence TEXT column, with old text evidence/history preserved.
Recording changes only the attempt and task record revision, never authority,
disposition, gates or completion. ACKs omit proof and expose attempt/task revisions
plus unchanged queue placement/disposition and active gate diagnostics.

`get_next_action` replaces the old implementation-only selector with no alias or
extra tool. It follows shared order across eligible implementation and review,
requiring queue placement, active disposition and clear unresolved/prerequisite
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
separately. Human decision dialogs require explicit confirmation; signoff reasons are required
for rework/revise and optional for approve/drop;
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
saves an inbox feature brief with an active unresolved design gate. Inbox
creation precedes adding the gate and any requested branch placement so there is
no queued, ungated intermediate task. The human-readable `Feature design required (feature-design):` prefix
identifies the workflow by convention; no schema, task type or parser is added.
Design resumes those briefs, researches current behavior, compares alternatives,
recommends an approach and records the user's decisions. Selection includes
inbox tasks alongside queued tasks with unresolved gates.
Unrelated unresolved items do not automatically imply feature design.

Design saves the agreed specification before resolving settled gates, keeping
unfinished decisions blocked. Unqueue the parent before clearing settled gates for decomposition; children
then start in the inbox. Queue only the concrete agreed specifications. Prior informed
authorization remains usable; a capture/design request alone is not permission
to implement. Finishing design creates no implementation attempt or review.
MCP instructions route familiar design-task language to the two packaged skills
through `get_default_skills`, making them discoverable without client installation.
The database enforces the gates; interpreting language, researching choices and
obtaining real user decisions remain agent workflow responsibilities.

The read-only skill index returns names, versions, hashes and descriptions;
a named request returns exactly that canonical skill verbatim. Export is a versioned,
deterministic workstream view, never an editable synchronized ledger. The server
writes its private SQLite database; it does not edit project files, client
configuration, or installed skills.

The default `task-mcp/v5` export is human-readable Markdown with the same derived
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

Protocol 11 supports four actual human signoff verdicts: approve completes;
rework returns the attempt to implementation; revise returns to design with an
open question; drop closes without approval. A single reasons field is required
for rework/revise, optional for approve/drop. Deferral remains an ordinary status
change. Signoff records only the actual verdict and reasons with provenance;
there are no separate purpose/technical judgments. Revise uses the reasons as
the open question without fabricating a specification revision. Queue placement,
context and factual proof survive; revival from dropped needs actual authority.
Current-spec reviewed proof and clear completion gates govern approval.
Historical signoff records, including old defer and judgment fields, stay intact.

A task's latest rejection is the newest successful reviewer rework or signoff
rework/revise event, with source, verdict, reasons, originating attempt/workstream/
specification, timestamp and decision reference. Full reads and get_next_action
carry this handoff; compact cards carry its provenance without prose. Each new
rejection replaces the projection while prior rounds remain in immutable history.
It is canonical task context, never evidence that an originating branch's proof
is current locally. Schema 7 adds a partial rejection-history index so bounded
reads seek actual rejection records without scanning intervening audit reads;
no task, attempt or event rows are rewritten. The existing verified migration
backup/rollback mechanism applies. Export v5 includes rejection and signoff history.

Protocol 7 makes ordinary MCP calls compact and complete for their chosen action.
Cards never expose partial specifications or replacement tokens. Specifications
retain all requirements/proposals/parent context and at most three actionable-first
current-spec attempt summaries; selected delivery ID is independent of that window.
Paged attempt/member history and exact proof reads preserve provenance. Store/viewer
full details and complete exports remain available. Every write acknowledgement
identifies affected entities, their revisions, changed/no-op state and useful gates
without echoing requirements/evidence/history. Continue from returned revisions;
full-spec tokens may continue from authored creates and valid token-bearing updates.
Conflicts reconcile against complete requirements before replacement.

Schema 4 adds nullable task/group summary and summary_spec_revision, preserving all
prior columns/rows without backfill. Summaries are optional, non-normative one-line
intent/constraints up to 240 Unicode characters. Null clears; omitted values persist.
Summary-only corrections change ordinary revision while preserving queue placement/spec/
proof, even after completion. Later spec edits make summaries stale; explicitly
reaffirmed or simultaneous edits stamp the current spec. Freshness tracks revisions,
not descriptive accuracy. Init queues/candidates are bounded to ten; ordinary task/
group/event pages to twenty; default scope/group/member expansions are bounded.
