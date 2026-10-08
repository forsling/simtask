---
name: task-design
description: Research and design captured features with the user. Use in projects using simtask for "let's design X", "review design tasks" or "designrev"; not for implementation review or sign-off.
---

# Task design

1. Find the work: the named task or, for "review design tasks", every inbox or
   workstream task with a `Feature design required (task-design):` question.
   Older briefs say `(feature-design)` instead; treat them the same. Other
   questions alone do not make a task design work. Leave deferred tasks alone
   unless asked. If the idea was never captured, follow task-capture first.

2. Fetch the full specification, questions, blockers and latest rejection.
   Use the stable public task ID in discussion and signoff; existing tsk_ IDs
   remain unchanged. New tasks have descriptive public IDs.
   Refresh your understanding of the current code and documentation rather
   than trusting the capture.

3. Decide with the user whether the work is worth doing before designing a
   solution. If it is not, propose dropping or deferring it.

4. Work through one decision at a time, starting with the one that constrains
   the rest. Explain the options with concrete tradeoffs and a recommendation.
   Do not invent alternatives for settled or easily reversible details.

5. As questions are answered, use `update_task` to rewrite the specification
   so it states the agreed outcome, scope, approach, acceptance criteria and
   rationale. Replace superseded text rather than logging the discussion.
   Resolve only the settled questions with `resolve_unresolved`; keep the rest
   open.

6. Split independently deliverable parts into separate tasks (`create_task` or
   `decompose_task`), and add the results to the workstream where the user is
   working. Express a real ordering between them with `add_prerequisite`. Add a
   sign-off blocker, or remove any blocker, only when the user directs.

7. Report the task IDs, decisions, remaining questions and whether the work is
   now eligible. Design records no result and starts no implementation unless
   the user asks.
