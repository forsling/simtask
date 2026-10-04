---
name: task-mcp-proposal-review
description: Review ordinary inbox proposals and unresolved items with the user; route feature design to feature-design.
---

# Proposal and unresolved-item review

Follow explicit user instructions over this guidance. Fetch `feature-design`
through `get_default_skills` for explicit design requests/briefs or a
`Feature design required (feature-design):` gate. A generic blocker does not
imply design. Saving exploratory features, including "add a design task", uses
`feature-capture`. Judging delivered Value, Design and Build belongs to `signoff`;
recorded concerns are not automatic blockers.

After `init`, use `list_tasks` and relevant full specs to present proposals/
questions with goal, scope, criteria and decisions. Decide worth-doing before
design. Concrete requests authorize their recorded spec without reconfirmation.
Create settled work with `create_task(workstream_id=...)`; gate exploratory
briefs through feature-capture before inclusion.

User-requested work belongs in the current workstream; confirmed agent ideas
may stay in the inbox. Respect explicit placement. **Add to workstream** uses
`add_to_workstream(task_id, workstream_id, expected_revision)`; adding B retains
A. **Remove from workstream** uses `remove_from_workstream` with the same
arguments and affects only that workstream. Inbox means zero effective
memberships. Groups/exclusions stay dynamic; edits retain memberships and
unfinished attempts/reviews remain branch-local. Inclusion starts no
implementation and clears no design gate.

Resolve only settled items; fold decisions/reasons into the spec, replacing
superseded text. Requirements advance spec revision and leave old proof
historical. Preserve deferred context/history. Source/user_request are descriptive,
never extra authority.

Add necessary unresolved items/prerequisites directly; resolve IDs explicitly. Questions should risk material waste or need user-only
decisions; routine reversible choices belong to the implementer.

Use `add_prerequisite` for dependencies across canonical tasks/groups without
sharing scope/proof/code. Default `milestone="review"` clears on done or
current-spec passed/human-reviewed proof; every member of a nonempty group must
satisfy it. Use `signoff` only when proceeding before the verdict would very
likely waste work. Dropped/deferred blockers stay unsatisfied; rework/spec changes
can block links again without erasing dependent results. Satisfaction proves
reviewed work exists, not integration into the dependent branch.

Inspect identity/milestone/satisfaction; fetch remote specs/proof when needed.
Verify IDs/meaning before replacing prose gates with links. Remove mistaken/
obsolete links with `remove_prerequisite(task_id, expected_revision,
blocked_by_id, note)` and the actual reason. Memberships/spec/proof survive;
absent links are no-ops. Never remove real
blockers merely to make work eligible.

Use local order for scheduling intent. **Reorder tasks** is
`reorder_tasks(workstream_id, task_ids, expected_order_revision)`: one exact
prefix; unlisted members keep relative order. Reuse returned
`workstream_order_revision` without routine reads/reshuffling. Other lists stay
intact; new inclusions append deterministically.

Settle questions before decomposition. Groups hold context/whole-group
completion; concrete members hold execution gates. Creating members
(`create_task(group_id=...)`) or attaching them (`update_task(group_id=...)`)
requires the current group revision and respects dynamic inclusion/exclusions.
Completed requirements/proof are immutable; changed needs become new tasks.

Reuse last revisions after writes/pauses; reconcile conflicts and inspect the
board before retrying uncertain creation. No confirming read is needed. Cards
are not specs. Whole body/criteria edits need the full-spec etag; valid updates
return its successor. Fetch chosen proof with `get_tasks` attempt IDs or
`get_attempt`; page `list_task_attempts` or `list_tasks(group_id=...)` deliberately.
