---
name: task-mcp-superdevloop
description: Implement queued tasks with a fresh implementer and independent reviewer.
---

# Superdevloop

Follow explicit user instructions over this guidance. Run `init` for the explicit
checkout and branch/name; resolve setup through the init workflow, then retain
its workstream ID. Use `get_next_action(workstream_id)` to select one `implement`
or `review` action. It returns the full current spec/read token and
`latest_rejection`; review also includes exactly one complete local attempt,
its proof/concerns and provenance. No second history read or claim is needed.
At `action:null`, report bounded waiting counts, including human sign-off.

Follow shared project order across eligible actions. Pending local review
precedes another implementation of that task; passed/human-reviewed results
wait for the user. Queue placement, active disposition, unresolved items and
prerequisites gate both action kinds. Never clear a real gate or move work just
to drain the queue. User-requested additions go on the current branch without
reconfirming settled scope; confirmed agent ideas go to the inbox. Respect
explicit placement instructions. Fetch `feature-capture`/`feature-design` through
`get_default_skills` for exploratory work/design discussions. Queueing starts
no implementation. Put scope/questions in task bodies and progress/proof in
attempts; group references are context, not executable tasks.

## Implement the specification

Dispatch implementation to a **fresh implementer**, with the complete spec,
returned rework proof/findings and latest rejection reasons. Inspect the actual
checkout/diff and relevant commits, reusing applicable work after interruption
or rebind. Build what the specification says. If its design appears substantially
wrong, record a value/design concern and carry on rather than inventing another
design. If unexpectedly blocked, add a concrete blocker or unresolved question
and move on; keep that factual handoff in the task, not a partial-progress log.

On a finished durable result call `record_result` once with the last returned
task revision, `specification_etag`, actual implementer/summary/context evidence,
concrete `artifacts=[{kind: "commit"|"artifact", reference: ...}]` and
`verification` describing actual checks, outcomes and limits. Optional
`concerns=[{kind: "value"|"design", text: ...}]` records doubts that need a
specification change. The ACK gives attempt/task revisions and gates, omitting
proof/concern prose. Select again and dispatch review; no start/checkpoint is needed.

Factual recovery of an already durable but unrecorded result requires checking
the current full spec and actual artifacts first. Recording is possible while
inbox, blocked, deferred or dropped, but grants no execution authority, queues
nothing and clears no gates. Do not use that recovery path to start blocked work.

## Review Build independently

Dispatch a selected review directly to a **fresh independent reviewer** who did
not implement the attempt. Supply the complete task, exact proof/concerns,
latest rejection and actual checkout/artifact references. Check applicability
before trusting the result. Treat the agreed design as substantially correct
and judge Build within it.

Use this test: could an implementer fix the problem unattended without changing
the task's goal, scope, decided design or acceptance criteria? If so, it is
`rework`; otherwise it is a value/design concern. Choices left open by the spec
belong to the implementer, so a poor choice is rework. When faithfully following
the spec causes a real problem, pass a sound Build with a serious concern.
Never fail review over a Value or Design doubt. "It would be nice to add X" is
a new idea, not a concern; ask whether to capture it rather than expanding scope.

The coordinator records the actual independent identity, `pass`/`rework`,
findings in `note` and optional structured concerns with `record_review`, using
the last attempt revision. Concerns retain contributor attribution, preserve
implementer concerns and affect no gate/verdict. Review pass is readiness for
human sign-off when other gates permit, never task completion. Only the user
approves through `signoff`. `human_review` requires actual user review or explicit
direction to skip further review; never self-issue it. Rework repeats eligible
implementation and fresh review, using the latest reasons; if repeated rounds
exhaust normal recovery, save a concrete unresolved question and move on.

## Blockers and branch applicability

Express real ordering with `add_prerequisite`, not queue position. Default
`milestone="review"` clears on done or any current-spec passed/human-reviewed
attempt across workstreams; every member of a nonempty group must satisfy it.
Use `signoff` only when proceeding before the verdict would very likely waste
work. Dropped/deferred blockers stay unsatisfied; rework/spec changes can block
links again while dependent results survive. Inspect milestone/satisfaction in
returned references. A satisfied blocker means reviewed work exists, not that it
is integrated into this branch. Remove only mistaken/obsolete links with
`remove_prerequisite(task_id, expected_revision, blocked_by_id, note)` and the
actual decision reason; removal preserves specifications, placement and proof.

Ordinary selection uses current-spec local attempts; other-workstream/older-spec
proof is attributed history, not a competing local execution gate. Deliberate
merge/cherry-pick of open work requires inspecting origin proof/source commits,
the target's full spec and actual checkout, then verifying the integrated tree.
Record a new target-local result citing origin attempt/workstream, actual
source/target commits and target verification/limits, then obtain fresh review.
Never inherit origin review or infer integration from names/prose. Completed
selected proof is immutable; later integration needs a new task.

Continue from last returned revisions after writes/pauses. Reconcile conflicts;
inspect the board before retrying uncertain creation. No routine confirming
reads or repeated repository scans are needed. Cards/summaries are not specs.
Whole-field body/criteria replacements need the full-spec etag; valid updates
return its successor. Fetch only missing requirements/exact proof with
`get_tasks(specification=true, attempt_ids=[...])` or `get_attempt`; page
history/membership deliberately with `list_task_attempts`/`list_group_members`.
