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

Task origin/request metadata never grants acceptance. Creation and standalone
`accept_task` use the same `approval={"basis": "specific" | "delegated", "note":
...}` payload: exact-scope user approval or real authority to select work within
a stated goal, with the actual supporting instruction recorded. Omit approval
for pending or design-first work, even with user origin. No approval clears
other gates or starts implementation. `withdraw_acceptance` is revision-checked
and records a reason without changing spec revision or deleting decisions/proof;
reapproval of an unchanged spec may reuse applicable review. Completed tasks
are immutable. Read full details after compact create/accept/withdraw ACKs when
needed; use returned revisions to continue.
