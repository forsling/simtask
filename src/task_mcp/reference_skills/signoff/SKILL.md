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

Present the task's purpose,
`acceptance_basis`, supporting `approval_decision` (actual note and decision
reference), and whether it is currently accepted separately from the result,
verification, independent review and material limitations. Specific approval
already answers purpose for this scope and can be reused. Delegated or unknown
approval needs an actual purpose judgment now; never infer it from origin or
old prose. One informed approval may cover both purpose and result. Do not ask
for two ceremonial confirmations.

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

- `approve`: accepts purpose and result and completes the task. Current
  acceptance, applicable passed/human-reviewed attempt and clear completion
  gates are required. Completed requirements and proof are immutable.
- `rework`: purpose remains approved; repair this implementation, record a new
  result and obtain fresh review. The selected attempt goes to rework.
- `revise`: withdraw approval and pass the actual concrete
  `specification_question`. The sound reviewed result survives. Recording this
  verdict does not edit requirements or advance `spec_revision`; later real
  edits do.
- `drop`: deactivate approval and keep history, context and proof. Revival
  requires an actual supporting authorization and current specification approval.
- `defer`: pause while retaining approval, context and proof for resumption.

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
attempt continuation state. Use deliberate `get_tasks` reads for proof,
`approval_decision` and structured `signoff_decisions`, or detailed `list_events`
for original audit snapshots. Ordinary drop/defer needs no reviewed attempt;
drop deactivates approval, defer retains it, and leaving dropped status requires
`authorization` with the actual revival instruction. Neither dropped nor
deferred work satisfies a prerequisite. Shared-group completion still needs all
members human signed off across projects; there is no separate group sign-off.

Use last returned entity revisions after user pauses and successful writes. No
routine read-before-write or confirming read is needed. Fetch and reconcile for
conflicts, uncertainty or missing information; inspect the board before retrying
an uncertain creation. Cards never carry body previews or replacement tokens.
For whole-field body/criteria replacements use the full-specification etag from
your complete read or create/update acknowledgement. Valid token-bearing updates
return a refreshed token; unchanged specifications retain it. Title/summary-only
edits need no full read. Summary edits preserve spec acceptance and proof.
Retrieve exactly needed proof with `get_tasks(specification=true, attempt_ids=[...])`
or `get_attempt`; page deliberate history/membership with `list_task_attempts`
and `list_group_members`. Never write a card or summary back as a specification.
