# Resuming and integrating implementation evidence

Call `init` for the actual checkout and branch/name. An exact binding resumes
the same durable workstream ID, scope and history. To move that binding, choose
the existing workstream and use the revision-checked, confirmed
`init(action="rebind_workstream", ...)` flow. Rebinding preserves ledger
history; it does not move files or prove that a recorded result exists in the
new checkout.

Before relying on an attempt, read the current full task with `get_tasks(specification=true)` and
inspect the actual checkout, working diff and relevant commits. For Git work,
`git status --short`, `git rev-parse HEAD`, `git show <recorded-commit>` and
inspection of the affected files are ordinary verification aids. Commit
existence alone is insufficient: check that the current tree contains the
required behavior and run relevant verification. Use evidence proportional to
the task, especially after rebinding; no extra repository scan or checkpoint
is required at every step.

Selection and execution gates use only the target workstream's current-spec
attempts. A pending or passed attempt on another workstream does not prevent
a competing implementation locally. Explicit full task reads expose attempt
history with `workstream_id` and `spec_revision`; treat other-workstream and
older-spec proof as attributed history. Shared project order and human-signed-off
prerequisites do not prove that code was integrated into a branch.

For an **open task** whose implementation is deliberately merged or
cherry-picked into another workstream:

1. Inspect the origin attempt, its workstream and actual source commits. Read
   the task's current full specification and the target workstream binding/scope.
2. Deliberately integrate the required code, inspect the resulting target tree
   and target commits, and verify it against the current specification. Fix or
   complete any differences. A matching title, branch name, deployment report or
   prose claim is insufficient evidence.
3. Call ordinary `record_result` with the target `workstream_id`, the task's
   last-read `expected_revision` and full `specification_etag`, actual implementer,
   summary/context evidence, structured `artifacts` commit/path references and
   `verification` with actual checks/outcomes/limits.
   Cite the origin attempt/workstream, actual integrated source commits, target
   commits and target verification, including material limits. This creates a
   new target-local unreviewed attempt; it transfers no origin review.
4. Have a fresh independent reviewer inspect that target result and its actual
   checkout against the current specification. Record the review for the new
   attempt. Present it for explicit human sign-off only after review passes.

Example evidence, with real IDs/hashes/results substituted:

```text
Origin: attempt att_origin in workstream wst_origin, spec revision 4.
Integrated source commits: <source hashes>; cherry-picked into <target hashes>.
Target: workstream wst_target, checkout /work/target, HEAD <target hash>.
Checked current spec revision 4 and affected target files.
Target verification: <actual command and outcome>; limits: <material limits>.
```

Origin evidence and review remain intact. The new result follows the normal
scope, concurrency and full-spec binding checks; factual recording grants no
approval, resumption or completion and preserves every execution gate. Task MCP
stores assertions and does not inspect Git or authenticate
reviewer independence, so callers must establish applicability and record it
truthfully. No adoption tool, shared working state or automatic review
inheritance is involved.

A **completed task** keeps its selected human-approved attempt and immutable
proof. A later integration requirement is a new task with its own specification,
authorization, result and review. Historical example reports do not authorize
repairing another project's ledger. Compact default task reads remain deferred.

When an interrupted session left durable implementation unrecorded, inspect the
current full spec and actual checkout/commits before one factual `record_result`.
It may coexist with pending acceptance, unresolved/prerequisite gates or a
deferred/dropped disposition; those facts never authorize fresh implementation.
Its concise ACK exposes unchanged approval/disposition/blockers and attempt/task
revisions; retrieve complete proof deliberately.

After restart, `get_next_action(workstream_id)` may return `review` with the full
current task and exactly one complete current-spec local attempt. Send it directly
to a fresh independent reviewer after checking actual artifact applicability.
`implement` goes to an implementer, including rework findings when applicable.
Both use shared project order and unchanged autonomous gates. Passed/human_review
waits for human sign-off; no eligible action returns bounded waiting counts.
Selection creates no claim, lease, reorder or partial-progress write. Normal Git
continuation needs no extra scan/checkpoint after every step.
