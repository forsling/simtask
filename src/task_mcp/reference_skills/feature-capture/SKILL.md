---
name: task-mcp-feature-capture
description: Save a researched feature brief with open design questions, or go through saved ideas. Use for "add a design task", an idea needing design or "go through my ideas"; use feature-design for the discussion.
---

# Feature capture

Follow explicit user instructions over this guidance. Capture exploratory ideas
for later design; concrete "add a task" requests need no feature design.
User-requested work belongs in the current workstream. Save agent-invented ideas
only after user confirmation; they may stay in the inbox. Respect explicit
placement. Membership starts no implementation.

## Ground the brief

Run `init` for the explicit checkout and branch/name. Inspect `list_tasks` and
related full specs with `get_tasks(specification=true)` to avoid duplicates and
preserve decisions. Read enough code/documentation to identify current behavior,
integration points and constraints; research what shapes the brief and state
what remains unverified.

For settled work, use `create_task(source="user", user_request=...,
workstream_id=...)`, or **Add to workstream** with `add_to_workstream(task_id,
workstream_id, expected_revision)`. Record the actual request/scope without
another confirmation or design gate. Adding B retains A. **Remove from
workstream** uses `remove_from_workstream` with the same arguments and affects
only that workstream. Inbox means zero effective memberships. Groups/exclusions
stay dynamic; omitting `workstream_id` does not ensure inbox placement when
joining an included group.

Record outcome, motivation, relevant current behavior/code, tentative scope/
exclusions, constraints, assumptions, approaches and material questions.
Distinguish user decisions from suggestions; mark provisional criteria.
Questions should affect value, experience, scope, architecture, authority or
costly choices; leave routine reversible details to the implementer. Ask now
only if ambiguity prevents useful capture.

Use an observable title and optional one-line intent/constraints summary (at
most 240 Unicode characters). Keep scope/questions in the body, criteria in
their field and evidence in attempts. Summaries are non-normative. Attribute
existing delivery to its workstream/spec and inspect actual files/commits before
relying on it locally. Deliberate integration needs target-local verification,
a result with origin/source/target references and fresh independent review;
completed proof is immutable, so later integration needs a new task.

## Save with active design gates

Create a new brief without `workstream_id` or `group_id`, with honest `source`
and the actual `user_request`. Using its returned revision, add an ordinary
active unresolved item before adding user-requested work to the current workstream:

> Feature design required (feature-design): decide whether the work is worth doing, then agree the approach, scope and acceptance criteria with the user.

Tailor it to actual decisions. The prefix is a readable convention, not a task
type/parsed field. Keep questions in the body; add separate unresolved items
for independently settled gates. Gate existing mutable work before revising
its brief; preserve other memberships, deferred status and unrelated gates.
Observer proposals are nonblocking: do not use `handling="observer"` for actively
requested capture. Completed work needs a new task. If gating fails, leave new
work outside workstreams and report incomplete capture. Gate before attaching
to a dynamically included group.

Use `add_prerequisite` for dependencies. Default `milestone="review"` clears on
done or current-spec passed/human-reviewed proof across workstreams; every member
of a nonempty group must satisfy it. Use `signoff` only when proceeding before
the verdict would very likely waste work. Dropped/deferred blockers stay
unsatisfied; rework/spec changes can block links again. A satisfied blocker proves
reviewed work exists, not integration into the dependent branch. Verify IDs/
meaning before replacing prose gates with links. Remove mistaken/obsolete links
with `remove_prerequisite(..., note=...)` and the actual reason.

Use local order for scheduling intent. **Reorder tasks** is
`reorder_tasks(workstream_id, task_ids, expected_order_revision)`: one exact
prefix; unlisted members keep relative order. Reuse returned
`workstream_order_revision` without routine reads/reshuffling. Other lists stay
intact; new inclusions append deterministically.

Return saved ID/title, findings and open decisions. "Let's design X"/"review
design tasks" resumes through `feature-design`. Capture starts no discussion/
implementation; keep gates until decisions cover the scope.

Reuse last revisions after writes/pauses; reconcile conflicts and inspect the
board before retrying uncertain creation. Cards are not specs. Whole body/
criteria edits need the full-spec etag; valid updates return its successor.
Fetch chosen proof with `get_tasks` attempt IDs or `get_attempt`; page
`list_task_attempts`/`list_group_members` deliberately.

## Go through my ideas

Browser **+ Idea** saves inbox tasks held by an item starting `Idea to
process:`. On request, find them with `list_tasks` and take them one at a
time with the user, never unattended. Rewrite each in place with
`update_task` into a proper brief (gated as above) or task, adding it to a
workstream only when the user wants it built; split it with `decompose_task`;
or drop it with `set_disposition` once the user agrees. Then resolve the idea
item with `resolve_unresolved` and the user's outcome.
