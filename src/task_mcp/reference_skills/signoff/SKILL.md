---
name: task-mcp-signoff
description: Walk through reviewed results and record the user's explicit sign-off verdict.
---

# Sign-off

Follow explicit user instructions over this guidance. Sign-off is a workflow,
not a verdict. "Sign off X" requests what was asked, built and verified, then
the user's verdict. "Approve X" supplies it; explicit approval without a
walkthrough is valid. Only the user approves. For "reject" with reasons,
propose the matching verdict below and confirm the mapping.

Fetch `get_tasks(ids=[...], specification=true, workstream_id=...,
attempt_ids=[...])` for full spec and the scoped or known reviewed attempt.
Select current-spec passed independent review (or an earlier human review).
If missing, arrange/record fresh independent review before sign-off;
your verification is not independent review. Only when the user explicitly
approves may `approve` accept a current-spec result awaiting independent review;
its reasons must say so. Never self-issue that approval.

Address every recorded concern with source/author. Use `concerns_has_more` and
totals to page `list_task_attempts` and fetch omitted concern-bearing proof.
The bounded spec window is not complete; concerns are not automatic blockers.

Present one task at a time:

- **WHAT WAS ASKED:** goal, constraints and acceptance criteria.
- **Built:** delivered behavior and differences from the specification.
- **Verified:** actual checks, independent review, inspection handles and limits.
- **Value:** is the work worth doing at all, given present needs?
- **Design:** is the specified approach substantially right?
- **Build:** is the result well implemented within that approach?

Judge Value, Design, then Build separately on their own evidence before Build
colours the view. Never use an earlier judgment as the next premise. Address
concerns in each assessment. Recommend approval only when all three hold.
Faithful delivery can have serious Value/Design doubts; poor choices left open
by the spec are Build rework. Seek the user's verdict.

Attribute alternatives to their workstream/spec; check checkout/commits before
describing local delivery. Integration of open work needs target-local
origin/source/target proof, checks and fresh independent review.
Completed proof is immutable; later integration needs a new task.

Record the actual verdict with `signoff_task(task_id, expected_revision,
attempt_id, expected_attempt_revision, decision, reasons)`, reusing last task/
selected attempt revisions:

- `approve`: completes with current-spec passed/human-reviewed proof and clear
  gates. Review pass never completes a task.
- `rework`: fix Build within the spec, record a new result and obtain fresh
  review. The selected attempt returns to implementation.
- `revise`: solve Design substantially differently. Reasons become an open
  question; later requirement edits advance `spec_revision`.
- `drop`: close Value failures or obsolete/superseded work without approval.
  If code must go, create a removal task in the current workstream in the same
  step, preserving removal scope and the user's reasons.

Carry reasons to the next agent through `latest_rejection`. Rework/revise
require reasons; approve/drop allow them. No judgment fields. Verdicts retain
memberships/history. Use `set_disposition` for deferral; revival needs actual
`authorization`.

Memberships overlap; unfinished attempts/reviews stay branch-local.
**Add to workstream** uses `add_to_workstream(task_id, workstream_id,
expected_revision)` and retains other memberships. **Remove from workstream**
uses `remove_from_workstream` with the same arguments, affecting only that
workstream. Inbox means zero effective memberships; groups/exclusions stay
live. Inclusion starts no implementation. **Reorder tasks** uses
`reorder_tasks(workstream_id, task_ids, expected_order_revision)`: one local
prefix, retaining unlisted relative order. Reuse `workstream_order_revision`
without routine reads/reshuffling; other lists stay intact.

Default review prerequisites clear on done/current-spec passed/human-reviewed
proof across workstreams, for every member of a nonempty group. Use
`milestone="signoff"` only when proceeding before the verdict would very likely
waste work. Dropped/deferred blockers stay unsatisfied; rework/spec changes can
block links again. Satisfaction proves no branch integration. Group completion
requires every member's human sign-off; no group verdict.

Reuse last revisions after writes/pauses; reconcile conflicts. Cards are not
specs. Whole body/criteria edits need the full-spec etag; valid updates return
its successor. Fetch chosen proof; page needed history. Inspect the board
before retrying uncertain removal-task creation.
