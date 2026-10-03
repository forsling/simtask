---
name: task-mcp-signoff
description: Present reviewed implementation attempts for informed human sign-off.
---

# Sign-off

Use the scoped card's relevant attempt reference, or the known reviewed attempt ID,
and request `get_tasks(ids=[...], specification=true, workstream_id=...,
attempt_ids=[...])` once for the full specification and exactly that proof.
Select its passed or explicitly human-reviewed current-spec attempt. Present
one current task at a time with these explicit fields:

- **Asked:** the approved goal and constraints.
- **Built:** the actual delivered behavior and its difference from that goal.
- Actual verification and independent review, with fitting inspection handles.
- What the user should judge, material limitations and your recommendation.
- A judgment question seeking the informed explicit verdict.

Present the task's purpose and current specification separately from result,
verification, independent review and material limitations. An informed verdict
can judge purpose and result together. No earlier purpose decision is reused.

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

- `approve`: accepts purpose and result and completes the task. An applicable current-spec passed/human-reviewed attempt and clear completion
  gates are required. Completed requirements and proof are immutable.
- `rework`: purpose remains approved; repair this implementation, record a new
  result and obtain fresh review. The selected attempt goes to rework.
- `revise`: keep placement and pass the actual concrete
  `specification_question`. The sound reviewed result survives. Recording this
  verdict does not edit requirements or advance `spec_revision`; later real
  edits do.
- `drop`: keep queue placement, history, context and proof. Revival
  requires actual supporting authorization.
- `defer`: pause while retaining queue placement, context and proof for resumption.

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
attempt continuation state and the recorded purpose/result judgments. `get_attempt` retrieves
one known proof. Use paged `list_events(task_id=..., include_details=true)` for
historical signoff audit snapshots. Ordinary drop/defer needs no reviewed attempt;
both preserve queue placement, and leaving dropped status requires
`authorization` with the actual revival instruction. Neither dropped nor
deferred work satisfies a prerequisite. Shared-group completion still needs all
members human signed off across projects; there is no separate group sign-off.
Default `review` prerequisite links may already be satisfied by a current-spec
passed/human-reviewed attempt before sign-off, for every group member. Explicit
`signoff` links are rare exceptions for work very likely wasted without the
user's verdict. Rework or a spec change can block review links again without
erasing dependent results. Satisfaction proves no local code integration.

Use last returned entity revisions after user pauses and successful writes. No
routine read-before-write or confirming read is needed. Fetch and reconcile for
conflicts, uncertainty or missing information; inspect the board before retrying
an uncertain creation. Cards never carry body previews or replacement tokens.
For whole-field body/criteria replacements use the full-specification etag from
your complete read or create/update acknowledgement. Valid token-bearing updates
return a refreshed token; unchanged specifications retain it. Title/summary-only
edits need no full read. Summary edits preserve queue placement and proof.
Page deliberate attempt history/membership with `list_task_attempts`
and `list_group_members`. Never write a card or summary back as a specification.
