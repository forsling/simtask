---
name: task-mcp-superdevloop
description: Implement workstream tasks with a fresh implementer and independent reviewer.
---

# Superdevloop

Follow explicit user instructions over this guidance. Run `init` for the explicit
checkout and branch/name; retain its IDs. Select one `implement`/`review` with
`get_next_action(workstream_id)` in this workstream's order. It returns the full
current spec/token and `latest_rejection`; review/rework includes exactly one
complete local attempt with proof/concerns/provenance. No second history read
is needed. At `action:null`, finish with bounded waiting counts, including sign-off.

Pending local review precedes another implementation of that task; passed/
human-reviewed results wait for the user. Membership, disposition, unresolved
items and prerequisites gate actions. Never clear real gates or change scope
to drain the list. Tasks can be in A and B; unfinished attempts/reviews remain
local to their workstream. Groups are context, not executable tasks.

User-requested additions belong in the current workstream without reconfirming
settled scope; confirmed agent ideas may stay in the inbox. Respect explicit
placement. **Add to workstream** uses `add_to_workstream(task_id, workstream_id,
expected_revision)` and retains all other memberships. **Remove from workstream**
uses `remove_from_workstream` with the same arguments and affects only the named
workstream. Inbox means zero effective memberships; groups/exclusions stay live.
Membership starts no implementation. Fetch `feature-capture`/`feature-design`
through `get_default_skills` for exploratory work/design. Keep scope/questions
in bodies and durable evidence in attempts.

## Implement the specification

Dispatch to a **fresh implementer** with complete spec, rework proof/findings
and latest rejection reasons. Inspect actual checkout/diff and relevant commits,
reusing applicable work after interruption/rebind. Build what the task says.
If its design appears substantially wrong, record a value/design concern and
carry on; do not improvise another design. If unexpectedly blocked, add a
concrete prerequisite or unresolved question with the factual handoff and move on.

For finished durable work call `record_result` with the last task revision,
`specification_etag`, actual implementer/summary/context evidence,
`artifacts=[{kind: "commit"|"artifact", reference: ...}]` and `verification`
describing actual checks/outcomes/limits. Optional
`concerns=[{kind: "value"|"design", text: ...}]` records doubts requiring a spec
change. The ACK returns task/attempt revisions and gates, omitting proof/concern
prose. Select again and dispatch review.

Recover durable unrecorded work only after checking current full spec and actual
artifacts. Proof can be recorded outside workstreams or while blocked/deferred/
dropped, but grants no authority, changes no membership and clears no gate.
Recovery does not authorize starting gated work.

## Review Build independently

Dispatch to a **fresh independent reviewer** who did not implement the attempt.
Supply complete task, exact proof/concerns, latest rejection and checkout/artifact
references. Check applicability before trusting proof. Treat the agreed design
as substantially correct and judge Build within it.

Could an implementer fix the problem unattended without changing goal, scope,
decided design or acceptance criteria? Then it is `rework`; otherwise it is a
value/design concern. Choices left open by the spec belong to the implementer,
so a poor choice is rework. When faithful implementation causes a real problem,
pass sound Build with a serious concern. Never fail review over Value/Design
doubts. "It would be nice to add X" is a new idea, not a concern.

The coordinator records actual independent reviewer, `pass`/`rework`, findings
in `note` and optional structured concerns with `record_review`, using the latest
attempt revision. Concerns retain attribution and implementer contributions;
they change no gate/verdict. Review pass permits sign-off when gates allow,
never task completion. Only the user approves through `signoff`. `human_review`
requires actual user review or explicit direction to skip further review;
never self-issue it. Rework repeats eligible implementation/fresh review using
latest reasons. If normal recovery is exhausted, save a concrete unresolved
question and move on.

## Dependencies, order and applicability

Use `add_prerequisite` for dependencies. Default `milestone="review"` clears on
done or current-spec passed/human-reviewed proof across workstreams; every member
of a nonempty group must satisfy it. Use `signoff` only when proceeding before
the verdict would very likely waste work. Dropped/deferred blockers stay
unsatisfied; rework/spec changes can block links again while dependent results
survive. Satisfaction proves reviewed work exists, not branch integration.
Remove only mistaken/obsolete links with `remove_prerequisite(task_id,
expected_revision, blocked_by_id, note)` and the actual reason.

Use local order for scheduling intent. **Reorder tasks** is
`reorder_tasks(workstream_id, task_ids, expected_order_revision)`: one exact
prefix; unlisted members keep relative order. Reuse returned
`workstream_order_revision` without routine preflight/reordering. Other lists
stay intact; new inclusions append deterministically. Project order is a baseline.

Other-workstream/older-spec proof is history, not a local execution gate.
Deliberate merge/cherry-pick of open work needs origin proof/source commits,
target full spec and actual tree checks. Record a target-local result citing
origin attempt/workstream, source/target commits and checks/limits, then obtain
fresh independent review. Never inherit origin review or infer integration
from names/prose. Completed proof is immutable; later integration needs a new task.

Reuse last revisions after writes/pauses; reconcile conflicts and inspect the
board before retrying uncertain creation. No confirming read is needed. Cards
are not specs. Whole body/criteria edits need the full-spec etag; valid updates
return its successor. Fetch missing requirements/chosen proof with
`get_tasks(specification=true, attempt_ids=[...])` or `get_attempt`; page
`list_task_attempts`/`list_group_members` deliberately.
