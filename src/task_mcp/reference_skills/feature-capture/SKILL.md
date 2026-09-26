---
name: task-mcp-feature-capture
description: Capture a future feature with preliminary research and open design questions. Use for "add a design task for X" or an idea to design later; use feature-design for the interactive design discussion.
---

# Feature capture

Turn the user's idea into a useful brief for a later design session. Capturing
or discussing an idea authorizes this preparation, not an invented implementation.
Use the context and decisions already supplied; do not demand answers to every
question now or expand a clear implementation request into a design exercise.

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

## Save without making implementation ready

Create a normal task with `source="agent"`, even for a user-requested feature,
so the brief starts pending. Preserve the user's actual request in its body.
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

Do not call `accept_task` merely because the user asked to capture the feature.
Use the latest returned revision for each mutation. On a conflict, re-read and
reconcile; after an uncertain create, inspect the board before retrying. If
adding the gate fails, report the incomplete capture and leave the task pending.

Return the saved task ID/title, a short account of the preliminary findings and
the main open decisions. Explain that "let's design X" or "review design tasks"
resumes it through `feature-design`. Do not start that discussion or implement
unless the user also requested that next phase.
