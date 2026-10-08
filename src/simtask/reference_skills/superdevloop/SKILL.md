---
name: superdevloop
description: Implement and independently review workstream tasks one at a time, with a fresh implementer and a fresh reviewer for each. Use in projects using simtask when the user asks to work through a workstream.
---

# Superdevloop

You coordinate one workstream's implementation and independent review. The
loop ends at review; sign-off is the user's (task-signoff).

1. Call `get_next_action` for the workstream; if it returns none, report what
   is waiting and stop. Never resolve questions, remove blockers or change
   membership to make work eligible.

2. For an implement or rework action, dispatch a fresh implementer with the full
   specification, review findings and latest rejection reasons. They first
   inspect the actual checkout and recent commits, reusing work an interrupted
   attempt left. Results from another workstream or an older specification are
   history; reusing them needs a new local result and review.

3. The implementer builds what the task specifies; open choices are theirs. If
   the approach looks substantially wrong, they record a worth-doing or approach
   concern and carry on, never improvising another design. For a bug, they
   demonstrate it before changing code, or else add a question
   (`add_unresolved`) listing the steps tried and move on. If unexpectedly
   blocked, they add a blocker (`add_prerequisite`) or question and move on.

4. The implementer records the result (`record_result`), naming the artifact
   paths and the exact command that takes the user to the thing being judged.
   If none exists, creating it is part of the task.

5. For a review action, dispatch a fresh reviewer who did not implement the
   attempt. They check the work against the specification and judge how well
   it is built. Anything an implementer could fix without changing what the
   task says is rework; doubts about the task itself are concerns. When
   following the specification faithfully causes a real problem, pass with a
   serious concern. "Nice to also add X" is a new idea, not a concern.

6. Record the verdict, findings, concerns and evidence handles with
   `record_review`. Rework returns to step 2.

7. Record an unrelated failing check or environment problem once, as its own
   task. Reviewers cite it rather than send work back when the task's own checks
   pass; work it genuinely blocks gets it as a blocker. A cleared blocker means
   reviewed work exists, not that it is in this branch.

8. Repeat from step 1.
