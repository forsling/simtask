---
name: task-signoff
description: Walk the user through a reviewed task result and record their verdict. Use in projects using Task MCP for "sign off X", "approve X" or "reject X".
---

# Task sign-off

Sign-off is a workflow, not a verdict: present what was asked, what was built
and how it was verified, assess it, recommend, then take the user's verdict.
"Sign off X" means run the walkthrough below. "Approve X" and "reject X" are
verdicts; an explicit approval without a walkthrough is valid. Record only the
verdict the user gave, never one they did not.

1. Fetch the task's full specification and its reviewed attempt with
   `get_tasks`. Sign-off needs a passed independent review of the current
   specification; if there is none, arrange one first. Your own checks are not
   an independent review.

2. Open with the evidence handles the implementer and reviewer recorded: the
   artifact paths and the exact command that takes the user to the thing being
   judged.

3. Present one task at a time: what was asked (goal, constraints, acceptance
   criteria), what was built (including any difference from the specification)
   and how it was verified (checks, independent review, limits).

4. Answer three questions in this order, judging each independently on its own
   evidence rather than as a premise for the next:
   - **Worth doing?** Should this exist; does it solve a real problem for the
     project?
   - **Right approach?** Is this the best way to solve it?
   - **Built well?** Is it implemented properly within that approach?

5. List every recorded concern with its author and address each one under the
   question it bears on. Fetch any concerns the response says were left out.

6. Recommend approval only when all three questions hold; otherwise recommend
   the matching verdict. Then ask for the user's verdict.

7. Record it with `signoff_task`:
   - **approve:** all three hold. Even after an explicit approval, show any
     still-open prerequisite and ask whether it affects the verdict before
     recording. Open questions must be answered first.
   - **rework:** "Built well?" failed; the work goes back to the implementer.
   - **revise:** "Right approach?" failed; the reasons become an open question.
   - **drop:** "Worth doing?" failed, or the work is obsolete or superseded. If
     its code must go, create a removal task in the current workstream in the
     same step (`create_task`).

8. If the user says "reject" with reasons, propose the matching verdict and
   confirm it before recording.
