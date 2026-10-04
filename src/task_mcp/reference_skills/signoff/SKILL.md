---
name: task-mcp-signoff
description: Walk through reviewed results and record the user's explicit sign-off verdict.
---

# Sign-off

Follow explicit user instructions over this guidance. "Sign off X" requests a
walkthrough of what was asked, what was built and how it was verified, followed
by the user's verdict. "Approve X" supplies that verdict; explicit approval
without a walkthrough is valid. Only the user approves. If the user says
"reject" with reasons, propose the matching verdict below and confirm it.

Use the scoped card's attempt reference or a known reviewed attempt ID. Fetch
`get_tasks(ids=[...], specification=true, workstream_id=..., attempt_ids=[...])`
once for the complete specification and exact proof. Select a current-spec
passed or explicitly human-reviewed attempt. List and address every recorded
concern, with its source/author; use `concerns_has_more` and totals to retrieve
omitted concerns through deliberate attempt reads rather than treating the
bounded specification window as complete.

Present one task at a time:

- **Asked:** the specified goal, constraints and acceptance criteria.
- **Built:** the delivered behavior and any difference from the specification.
- **Verified:** actual checks, independent review, inspection handles and limits.
- **Value:** is this work worth doing at all, given present needs?
- **Design:** is the specified approach substantially right?
- **Build:** is the result well implemented within that approach?

Judge Value, then Design, then Build on their own evidence before implementation
quality colours the view. Never use an earlier judgment as a premise for the
next. Explain your recommendation and seek the user's verdict; recommend
approval only when all three hold. A faithfully built specification can still
have a serious value/design concern.

Attribute alternatives to their workstream/specification. Check actual
checkout/commit applicability before describing a result as locally delivered,
especially after rebinding. Deliberate integration of open work needs a new
target-local result citing origin attempt/workstream and source/target commits,
target verification and fresh independent review; origin review is not inherited.
Completed proof is immutable; later integration requirements need a new task.

Record the actual verdict with `signoff_task(task_id, expected_revision,
attempt_id, expected_attempt_revision, decision, reasons)`, using the last
returned task and selected attempt revisions:

- `approve`: completes the task. Current-spec passed/human-reviewed proof and
  clear completion gates are required.
- `rework`: fix Build problems within the specification, then record a result
  and obtain fresh review. The selected attempt returns to implementation.
- `revise`: solve the Design problem substantially differently. Reasons become
  an open question; later requirement edits advance `spec_revision`.
- `drop`: close Value failures or obsolete/superseded work without approval.
  If delivered code must be removed, create a queued removal task in the same
  step, preserving the removal scope and the user's reasons.

`reasons` is required for rework/revise and optional for approve/drop. There are
no separate quality judgments. Verdicts preserve queue placement and factual
history. Deferral uses `set_disposition`, not a sign-off verdict. Ordinary
drop/defer needs no reviewed attempt; revival from dropped requires actual
`authorization`. The service records assertions, not authenticated user or
reviewer identity. `human_review` requires actual user review or explicit
direction to skip further review; never self-issue it.

Default `review` blockers clear on done or current-spec passed/human-reviewed
proof, across workstreams; every member of a nonempty group must satisfy them.
Use `milestone="signoff"` only when proceeding before the verdict would very
likely waste work. Dropped/deferred blockers remain unsatisfied; rework/spec
changes can block review links again without erasing dependent results.
Satisfaction proves no integration into the dependent branch. Shared-group
completion still requires every member's human sign-off, with no group verdict.

Continue from last returned revisions after writes and user pauses. Reconcile
conflicts; no routine confirming read is needed. Cards/summaries are not
specifications. Whole-field body/criteria replacements need the full-spec etag;
token-bearing updates return its successor. Fetch only needed proof with
`get_attempt` or chosen `get_tasks` attempt IDs, and page history deliberately
with `list_task_attempts`/`list_events`. Inspect the board before retrying an
uncertain task creation.
