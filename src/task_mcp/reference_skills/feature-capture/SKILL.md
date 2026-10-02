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
accepted with `create_task(source="user", user_request=...)`. Explicit requests
to design first or leave work unaccepted take precedence.
For an existing task, use `accept_task` when the request covers its exact current
specification. No separate acceptance keyword or repeated approval is needed.
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

For an exploratory design brief, create a normal task with `source="agent"`,
even when the idea came from the user, so it starts pending. Preserve the user's
actual request in its body.
Use the requested scope, or the project inbox when no workstream placement was
requested. Do not create it accepted with `source="user"`: creation and adding a
gate are separate calls, leaving an implementation eligibility window.

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
Use the latest returned revision for each mutation. On a conflict, re-read and
reconcile; after an uncertain create, inspect the board before retrying. If
adding the gate fails, report the incomplete capture and leave the task pending.

Return the saved task ID/title, a short account of the preliminary findings and
the main open decisions. Explain that "let's design X" or "review design tasks"
resumes it through `feature-design`. Do not start that discussion or implement
unless the user also requested that next phase.
