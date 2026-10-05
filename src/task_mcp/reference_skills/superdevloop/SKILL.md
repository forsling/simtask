---
name: superdevloop
description: Implement and independently review workstream tasks one at a time, with a fresh implementer and a fresh reviewer for each. Use in projects using Task MCP when the user asks to work through a workstream.
---

# Superdevloop

You coordinate implementation and independent review for one workstream. The
loop ends at review; sign-off belongs to the user through task-signoff.

1. Call `get_next_action` for the workstream. When it returns no action, report
   what is waiting and stop. Never resolve questions, remove blockers or change
   membership just to make more work eligible.

2. For an implement or rework action, dispatch a fresh implementer with the full
   specification, any review findings and the latest rejection reasons. The
   implementer first inspects the actual checkout and recent commits, reusing
   work an interrupted attempt left behind. Results from another workstream or
   an older specification are history; reusing that work here needs a new local
   result and a fresh review.

3. The implementer builds what the task specifies; choices the specification
   leaves open are theirs. If the approach looks substantially wrong, they
   record a worth-doing or approach concern and carry on, never improvising
   another design. For a bug, they demonstrate it before changing code; if they
   cannot, they add a question (`add_unresolved`) listing the steps tried and
   move on. If unexpectedly blocked, they add a blocker (`add_prerequisite`) or
   question and move on.

4. The implementer records the result with `record_result`, naming the artifact
   paths and the exact command that takes the user to the thing being judged.
   If no such shortcut exists, creating it is part of the task.

5. For a review action, dispatch a fresh reviewer who did not implement the
   attempt. The reviewer treats the agreed approach as substantially correct and
   judges whether the work is built well within it. For each problem: could an
   implementer fix this unattended without changing what the task says (goal,
   scope, the approach decided in the specification, acceptance criteria)? Then
   it is rework; otherwise it is a concern. When faithfully following the
   specification causes a real problem, pass with a serious concern. "It would
   be nice to also add X" is a new idea, not a concern.

6. Record the reviewer's verdict, findings, concerns and the evidence handles
   with `record_review`. Rework returns to step 2 with those findings.

7. Record an unrelated failing check or environment problem once, as its own
   task. Reviewers cite it and do not send work back when the task's own checks
   pass; work it genuinely blocks gets it as a blocker. A cleared blocker means
   reviewed work exists, not that it is in this branch.

8. Repeat from step 1.
