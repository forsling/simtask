---
name: task-mcp-signoff
description: Present reviewed implementation attempts for informed human sign-off.
---

# Sign-off

Fetch the full task and reviewed attempts. Present what was built, the actual
verification and review evidence, material limitations, and the exact attempt
the user is judging. Ask for the user's informed verdict. Call `signoff_task`
only after that explicit verdict, selecting one passed or human-reviewed
attempt. Approval completes the project-owned task. Rejection with `rework` means the accepted
specification remains correct and the attempt needs repair and fresh review.
Once complete, the task is immutable; capture later requirements as new tasks.
Rejection with `revise` invalidates acceptance and adds an unresolved item for
the requested change. A shared group's completion derives from every member's
completion across its projects; local task completion alone may leave it open.
There is no separate group sign-off. The service records assertions but cannot
authenticate the user or prove reviewer independence.

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
