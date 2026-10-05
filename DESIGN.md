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
rebind checks the workstream revision and preserves its scope and history.
Passing a known workstream ID checks that it is bound to the given checkout and
branch and reports any mismatch; there are no separate setup tools. A new
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
Membership is nonexclusive: adding a task to B keeps it in A. Adoption is derived
from effective membership in at least one workstream; inbox means none. Workstream
implementation/review state uses its own current-spec attempts. A task's global
completion and historical proof remain canonical.
A workstream has an ordered list of shared tasks. Each list's ordering metadata
is separate from canonical task revisions, immutable specifications/proof and
branch-local attempts. `reorder_tasks(workstream_id, task_ids, expected_order_revision)`
sets the supplied included-task IDs as the exact new prefix in one transaction.
Every unlisted member follows in its previous relative order; membership does not
change. Duplicate/nonmember IDs and stale revisions fail atomically. Empty lists
and already-matching prefixes preserve the order revision and stored keys.
Changed ordering advances only the named workstream's order revision once,
including when completed tasks move. Compact ACKs contain identity, revision,
change and count facts; actual ordered-ID requests and configured actors remain
in existing audit events. Reads and selection never reorder or normalize work.

Schema 10 adds `workstreams.order_revision` and `workstream_task_order`. Initial
migration seeds every effective list in existing project baseline order at
revision 0. Live scopes, group references and exclusions remain authoritative.
Membership-changing transactions remove departing members, retain existing
positions and append newly included members in deterministic project baseline
order, advancing each affected list revision once. Removing and re-adding a task
appends it. Copying a scope copies its expression and live references, not source
ordering or a membership snapshot. Canonical `tasks.order_key` remains a stable
project-wide baseline; old project order revisions remain private historical
storage. There is no project-level reorder surface or shared execution order.
Boards, action/init/status reads and exports expose `workstream_order_revision`;
scoped cards also expose their `position` in that order. The viewer reorders by drag and
drop within the selected workstream's list, sending the prefix through the moved
task with the loaded order revision; conflicts reload rather than overwrite.
No automatic priority rules, normalization or consumer reporting calls exist.
Every explicit scope change increments the workstream revision atomically.
Local queues, status counts and exports include only concrete tasks from the
workstream's project. Group references and whole-group progress are separate;
an empty local slice does not imply global completion.

Each project and each workstream has at most one note: personal, uncommitted
plain text of at most 2,000 characters. The project note holds the user's rules
for that repository; the workstream note holds that branch's current situation
(for example what is deployed and how to roll back) and its rules. They carry
what a handover would otherwise carry, so a fresh session reads them from the
ready `init` response, which includes each nonempty note with its text, revision,
update time and configured actor, and omits empty ones. Other knowledge lives
elsewhere: rules about the code in committed repository files, personal rules
for every repository in user-level files, and Task MCP workflow rules in skills.
The bound keeps notes a current summary rather than an unbounded, stale log:
saves over the limit are rejected with the limit and the submitted length, and
agents replace outdated content instead of appending. Group notes are not
supported.

`set_note(kind, target_id, expected_revision, text)` replaces either kind; empty
or whitespace-only text clears. The note's own revision guards saves: an absent
or empty note accepts 0, and a cleared note keeps its row so a revision is never
reused for different text. Identical text is a no-op. Every save is audited
(`note.set`) with before/after text, like task mutations; it changes no task,
scope or workstream revision. The viewer shows both notes read-only above the
task list. Notes live in a separate `notes` table. Because schema 10 servers
never read it, it is added without a schema revision bump, so a previous server
keeps running against, and restarting on, the upgraded database. An existing
database without the table gets a verified `*.pre-notes.*.sqlite3` online backup
before the table is created.

A workstream can be archived when its binding is stale, such as a snapshot
folder that would otherwise be found by path, branch or name and mislead a new
session. `archive_workstream(workstream_id, expected_revision, reason, archived)`
archives or unarchives with a required short reason (at most 200 characters),
checked against the workstream's own archive revision (0 before any archive)
and audited as `workstream.archived`/`workstream.unarchived`. It is a discovery
flag, not a disposition: tasks, memberships, other workstreams' lists, order,
attempts, history and the workstream revision are unchanged, so unarchiving
restores everything as it was. Archived workstreams are skipped by
`list_workstreams` (counted in `archived_hidden`), init candidates,
`workstream_status` listings (header and counts only) and `get_next_action`,
and `include_archived=true` includes them. `init` at an archived workstream's
own checkout returns `state=archived` rather than resuming it, and a rebind of
one needs the flag too. Init's mismatch invariants hold: a requested workstream
is never swapped, and every offered choice works when followed, so a choice that
resumes or moves an archived workstream carries `include_archived=true`. The
archived workstream keeps its branch and name, which therefore stay unavailable
to new workstreams in that project. The viewer hides archived workstreams from
navigation unless the user shows them. Archive state lives in a separate
`workstream_archive` table, added without a schema revision bump like `notes`
(and after a verified `*.pre-workstream_archive.*` backup when missing); a
previous server ignores it and keeps listing archived workstreams.

## Tasks and groups

Task origin (`source`) and `user_request` remain descriptive metadata.
`create_task(workstream_id=...)` adds direct membership there; without one it has
no direct membership, but existing group scope may include it. Use
`add_to_workstream(task_id, workstream_id, expected_revision)` and
`remove_from_workstream(task_id, workstream_id, expected_revision)` for one named
workstream. Removal adds a local exclusion so group expansion cannot reinclude the
task. Other scopes and attempts survive. No typed authority note or separate
specification acceptance is required. Membership starts no implementation and
clears no unresolved/prerequisite gates.

`update_task` saves title/body/criteria/summary amendments without changing
membership. Genuine requirement edits advance `spec_revision`; old attempts and
reviews remain historical on their original revision. Whole-field body/criteria
replacement requires the full `specification_etag` and expected task revision.
Summary-only corrections preserve spec revision and proof. No-op mutations retain
revisions. Named membership changes check and advance the task revision once and the
named workstream revision once. Original bulk scope changes check/advance only
the workstream revision; membership remains separate from specifications/proof. Failed concurrency
or audit writes roll back the entire operation. Completed specifications and
proof are immutable; new requirements after completion become new tasks.

Schema 9 restores original live `scope_members`, `scope_groups` and
`scope_exclusions`. It removes the rejected exclusive `queue_members` table.
For original schemas, all scope rows survive; for isolated schema 6–8 candidates,
retained rows and `legacy_queue_migration` archives recover every original scope
reference, and candidate additions are retained without selecting an owner.
The private `legacy_membership_migration` archive records original task/scope
facts. Legacy authority columns and older migration archives remain private
history; no current payload or gate reads separate acceptance state.

Scoped mutable legacy work lacking current-spec intent keeps its memberships and
receives an ordinary unresolved question explaining the migration and requiring
an explicit decision before implementation. Normal `resolve_unresolved`, deferral
or named removal handles it. Done/dropped history receives no activation gate.
Specifications, full-spec etags, old attempt evidence/concerns, reviews, selected
completion and existing audit bytes survive. Only the new pending question bumps
a task's revision. Fresh verified private SQLite online backups include committed
WAL data; failure rolls back schema/data and retains the backup. Rehearsals use
copies, and live migration stays held until coordinated rollout. Protocol 13
exposes `workstream_ids` and derived `adopted` in compact/full payloads.

Groups store overarching context and aggregate completion. They are never
implementable and carry no unresolved items, disposition, attempts, or execution
gates. `create_task(kind="group")` creates an empty globally identified group in
the caller's workstream scope; it does not require a home project.
`update_task(group_id=..., group_expected_revision=...)` or
`create_task(group_id=..., group_expected_revision=...)` attaches a concrete
task from any project with revision checks. `list_tasks(state="group")` discovers
groups globally or through member/scoped projects, and `list_tasks(group_id=...)`
lists members. Group IDs in `add_to_workstream`/`remove_from_workstream` include
or remove a group in a workstream's scope; a member ID excludes just that member.
Existing decomposition converts a
concrete task in place after resolving its unresolved items;
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
prerequisite links and implicit group-to-member completion edges for additions
and membership/decomposition changes. SQLite write
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
Specifications, queue membership, attempts, reviews and other link milestones
survive. Schema 5 already supports removal; no migration is needed.
Replace known prose gates only after verifying actual IDs/meaning and adding
real links before resolving the old item; no automatic parsing or state repair.

## Delivery and authority

Reading/selecting a task does not claim it. An implementer creates an attempt
only when recording a durable result. Parallel workstreams may produce
alternative attempts on one canonical task. An independent declared reviewer
or an explicit human review must pass an attempt before human sign-off. Sign-off
selects a reviewed attempt. Rejection chooses rework on the current specification or opens an unresolved
specification question. Workstream membership stays unchanged. The service cannot authenticate the reviewer or the user's verdict;
workflow clients must obtain and accurately record those decisions.
"Sign off" requests asked/built/verified presentation and then a verdict;
explicit approval needs no walkthrough, though a still-open prerequisite is shown
and the user asked whether it affects the verdict. The walkthrough answers three questions
independently, in order: "Worth doing?", "Right approach?" and "Built well?",
addressing every concern. Only the user approves. A failed "Built well?" maps to
rework, "Right approach?" to revise, and "Worth doing?" (or obsolete work) to
drop; a removal task is added when dropped code must go. Confirm the matching
verdict for a generic rejection with reasons.

Implementers follow the spec and record worth-doing or approach concerns
(stored kinds `value`/`design`) instead of substituting a different design.
Unexpected blockers/questions are saved before moving on. Independent review
assumes the agreed approach is substantially right: an unattended fix within
goal/scope/decided approach/criteria is rework, including poor choices left open
to the implementer. A real problem caused by faithful specification is a
concern, so well-built work passes with it. Concerns never fail review; optional
additions are new ideas. Explicit user instructions override this reference
workflow guidance.

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
plus unchanged queue membership/disposition and active gate diagnostics.

`get_next_action` replaces the old implementation-only selector with no alias or
extra tool. It follows local workstream order across eligible implementation and review,
requiring queue membership, active disposition and clear unresolved/prerequisite
gates. Within a task, pending current-spec local review precedes implementation;
newest first then ID ascending is deterministic. Passed/human_review waits for
human sign-off; rework implementation carries its attempt/findings. One selected
full spec includes the read token and local gates; review
includes exactly one complete local proof and review provenance, without history.
Null selection gives bounded counts/waiting reasons. Reads keep existing audit
semantics and do not claim, reorder or write progress. Explicit manual reviews
retain their existing authority and cannot clear remaining gates. Workflow clients
check the actual checkout/artifacts and send reviews to fresh independent reviewers.
No working/start/lease/checkpoint lifecycle or routine extra repository scan exists.

Protocol 12 adds optional value/design concerns to result/review recording.
A concern is a doubt that cannot be fixed without changing what the task says.
Concern entries preserve implementer/reviewer source and author on the attempt;
reviewer additions do not erase implementer contributions, and omission leaves
stored evidence unchanged. They are a nonblocking channel to the user: gates,
review outcomes and prerequisite satisfaction are unaffected.
Complete attempt reads and viewer/sign-off show the prose. Explicit full task
reads show a bounded applicable attempt window with concern totals/references;
selected review proof carries its concerns in the same call. Current workstream
status has a separately paged task-with-concerns list, prioritizing awaiting
sign-off and filtering to the current specification and local attempts. Historical
concerns remain in deliberate attempt/history reads and exports, with provenance.
Schema 8 stores attributed concerns in a separate private attempt JSON column.
Migration initializes it empty without inspecting or rewriting existing evidence,
including historical JSON that resembles concern metadata. Reviewer additions
never rewrite proof. The established durable-result proof decoder is unchanged;
concerns come only from the explicit column, exposed as decoded entries.

An active handler may add a task gate directly. Nonblocking observer gate
proposals are retired; rows written by older servers stay readable and inert.
The default is agent autonomy. Unresolved items are for
questions that materially risk wasted work,
expand authorization, require user-only information, or exhaust normal recovery.
Cheap research and reversible choices proceed without ceremony.

## Surface and boundary

Agent boards are for choosing active work. `list_tasks`, `workstream_status` and
the `init` queue list open and rework tasks (including those awaiting review or
sign-off) and count done, deferred and dropped tasks per status instead of listing
them; `include_inactive=true` or an explicit closed `state` filter lists them.
Cards are slim: identity, title, summary, one state word, revision, workstream
position, unsatisfied blocker IDs, question and concern counts and a waiting-
rejection flag, omitting empty defaults. Named include groups (`blockers`,
`attempt`, `concerns`, `workstreams`, `ids`) on `list_tasks` and `get_tasks`
restore the remaining card detail without a specification read, so nothing an
agent could read before costs more than it did. Full specification reads,
`get_next_action` and write acknowledgements are unchanged. Hiding is a
projection only: positions, order revisions, gate semantics, status counts and
exports still cover every member.

The optional local browser companion invokes a narrow allowlist of the same
Store operations as MCP. It adds no task state model, business transition
logic, synchronization or agent dependency. Its board reads unabridged cards,
closed tasks included, so project/workstream queues retain their derived views; stored disposition and global group progress are labelled
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

The initial recommended path is `init -> proposal-review -> superdevloop ->
task-signoff`. Features with unsettled design use `task-capture` then
`task-design` before implementation. Capture does bounded research and saves a
feature brief with an active unresolved design gate. Create in the inbox, add
the gate, then add user-requested briefs to the current workstream; confirmed
agent ideas stay in the inbox. Explicit membership instructions override these
defaults. This avoids an ungated intermediate workstream member. The readable
`Feature design required (task-design):` prefix (older briefs say
`(feature-design)`) identifies the workflow by convention; no schema, task type
or parser is added.
Design first asks whether the work is worth doing, then researches behavior,
compares approaches and records the user's decisions. Selection includes
inbox tasks alongside workstream members with unresolved gates.
Unrelated unresolved items do not automatically imply feature design.

Design saves the agreed specification before resolving settled gates, keeping
unfinished decisions blocked. Keep unsettled decisions blocking until decomposition; member tasks inherit all
original parent scopes. Add member gates and adopt the resulting user-requested
specifications where the user is working. Prior decisions remain usable;
membership and design discussion start no implementation. Finishing design creates
no implementation attempt or review.
MCP instructions name the packaged skills, and each skill's description routes
familiar request language to it through `get_default_skills`, making them
discoverable without client installation. Tool descriptions hold tool mechanics;
skills hold workflow and judgment, so each fact is stated once.
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

Deferred: TASKS.md import, nested groups, persistent
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
the open question without fabricating a specification revision. Queue membership,
context and factual proof survive; revival from dropped needs actual authority.
Current-spec reviewed proof and answered questions govern approval. An
unsatisfied prerequisite does not refuse it: a reviewed result stays awaiting
sign-off and the user, as the authority, judges whether the open blocker affects
the verdict. The approval records which prerequisites were still open
(`open_prerequisites`) in its decision and response, so history shows them;
dependents of the approved task follow the ordinary prerequisite rules.
Historical signoff records, including old defer and judgment fields, stay intact.

A task's latest rejection is the newest successful reviewer rework or signoff
rework/revise event, with source, verdict, reasons, originating attempt/workstream/
specification, timestamp and decision reference. Full reads and get_next_action
carry this handoff; slim cards flag `rejected` until a newer result is recorded,
and the `attempt` include group carries its provenance without prose. Each new
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
Summary-only corrections change ordinary revision while preserving queue membership/spec/
proof, even after completion. Later spec edits make summaries stale; explicitly
reaffirmed or simultaneous edits stamp the current spec. Freshness tracks revisions,
not descriptive accuracy. Init queues/candidates are bounded to ten; ordinary task/
group/event pages to twenty; default scope/group/member expansions are bounded.

Schema 6–8 pending-intent classification uses the archived migration reason,
so frozen acceptance columns cannot withdraw adoption after candidate spec edits.
A candidate queue membership that contradicts a retained task exclusion aborts
with `membership_migration_conflict`, leaving the database unchanged and its
verified backup available. Resolve the intended scope on a disposable copy and
rerun; the migration never guesses or erases an original exclusion. Archives
recover recorded references, not candidate-only intent that was never persisted.
