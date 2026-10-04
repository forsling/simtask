---
name: task-mcp-proposal-review
description: Review ordinary inbox proposals and unresolved items with the user; route feature design to feature-design.
---

# Proposal and unresolved-item review

Follow explicit user instructions over this guidance. Fetch `feature-design`
through `get_default_skills` for an explicit design request/brief or
`Feature design required (feature-design):` gate. A generic blocker alone does
not imply design. Saving an exploratory feature, including "add a design task",
uses `feature-capture` first. Judging delivered Value, Design and Build belongs
to `signoff`, rather than treating recorded concerns as automatic blockers.

After `init`, use `list_tasks` and relevant full specifications to present inbox
proposals and unresolved items with their goal, scope, criteria and questions.
Decide whether proposed work is worth doing before choosing its design.
User-requested tasks are queued on the current branch; confirmed agent-invented
ideas go to the inbox. Respect explicit placement instructions. Concrete requests
authorize their exact specification without another confirmation. Use
`create_task(workstream_id=...)` for settled new work or `queue_task` for existing
work; queueing starts no implementation and clears no other gate. Gate exploratory
briefs before queueing through feature-capture.

Resolve only settled items, folding decisions/reasons into the specification
and replacing superseded wording. Edits retain placement; real requirements
advance spec revision and leave prior proof historical. Preserve deferred
context/history. Source/user_request are descriptive, never extra authority.
Correct mistaken placement with `unqueue_task`, preserving requirements/proof.

A session handling a task adds necessary unresolved items/prerequisites
directly; an observer uses `handling="observer"` to propose a nonblocking gate.
The active coordinator accepts only valid proposals with `accept_gate_proposal`,
or uses `dismiss_gate_proposal` with the actual reason for stale/unwanted ones,
including proposals whose targets were dropped. Resolve IDs explicitly.
Unresolved questions materially risk wasted work or need user-only decisions;
routine reversible choices belong to the implementer.

Express real ordering as blockers, not queue position. `add_prerequisite` links
canonical tasks/groups across projects without sharing scope/proof/code.
Default `milestone="review"` clears on done or any current-spec passed/human-
reviewed attempt, for every member of a nonempty group. Use `signoff` only when
proceeding before the user's verdict would very likely waste work. Dropped/
deferred blockers remain unsatisfied; rework/spec changes can block links again
without erasing dependent results. A satisfied blocker proves reviewed work
exists, not integration into the dependent branch.

Inspect reference identity/milestone/satisfaction; fetch remote requirements or
proof only when needed. Verify IDs/meaning and add real links before resolving
an old prose gate. Do not parse prose into dependencies. Remove mistaken/
obsolete links with `remove_prerequisite(task_id, expected_revision,
blocked_by_id, note)` and the actual decision reason. Removal preserves spec,
queue and proof; absent links are no-ops. Dismiss pending proposals through
proposal dismissal instead. Never remove real blockers just to make work eligible.

Resolve settled questions/proposals before converting a task to a group.
Groups hold context/whole-group completion, with execution gates on concrete
members. `create_group` starts an empty scoped group; `list_groups` discovers it
through member projects. Creating/attaching a member, including from another
project, requires the current group revision. Completed requirements/proof stay
immutable; changed needs become new tasks.

Continue from last returned revisions after writes/pauses. Reconcile conflicts;
inspect the board before retrying uncertain creation. No routine confirming
read is needed. Cards/summaries cannot replace a specification. Whole-field
body/criteria edits need the full-spec etag; valid updates return its successor.
Fetch only needed proof with `get_tasks(specification=true, attempt_ids=[...])`
or `get_attempt`, and page deliberate history/membership with
`list_task_attempts`/`list_group_members`.
