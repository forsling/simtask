---
name: task-mcp-feature-capture
description: Save a researched feature brief with open design questions. Use for "add a design task" or an idea needing design; use feature-design for the discussion.
---

# Feature capture

Follow explicit user instructions over this guidance. Capture an exploratory
idea for a later design session; an ordinary concrete "add a task" request
does not require feature design. User-requested tasks are queued on the current
branch, including design briefs with active gates. Agent-invented ideas go to
the inbox only after the user confirms saving them. Explicit inbox or other
placement instructions take precedence. Queueing starts no implementation.

## Ground the scope

Run `init` for the explicit checkout and branch/name. Inspect `list_tasks` and
fetch only related full specifications with `get_tasks(specification=true)` to
avoid duplicates and preserve decisions. Read enough code/documentation to
identify current behavior, integration points and constraints; research only
what shapes the brief and state what remains unverified.

For settled concrete work, use `create_task(source="user", user_request=...,
workstream_id=...)`, or `queue_task` for an existing task. Record the actual
request and settled scope without another confirmation. Source/user_request
are descriptive. Do not add a design gate to settled work. When separate calls
must add blockers, create in the inbox, add gates, then queue.

For exploratory work, record the desired outcome, motivation, relevant current
behavior/code references, tentative scope/exclusions, constraints, assumptions,
possible approaches and material open questions. Distinguish user decisions
from suggestions and mark provisional acceptance criteria. Questions should
affect value, user experience, scope, architecture, authority or costly choices;
leave routine reversible details to the implementer. Ask now only if ambiguity
prevents useful capture; otherwise save the question for design.

Attribute existing delivery to its workstream/specification and inspect actual
files/commits before claiming it exists locally. Milestone satisfaction, branch
names and prose do not prove integration. Open work deliberately integrated
elsewhere needs a target-local result citing origin attempt/workstream and
source/target commits, target verification and fresh review. Completed proof
is immutable; later integration requires a new task.

Name the observable outcome, normally within about 70 characters. An optional
one-line summary describes intent/constraints, at most 240 Unicode characters;
it is non-normative and freshness tracks spec edits. Keep questions and scope
in the body, acceptance criteria in their field, and progress/commits/evidence
in attempts, rather than summaries or specifications.

## Save a gated brief

Create an ordinary inbox task with honest `source` (user or agent) and the
actual `user_request`. Using the creation revision, add an active unresolved
item before queueing user-requested work on the current branch:

> Feature design required (feature-design): decide whether the work is worth doing, then agree the approach, scope and acceptance criteria with the user.

Tailor it to the actual decisions. The prefix is a readable skill convention,
not a new task type or parsed server field. Keep material questions in the
body, adding separate unresolved items only for independently resolved gates.
For an existing mutable task, add its active design gate before revising the
brief. Observer proposals are nonblocking; do not use `handling="observer"`
while actively capturing requested work. Preserve deferred status/other gates;
completed work needs a new task. If gate creation fails, leave the task in the
inbox and report the incomplete capture.

Express real ordering with `add_prerequisite`, not queue position. Default
`milestone="review"` clears on done or current-spec passed/human-reviewed proof
across workstreams, for every member of a nonempty group. Use `signoff` only
when proceeding before the verdict would very likely waste work. Dropped/
deferred blockers remain unsatisfied; rework/spec changes can block links again.
A satisfied blocker proves reviewed work exists, not integration into the
dependent branch. Verify IDs/meaning before linking a known prose gate, then
resolve that old item. Remove a mistaken/obsolete link with
`remove_prerequisite(..., note=...)`, preserving the actual decision reason.

Return the saved ID/title, preliminary findings and main open decisions. Explain
that "let's design X" or "review design tasks" resumes through `feature-design`.
Capture alone does not start discussion or implementation. Leave gates until
the user's decisions cover the resulting scope.

Continue from last returned revisions after writes/pauses; reconcile conflicts
without routine confirming reads. Inspect the board before retrying uncertain
creation. Use `unqueue_task` to correct placement without changing requirements
or proof. Cards/summaries cannot replace a spec. Whole-field body/criteria edits
need its full-spec etag; valid updates return a refreshed token. Fetch only
needed proof through chosen `get_tasks` attempt IDs or `get_attempt`, and page
history/membership deliberately with `list_task_attempts`/`list_group_members`.
