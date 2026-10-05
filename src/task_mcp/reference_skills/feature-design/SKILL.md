---
name: task-mcp-feature-design
description: Research and interactively design captured features. Use for "let's design X", "review design tasks" or "designrev"; not implementation review or sign-off.
---

# Feature design

Follow explicit user instructions over this guidance. Decide whether work is
worth doing before designing a solution; retain prior user decisions.
User-requested tasks and resulting agreed work belong in the current workstream;
confirmed agent ideas may stay in the inbox. Respect explicit placement.
Membership/design discussion start no implementation.

## Find and design

Run `init` for the explicit checkout and branch/name. Find named work with
`list_tasks`, then fetch its complete spec. For "review design tasks", include
inbox briefs and included unresolved work unless the user narrows scope.
Page cards; batch relevant bodies up to 20. Preserve deferred work unless asked
to revisit it; never implicitly revive dropped/completed tasks.

A `Feature design required (feature-design):` gate, equivalent older brief or
explicit design request identifies work. Generic blockers do not. Work through
one feature at a time. If missing, fetch `get_default_skills(name="feature-capture")`
and capture first. Add an active design gate before revising mutable work,
using its last revision.

Read gates, prerequisites, decisions, `latest_rejection` and relevant history.
Refresh code/documentation rather than relying on stale capture. Explain current
behavior, desired outcome and whether value justifies the work. Then compare
material scope, experience and cost choices with concrete tradeoffs and a
recommendation. Do not manufacture alternatives for settled/reversible details.

Discuss the decision constraining the rest first. Adapt to user priorities.
Fold decisions/reasons into the brief, replacing superseded text; preserve
remaining questions and label assumptions/suggestions. Audit history retains
earlier versions, so avoid discussion logs in the specification.

Attribute delivery to its workstream/spec and check actual files/commits before
relying on it locally, especially after rebind. Deliberate integration of open
work needs a target-local result with origin attempt/workstream, source/target
commits, target checks and fresh independent review. Origin review is not
inherited; completed proof is immutable, so later integration needs a new task.
Design itself records no delivery result/review.

## Save agreed scope

Rewrite goal, boundaries, behavior, implementation direction, criteria and
rationale with `update_task`, or split independently deliverable tasks. Resolve
only settled gates with accurate notes; keep material questions/unrelated gates
active. Requirements changes advance spec revision and leave old proof historical.
Edits retain all memberships.

**Add to workstream** is `add_to_workstream(task_id, workstream_id,
expected_revision)`: include agreed user-requested results where the user works,
retaining other memberships and prior authorization. **Remove from workstream**
uses `remove_from_workstream` with the same arguments and removes only the named
membership. A task can be in A and B; unfinished attempts/reviews stay branch-local.
Inbox means zero effective memberships. Confirmed agent ideas may stay there.
No scope edit transfers work between workstreams.

Present split boundaries, criteria and dependencies. `decompose_task` needs an
open concrete task with no parent group, attempts, unresolved items or gate
proposals. It retains context and creates members in parent scopes, inheriting
prerequisites. Resolve genuinely settled gates first; do not clear questions
merely to permit conversion. If children need new gates before inclusion,
create them outside included groups, gate them, then attach/add them instead.
Attempts/existing structure may require follow-up tasks. Groups hold context/
whole-group completion, not attempts. `set_scope` preserves live group references
and exclusions; future local members enter included workstreams unless excluded.
Other workstreams remain intact.

Use `add_prerequisite` for dependencies, including canonical task/group IDs
across projects. Default `milestone="review"` clears on done or current-spec
passed/human-reviewed proof; every member of a nonempty group must satisfy it.
Use `signoff` only when proceeding before the verdict would very likely waste
work. Dropped/deferred blockers stay unsatisfied; rework/spec changes can block
links again without erasing dependent results. Satisfaction proves reviewed work
exists, not branch integration. Verify IDs/meaning before replacing prose gates;
remove mistaken/obsolete links with `remove_prerequisite(task_id,
expected_revision, blocked_by_id, note)` and the actual reason.

Use local order for scheduling intent. **Reorder tasks** is
`reorder_tasks(workstream_id, task_ids, expected_order_revision)`: one exact
prefix; unlisted members keep relative order. Reuse returned
`workstream_order_revision`; no routine preflight/reshuffling. Other lists stay
intact; new inclusions append deterministically.

Report IDs, decisions, remaining questions and eligibility. Implement only when
requested and eligible; agreement creates no proof/review/sign-off. Reuse last
revisions after writes/pauses; reconcile conflicts and inspect the board before
retrying uncertain creation/decomposition. Cards are not specs. Whole body/
criteria edits need the full-spec etag; valid updates return its successor.
Fetch chosen proof with `get_tasks` attempt IDs or `get_attempt`; page
`list_task_attempts`/`list_group_members` deliberately.


For new tasks/groups, choose a stable descriptive `public_id`, for example
`readable-task-ids`; decomposition members accept the same field. Names are globally
unique and cannot be reused for closed tasks. If creation returns
`public_id_conflict`, choose a more specific name and retry. Use the returned public
ID in all references; existing `tsk_…` references remain unchanged. Title/spec edits
never rename a task.
