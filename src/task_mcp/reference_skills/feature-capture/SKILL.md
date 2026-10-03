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

After `init` and checking the board and relevant specifications, record concrete
agreed work with `create_task(source="user", user_request=..., workstream_id=...)`.
For an existing task use `queue_task(task_id, workstream_id, expected_revision)`.
The queue is the one action that says the user wants it built on that branch;
queueing starts no implementation and clears no other gates. Record the request
and settled decisions in the specification. Source/user_request are descriptive.
Respect actual delegated scope and earlier decisions without inventing authority.
No repeated confirmation is needed when the concrete request covers the scope.
Explicit inbox/design-first requests take precedence. To add blockers in separate
calls, create in the inbox, add active gates, then queue when placement is agreed.
Use ordinary creation for this path; do not add a design gate to settled work.

Agent-suggested additions and exploratory scope stay in the inbox. Use the brief
path below when material decisions remain open. Routine reversible choices do
not require a design gate.

## Ground the brief

Run the `init` workflow for the explicit target checkout and branch/name. Inspect
`list_tasks` in the relevant project/scope, then `get_tasks(specification=true)` for related items to
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

Boards show title and optional authored summary. Name the observable outcome
in about 70 characters with one main fact, not internal slugs or file names.
Normally author a one-line summary of intent or settled constraints, at most 240
Unicode characters. Exclude progress, queue position and priority; absent summaries
have no fallback. Summaries are non-normative; freshness only tracks later spec edits.
Keep the body to the specification and its open questions. Progress, commits
and evidence belong in attempts; acceptance criteria belong only in their own
field.

## Save without making implementation ready

For an exploratory design brief, create a normal task with honest `source`
(`"user"` for a user idea, `"agent"` for an agent suggestion) and preserve the
actual `user_request` independently. Create in the project inbox (omit
`workstream_id`), add the active design gate, then queue only if branch placement
was explicitly requested. This sequence prevents an ungated eligibility window.

Add an active unresolved item using the revision returned by creation:

> Feature design required (feature-design): compare the material approaches and agree the scope and acceptance criteria with the user before implementation.

Tailor the text to the feature's actual decisions. This readable prefix is a
reference-workflow convention, not a new task type or server-parsed field. Keep
the brief's design questions in the body, adding separate unresolved items when
they need independent resolution. For an existing mutable task, add the active
design gate before revising the brief. Observer proposals are nonblocking, so
do not use `handling="observer"` when actively capturing the requested feature.
Completed work needs a new task; preserve deferred disposition and other gates.

Leave the design gate until the user's decisions cover the resulting scope.
Use `unqueue_task(task_id, expected_revision)` to correct mistaken placement;
requirements, spec revision and proof survive. Completed requirements/proof are
immutable. Compact create/update/queue/unqueue acknowledgements supply IDs and
revisions. On conflict reconcile current details; inspect the board before
retrying uncertain creation. If adding a gate fails, leave the brief in the inbox
and report the incomplete capture.

Return the saved task ID/title, a short account of the preliminary findings and
the main open decisions. Explain that "let's design X" or "review design tasks"
resumes it through `feature-design`. Do not start that discussion or implement
unless the user also requested that next phase.


Use last returned entity revisions after user pauses and successful writes. No
routine read-before-write or confirming read is needed. Fetch and reconcile for
conflicts, uncertainty or missing information; inspect the board before retrying
an uncertain creation. Cards never carry body previews or replacement tokens.
For whole-field body/criteria replacements use the full-specification etag from
your complete read or create/update acknowledgement. Valid token-bearing updates
return a refreshed token; unchanged specifications retain it. Title/summary-only
edits need no full read. Summary edits preserve queue placement and proof.
Retrieve exactly needed proof with `get_tasks(specification=true, attempt_ids=[...])`
or `get_attempt`; page deliberate history/membership with `list_task_attempts`
and `list_group_members`. Never write a card or summary back as a specification.
