---
name: task-mcp-signoff
description: Present reviewed implementation attempts for informed human sign-off.
---

# Sign-off

Fetch the full task with `get_tasks(ids=[...])` and select its passed or
explicitly human-reviewed current-spec attempt. Present the task's purpose,
`acceptance_basis`, supporting `approval_decision` (actual note and decision
reference), and whether it is currently accepted separately from the result,
verification, independent review and material limitations. Specific approval
already answers purpose for this scope and can be reused. Delegated or unknown
approval needs an actual purpose judgment now; never infer it from origin or
old prose. One informed approval may cover both purpose and result. Do not ask
for two ceremonial confirmations.

Attribute every alternative to its workstream and specification revision.
Check actual checkout/commit applicability before presenting it as locally
delivered, especially after rebinding. An open task deliberately integrated
elsewhere needs its own target-local result with origin/source/target provenance,
target verification and fresh independent review; origin review is not inherited.
Completed tasks retain their selected human-approved result. A later integration
requirement is a new task, never a rewrite of completed proof.

Only after the user's explicit informed verdict call `signoff_task` with the
last-read task `expected_revision`, selected `attempt_id` and its
`expected_attempt_revision`, actual `user_note`, and one `decision`:

- `approve`: accepts purpose and result and completes the task. Current
  acceptance, applicable passed/human-reviewed attempt and clear completion
  gates are required. Completed tasks are immutable.
- `rework`: purpose remains approved; repair this implementation, record a new
  result and obtain fresh review. The selected attempt goes to rework.
- `revise`: withdraw approval and pass the actual concrete
  `specification_question`. The sound reviewed result survives. Recording this
  verdict does not edit requirements or advance `spec_revision`; later real
  edits do.
- `drop`: deactivate approval and keep history, context and proof. Revival
  requires an actual supporting authorization and current specification approval.
- `defer`: pause while retaining approval, context and proof for resumption.

`revise`, `drop` and `defer` leave human result quality `not_judged` by default.
Only when the user separately supplied an actual technical judgment pass
`result_judgment="accepted"` or `"rework"` and the actual `result_note`.
Independent review remains its own evidence; a direction change alone does not
make a sound attempt rework. `approve` itself covers purpose and result;
`rework` itself retains purpose and judges the implementation in need of repair.
The service records assertions, not authenticated identity or proven reviewer
independence. Do not sign off on behalf of the user.

Signoff and ordinary `set_disposition` return compact IDs, revisions, disposition
and any new `unresolved_id`; signoff also returns `decision_ref` and selected
attempt continuation state. Use deliberate `get_tasks` reads for proof,
`approval_decision` and structured `signoff_decisions`, or detailed `list_events`
for original audit snapshots. Ordinary drop/defer needs no reviewed attempt;
drop deactivates approval, defer retains it, and leaving dropped status requires
`authorization` with the actual revival instruction. Neither dropped nor
deferred work satisfies a prerequisite. Shared-group completion still needs all
members human signed off across projects; there is no separate group sign-off.

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
