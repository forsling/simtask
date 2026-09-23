---
name: task-mcp-signoff
description: Present reviewed implementation attempts for informed human sign-off.
---

# Sign-off

Fetch the full task and reviewed attempts. Present what was built, the actual
verification and review evidence, material limitations, and the exact attempt
the user is judging. Ask for the user's informed verdict. Call `signoff_task`
only after that explicit verdict, selecting one passed or human-reviewed
attempt. Approval completes the task. Rejection with `rework` means the accepted
specification remains correct and the attempt needs repair and fresh review.
Rejection with `revise` invalidates acceptance and adds an unresolved item for
the requested change. Group completion derives from every member's completion;
there is no separate group sign-off. The service records assertions but cannot
authenticate the user or prove reviewer independence.
