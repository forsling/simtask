---
name: task-mcp-feature-capture
description: Prepare an exploratory feature brief with research and open design questions. Use for "add a design task" or an idea needing design, not routine creation of a concrete task; use feature-design for the discussion.
---

# Feature capture

Turn an exploratory idea into a useful brief for a later design session. Choose
the workflow from the user's intent and the requested scope, not whether they
said "add", "capture" or "accept". An ordinary "add a task" request does not
automatically ask for feature design.

## Preserve concrete task authorization

After `init` and checking the board and relevant specifications, when the user
wants to queue concrete work with settled scope, record its faithful specification
with `create_task(source="user", user_request=..., approval={"basis": "specific",
"note": ...})`. Record the actual supporting instruction in the approval note;
only approval accepts the exact created specification. Source and user_request
are descriptive provenance and never grant approval. Explicit requests
to design first or leave work unaccepted take precedence.
For an existing task, use `accept_task(task_id, expected_revision,
approval={"basis": "specific", "note": ...})` when the request covers its exact
current specification. Use the same payload for standalone approval. `specific`
means a real request/decision covering that exact scope; `delegated` requires
actual authority to select work within the user's stated goal. Record that
instruction and respect its limits. Never invent delegation from an idea,
origin label or general encouragement. No separate acceptance keyword or
repeated approval is needed.
For example, "add a task to reject blank names" can authorize that behavior.
Queueing accepted work does not start implementation; retain the requested scope
and execution timing. If blockers must be added in separate calls, create pending,
add them, then record the already-given authorization with `accept_task`.
Use ordinary task creation for this path and stop here; do not add a design gate.

Keep agent-suggested additions, exploratory scope and explicit requests to leave
work unaccepted pending. Use the design-brief path below when material decisions
remain open or the user asks to design before implementation. Routine reversible
implementation choices do not require a design gate. Preserve earlier decisions
and authorization; do not infer approval of scope invented by the agent.

## Ground the brief

Run the `init` workflow for the explicit target checkout and branch/name. Inspect
`list_tasks` in the relevant project/scope, then `get_tasks` for related items to
avoid duplicates and preserve earlier decisions. Read enough relevant code and
project documentation to identify the current behavior, integration points and
constraints. Keep this research bounded to shaping the brief; no implementation
or exhaustive architecture study is needed. State what remains unverified.

Attribute implementation history to its workstream/specification. Before
claiming existing behavior locally, inspect relevant checkout files and actual
commits; branch names, deployment reports and prose do not prove integration.
Completed proof remains immutable: a later integration requirement is a new
task. An open task's deliberate cross-workstream integration will need a new
target-local result with origin attempt/workstream, source/target commits and
target verification, followed by fresh independent review.

Record the desired user outcome and motivation, relevant current behavior with
useful code references, tentative scope and exclusions, constraints, assumptions,
possible approaches, and material open questions. Distinguish user decisions
from suggestions. Draft observable acceptance criteria where supported, marking
provisional criteria clearly. Questions should affect user experience, scope,
architecture, authorization or costly choices; leave routine reversible details
to the eventual implementer. Ask now only when ambiguity prevents a useful
capture, and otherwise save the question for design.

Boards and compact results show only the title, so name the observable outcome
in about 70 characters with one main fact, not internal slugs or file names.
Keep the body to the specification and its open questions. Progress, commits
and evidence belong in attempts; acceptance criteria belong only in their own
field.

## Save without making implementation ready

For an exploratory design brief, create a normal task with honest `source`
(`"user"` for a user idea, `"agent"` for an agent suggestion) and preserve the
actual `user_request` independently. Omit `approval` so it starts pending.
Use the requested scope, or the project inbox when no workstream placement was
requested. Omit approval: supplying it before the separate gate call would
leave an implementation eligibility window.

Add an active unresolved item using the revision returned by creation:

> Feature design required (feature-design): compare the material approaches and agree the scope and acceptance criteria with the user before implementation.

Tailor the text to the feature's actual decisions. This readable prefix is a
reference-workflow convention, not a new task type or server-parsed field. Keep
the brief's design questions in the body, adding separate unresolved items when
they need independent resolution. For an existing mutable task, add the active
design gate before revising the brief. Observer proposals are nonblocking, so
do not use `handling="observer"` when actively capturing the requested feature.
Completed work needs a new task; preserve deferred disposition and other gates.

Leave an exploratory brief pending until the user's decisions cover its current
specification. Reuse authorization already given when it covers that scope;
a request to record an idea does not approve unresolved or agent-invented scope.
If acceptance was recorded by mistake, use `withdraw_acceptance` with the last
read revision and a reason. It retains specification text/revision, decisions,
results and reviews; no dummy specification edit is needed. Reapproval of the
unchanged specification may reuse applicable review. Completed work is immutable.
Creation/acceptance/withdrawal return compact acknowledgements; use their IDs
and revisions to continue and `get_tasks` when full details are needed.
Use the latest returned revision for each mutation. On a conflict, re-read and
reconcile; after an uncertain create, inspect the board before retrying. If
adding the gate fails, report the incomplete capture and leave the task pending.

Return the saved task ID/title, a short account of the preliminary findings and
the main open decisions. Explain that "let's design X" or "review design tasks"
resumes it through `feature-design`. Do not start that discussion or implement
unless the user also requested that next phase.

Specification amendments use `update_task(..., approval=...)` to save and accept
already-authorized resulting scope in one revision-checked transaction. Reuse
the actual supporting instruction; do not ask for the same approval again.
Omit approval for unsettled or explicitly pending amendments. Every real
title/body/criteria edit changes the spec; no editorial exemption is inferred.
Before replacing body or acceptance_criteria, read the full task once with
`get_tasks(ids=[...])` and pass its `specification_etag` plus the current task
revision. These fields are whole replacements; board rows carry no token.
Title-only edits need no full body read. `get_tasks` currently returns complete
specifications by default; no `specification=true` flag exists. On conflict,
re-read the full spec, reconcile, and use its current revision/token.
Unchanged patches are no-ops unless approval changes; approving an unchanged
pending spec leaves spec revision unchanged. Compact update acknowledgements
report `changed`, `spec_changed`, `approval_changed`, revisions and gates.
Approval grants no scope, prerequisite satisfaction, review, completion or
execution. Attempts and reviews remain proof only for their original spec.

Signoff judges task purpose and delivered quality separately. Present the
approval basis and actual supporting decision beside review evidence. One
informed user decision covers both when appropriate: `approve`, `rework`,
`revise`, `drop` or `defer`. Revise withdraws approval and adds a concrete
question without changing spec revision; drop withdraws approval; defer keeps
it. Direction changes default to human technical quality not judged and preserve
sound proof. Ordinary drop/defer requires no reviewed attempt. Leaving dropped
status requires actual `authorization`; reactivation also needs current spec
approval. Read the packaged `signoff` workflow before recording a verdict.

Shared project order is scheduling metadata, filtered by each workstream's explicit
scope and local eligibility. New tasks append at the end. Reorder only for an
actual scheduling decision: read `list_tasks`/`workstream_status` or `init` for
`project_order_revision`, then call `reorder_tasks(project, task_id, anchor_id,
position="before"|"after", expected_order_revision=..., instruction=...)` to move
one task immediately beside a concrete project anchor. Record the actual
supporting instruction/authority; the configured audit actor is attribution,
not authenticated identity. The compact ACK returns project/task/anchor IDs,
`project_order_revision` and `changed`. A stale order revision requires a fresh
board read and reconciliation; an already-satisfied move changes nothing.
Completed task positions may shift without changing specifications, acceptance,
results or reviews. Reads and selection never reorder work. Do not repeatedly
normalize queues, add automatic priority rules or routine reporting calls;
evaluate excessive reordering after rollout through existing audit events.
