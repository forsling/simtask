---
name: task-mcp-superdevloop
description: Implement scoped accepted tasks with a fresh implementer and reviewer.
---

# Superdevloop

Run `init` for the explicit target checkout and branch/name. Resume its `ready`
context; settle `new_branch`, `unregistered_checkout` or `mismatch` through the
init workflow before work. Retain that returned workstream ID, even when the
session is handling other repositories. Use
`get_next_task(workstream_id)` to select one full eligible task without claiming
it. An empty scope or only gated tasks is a useful stop; report its diagnostics.
Do not accept feature briefs or clear design gates to drain the queue. When the
user requests a design discussion, fetch `get_default_skills` and follow
`feature-design`; save exploratory feature ideas through `feature-capture`.
Project ordering is advisory, but scope, acceptance, unresolved items, and
prerequisites are hard gates. Never invent an accepted task or move work into
another workstream. Agent-suggested new work is a pending proposal in this scope
or the inbox. Concrete user-requested additions retain authorization for their
exact scope without another approval; respect explicit unaccepted requests.
Title tasks by observable outcome; keep progress and evidence out of task bodies.
Group references are context, not executable tasks. A shared group expands only
to concrete members owned by this workstream's project; inspect whole-group
progress separately with `get_tasks(group_id)` or `workstream_status`.

Continue using the durable workstream ID and its retained scope/history.
Ordinary selection and execution gates use only its current-spec attempts;
other-workstream or older-spec attempts are attributed history and do not block
competing local implementations. Before relying on recorded proof, inspect the
actual checkout, working diff and relevant commits against the current full
specification, particularly after rebinding. Reuse applicable work as part of
normal implementation; do not add routine checkpoints or repeated repository
scans.

When deliberately merging/cherry-picking an open task's implementation from
another workstream, inspect its origin attempt/source commits and the target's
current full specification, scope and actual checkout. Verify the integrated
target tree and commits. Record an ordinary new target-local `record_result`
whose evidence cites the origin attempt/workstream, actual integrated source
and target commits, target verification and material limits. Then send that new
attempt and target checkout to a fresh independent reviewer. Never copy the
origin review or infer integration from a title, branch name, deployment report
or prose. Recording uses the normal gates and grants no approval/completion.
Completed tasks keep their selected human-approved proof; a later integration
requirement is a new task. No adoption API or shared working state is needed.

Assign the selected task to a fresh implementer. The implementer changes code,
verifies it, and calls `record_result` with concrete evidence and its identity.
This is the first durable attempt record; no start or claim call is required.
Use a fresh reviewer who did not implement it. The reviewer returns findings to
the coordinator, which calls `record_review` with the reviewer's identity and
verdict. If rework is requested, repeat implementation and independent review;
after the workflow's bounded retry cap add a concrete unresolved item explaining
failed rounds and recovery options. `human_review` is reserved for an actual
user review or an explicit user direction to skip further review. Do not
self-issue it. Review passed makes the result ready for human sign-off; it does
not complete the task. Multiple workstreams may record alternatives, which the
user can compare at sign-off.

Task origin/request metadata never grants acceptance. Creation, amendment and standalone
`accept_task` use the same `approval={"basis": "specific" | "delegated", "note":
...}` payload: exact-scope user approval or real authority to select work within
a stated goal, with the actual supporting instruction recorded. Omit approval
for pending or design-first work, even with user origin. No approval clears
other gates or starts implementation. `withdraw_acceptance` is revision-checked
and records a reason without changing spec revision or deleting decisions/proof;
reapproval of an unchanged spec may reuse applicable review. Completed tasks
are immutable. Read full details after compact create/update/accept/withdraw ACKs when
needed; use returned revisions to continue.

Specification amendments use `update_task(..., approval=...)` to save and accept
already-authorized resulting scope in one revision-checked transaction. Reuse
the actual supporting instruction; do not ask for the same approval again.
Omit approval for unsettled or explicitly pending amendments. Every real
title/body/criteria edit changes the spec; no editorial exemption is inferred.
Before replacing body or acceptance_criteria, read the full task once with
`get_tasks(ids=[...])` and pass its `specification_etag` plus the current task
revision. These fields are whole replacements; board rows carry no token.
Title-only edits need no full body read. `get_tasks` currently returns complete
specifications by default; no `specification=true` flag exists. On conflict,
re-read the full spec, reconcile, and use its current revision/token.
Unchanged patches are no-ops unless approval changes; approving an unchanged
pending spec leaves spec revision unchanged. Compact update acknowledgements
report `changed`, `spec_changed`, `approval_changed`, revisions and gates.
Approval grants no scope, prerequisite satisfaction, review, completion or
execution. Attempts and reviews remain proof only for their original spec.

Signoff judges task purpose and delivered quality separately. Present the
approval basis and actual supporting decision beside review evidence. One
informed user decision covers both when appropriate: `approve`, `rework`,
`revise`, `drop` or `defer`. Revise withdraws approval and adds a concrete
question without changing spec revision; drop withdraws approval; defer keeps
it. Direction changes default to human technical quality not judged and preserve
sound proof. Ordinary drop/defer requires no reviewed attempt. Leaving dropped
status requires actual `authorization`; reactivation also needs current spec
approval. Read the packaged `signoff` workflow before recording a verdict.

Shared project order is scheduling metadata, filtered by each workstream's explicit
scope and local eligibility. New tasks append at the end. Reorder only for an
actual scheduling decision: read `list_tasks`/`workstream_status` or `init` for
`project_order_revision`, then call `reorder_tasks(project, task_id, anchor_id,
position="before"|"after", expected_order_revision=..., instruction=...)` to move
one task immediately beside a concrete project anchor. Record the actual
supporting instruction/authority; the configured audit actor is attribution,
not authenticated identity. The compact ACK returns project/task/anchor IDs,
`project_order_revision` and `changed`. A stale order revision requires a fresh
board read and reconciliation; an already-satisfied move changes nothing.
Completed task positions may shift without changing specifications, acceptance,
results or reviews. Reads and selection never reorder work. Do not repeatedly
normalize queues, add automatic priority rules or routine reporting calls;
evaluate excessive reordering after rollout through existing audit events.
