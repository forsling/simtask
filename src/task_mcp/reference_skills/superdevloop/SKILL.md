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
user requests a design discussion, fetch `get_default_skills(name="feature-design")` and follow
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
progress separately with `get_tasks(ids=[group_id])` or `workstream_status`.

Continue using the durable workstream ID and its retained scope/history.
Ordinary selection and execution gates use only its current-spec attempts;
other-workstream or older-spec attempts are attributed history and do not block
competing local implementations. Prerequisites are a separate canonical milestone:
default `review` links clear on done or any current-spec `passed`/`human_review`
attempt, across workstreams. Every member of a nonempty group must satisfy it.
Explicit `milestone="signoff"` is a rare exception only when proceeding before
the user's verdict would very likely waste work. Dropped/deferred blockers remain
unsatisfied. Rework/spec changes can block review links again; dependent results
survive. Inspect `milestone`, `satisfied` and `blocking` in prerequisite references
to explain waits. A cleared milestone proves no integration into this checkout.
Before relying on recorded proof, inspect the
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


Use last returned entity revisions after user pauses and successful writes. No
routine read-before-write or confirming read is needed. Fetch and reconcile for
conflicts, uncertainty or missing information; inspect the board before retrying
an uncertain creation. Cards never carry body previews or replacement tokens.
For whole-field body/criteria replacements use the full-specification etag from
your complete read or create/update acknowledgement. Valid token-bearing updates
return a refreshed token; unchanged specifications retain it. Title/summary-only
edits need no full read. Summary edits preserve spec acceptance and proof.
Retrieve exactly needed proof with `get_tasks(specification=true, attempt_ids=[...])`
or `get_attempt`; page deliberate history/membership with `list_task_attempts`
and `list_group_members`. Never write a card or summary back as a specification.
