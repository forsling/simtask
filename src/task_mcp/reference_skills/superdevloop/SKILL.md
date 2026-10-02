---
name: task-mcp-superdevloop
description: Implement scoped accepted tasks with a fresh implementer and reviewer.
---

# Superdevloop

Run `init` for the explicit target checkout and branch/name. Resume its `ready`
context; settle `new_branch`, `unregistered_checkout` or `mismatch` through the
init workflow before work. Retain that returned workstream ID, even when the
session is handling other repositories. Use
`get_next_action(workstream_id)` to select one explicit `implement` or `review`
action without claiming it. It includes one full current task/specification with
revisions/read token. `review` includes exactly one complete applicable local
attempt and its evidence/review provenance; no second history read is needed.
`action:null` is a useful stop; report its bounded waiting counts, including human
sign-off. Follow shared project order across both action kinds. Pending local
review precedes another implementation of the same task. Passed/human-reviewed
results wait for the user. Rework returns the attempt and relevant findings.
Do not accept feature briefs or clear design gates to drain the queue. When the
user requests a design discussion, fetch `get_default_skills` and follow
`feature-design`; save exploratory feature ideas through `feature-capture`.
Shared project order determines the next eligible action. Scope, current acceptance,
active disposition, unresolved items and prerequisites gate both autonomous
implementation and review. Never invent an accepted task or move work into
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
or prose. Recording checks mutability, scope, concurrency and the full-spec token;
it grants no approval/completion and never clears the execution gates.
Completed tasks keep their selected human-approved proof; a later integration
requirement is a new task. No adoption API or shared working state is needed.

Dispatch a `review` action directly to a **fresh independent reviewer** who did
not implement the selected attempt. Give that reviewer the returned full task,
selected complete attempt and actual checkout/artifact references. Verify their
applicability before relying on proof, including after restart/rebind. Never
reimplement merely because a previous session ended. The reviewer returns
findings; the coordinator calls `record_review` with the actual reviewer identity
and verdict. Keep explicit/manual review authority rules unchanged; selection
never authorizes blocked work.

Dispatch an `implement` action to a **fresh implementer**, supplying any returned
rework attempt/findings. The implementer inspects the normal checkout/diff and
relevant history, reuses applicable work, changes code and verifies it. On a
finished durable attempt call `record_result` once with the last-read task
revision, `specification_etag`, actual implementer/summary/context `evidence`,
`artifacts=[{"kind": "commit", "reference": <actual hash>} ]` (or kind
`artifact` with the actual path/URL)
and a `verification` string with the actual checks/outcomes and limits. The ACK
returns attempt `id`/`revision`, `task_revision`, unchanged `accepted`/`status`
and active gate diagnostics; it does not echo evidence. Then select again and
send the `review` action to a fresh reviewer. No start/claim/checkpoint is needed.

If interrupted work is durably implemented but unrecorded, read the current full
spec and inspect the actual checkout/commits before factual recording. This rare
recovery may record proof while unaccepted, unresolved, prerequisite-blocked,
deferred or dropped; it never accepts, resumes, clears gates, completes a
prerequisite or manufactures review. Do not use factual recording as permission
to start autonomous implementation. Existing Git files/history are ordinary
continuation evidence; add no routine extra scan, partial-progress log or
external historical-example repair.

If rework is requested, repeat eligible implementation and independent review;
after the workflow's bounded retry cap add a concrete unresolved item explaining
failed rounds and recovery options. `human_review` is reserved for an actual
user review or an explicit user direction to skip further review. Do not
self-issue it. Review passed makes the result ready for human sign-off only when
all completion gates permit it; it does not complete the task. Multiple
workstreams may record alternatives, which the user can compare at sign-off.

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
