---
name: task-mcp-feature-design
description: Research and interactively design captured features. Use for "let's design X", "review design tasks" or "designrev"; not implementation review or sign-off.
---

# Feature design

Follow explicit user instructions over this guidance. Decide whether the work
is worth doing before designing a solution. Retain the user's decisions instead
of asking them to repeat approval. User-requested tasks/results of design are
queued where the user is working; confirmed agent-invented ideas stay in the
inbox until requested for that branch. Respect explicit placement instructions.
Queueing and design discussion start no implementation.

## Find and design the work

Run `init` for the explicit checkout and branch/name. Find a named task with
`list_tasks`, then fetch its complete specification by ID. For "review design
tasks", inspect the project, including inbox briefs and queued unresolved work;
respect a narrower requested scope. Page cards and fetch relevant bodies in
batches of at most 20. Preserve deferred work unless asked to revisit it, and
never implicitly revive dropped/completed tasks.

A `Feature design required (feature-design):` gate, equivalent older brief or
explicit design request identifies the work. Generic blockers/proposals do not.
Work through one feature at a time. If no task exists, fetch
`get_default_skills(name="feature-capture")` and capture it first. Add an active
design gate before revising a mutable task, using the last returned revision.

Read the brief, gates, prerequisites, decisions, `latest_rejection` and relevant
implementation history. Refresh code/documentation rather than relying on stale
capture. Explain current behavior, the desired outcome and whether its value
justifies doing the work at all. If it does, identify material scope, experience
and cost decisions; compare plausible approaches with concrete tradeoffs and
recommend a path. Do not manufacture alternatives for settled/reversible details.

Discuss the decision that constrains the rest first. Answer questions and adapt
to the user's priorities. Fold decisions and reasons into the brief, replacing
superseded text; preserve remaining questions and label assumptions/suggestions.
Audit history keeps prior revisions, so avoid dated discussion/progress logs.

Attribute delivery to its workstream/specification and inspect actual files/
commits before relying on it locally, especially after rebinding. Open work
deliberately integrated from another branch needs a target-local result citing
origin attempt/workstream and source/target commits, target verification and
fresh independent review. Origin review is not inherited; completed proof is
immutable, so later integration requirements need new tasks. Capture this work
when part of agreed scope; design itself records no result/review.

Express real ordering as blockers, not queue position. `add_prerequisite` uses
canonical task/group IDs across projects without sharing scope/proof/code.
Default `milestone="review"` clears on done or current-spec passed/human-reviewed
proof; every member of a nonempty group must satisfy it. Use `signoff` only when
proceeding before the verdict would very likely waste work. Dropped/deferred
blockers stay unsatisfied; rework/spec changes can block links again without
erasing dependent results. Satisfaction means reviewed work exists, not that it
is integrated into the dependent branch. Inspect actual code/commits before
relying on it. Verify IDs/meaning and add real links before resolving an old
prose gate; do not infer dependencies through parsing.

Remove mistaken/obsolete links with `remove_prerequisite(task_id,
expected_revision, blocked_by_id, note)` and the actual decision reason.
It preserves specification, queue and proof; an absent link is a no-op. This
records a dependency decision, not a bypass of unsettled design.

## Save the agreed work

When questions are answered, rewrite the task's goal, boundaries, behavior,
implementation direction, acceptance criteria and rationale with `update_task`,
or split it into independently deliverable tasks. Resolve only settled gates
with accurate notes. Keep material questions/unrelated gates active. User-
requested resulting tasks belong on the current branch; use `queue_task` if
needed, without repeating a decision already made. Confirmed agent ideas remain
in the inbox unless the user asks to queue them. Edits preserve placement;
genuine requirement changes advance spec revision and leave older proof historical.

Present any split's boundaries, criteria and dependencies as part of the design.
`decompose_task` needs an open concrete task with no parent group, attempts,
unresolved items or gate proposals. Keep unsettled work intact; do not clear
questions merely to permit conversion. First save the agreed split, unqueue the
parent, then resolve settled gates and decompose into inbox children. Add member
dependencies/gates before queueing the results where the user is working.
Attempts/membership may require a follow-up structure instead of conversion.

Groups hold context and whole-group completion. `set_scope` can queue an explicit
snapshot of current local members; future membership never changes placement.
Queueing on another branch moves ownership, never duplicates it. Correct mistaken
placement with `unqueue_task`. Report saved task/group IDs, decisions, remaining
questions and eligibility. Implement only when requested and eligible; design
agreement creates neither delivery proof nor independent review/sign-off.

Continue from last returned revisions after writes/pauses. Reconcile conflicts;
inspect the board before retrying uncertain creation/decomposition. No routine
confirming read is needed. Cards/summaries cannot replace a specification.
Whole-field body/criteria edits require its full-spec etag; valid updates return
its successor. Fetch only needed proof with `get_tasks(specification=true,
attempt_ids=[...])` or `get_attempt`, and page deliberate history/membership with
`list_task_attempts`/`list_group_members`.
