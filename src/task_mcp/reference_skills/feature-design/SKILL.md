---
name: task-mcp-feature-design
description: Research and interactively design captured features with alternatives, tradeoffs and a recommendation. Use for "let's design X", "review design tasks", or the former designrev workflow; not implementation review or sign-off.
---

# Feature design

Turn a captured feature into an informed, agreed specification. The user may
approve choices during the conversation; retain that authorization instead of
asking them to repeat it. Choosing an approach does not by itself authorize
unmentioned scope or implementation. A concrete "add a task" request does not
require this workflow when its scope is already settled; preserve authorization
for that exact specification without a separate acceptance keyword.

## Find and resume the design

Run `init` for the explicit checkout and branch/name. Find a named task with
`list_tasks` and fetch it by ID with `get_tasks(specification=true)`. For "review design tasks",
default to the project queue, including inbox ideas saved by `feature-capture`;
respect an explicitly narrower workstream scope. Page through `list_tasks`
and fetch relevant bodies in batches of at most 20. Include both
`pending_acceptance` and `unresolved_items` candidates: a pending task's design
gate is hidden by its compact pending-acceptance view. Preserve
deferred tasks unless the user asks to revisit them; do not revive dropped or
completed work implicitly.

Select actual design work by the `Feature design required (feature-design):`
unresolved item or an equivalent explicit request/brief in an older task.
Generic unresolved blockers, credentials and ordinary pending proposals are
not automatically feature design. Present a compact candidate list when needed,
then work through one feature at a time. If no task exists, load `feature-capture`
from `get_default_skills(name="feature-capture")` and capture it first. Ensure an active design gate is
present before revising a mutable task, using the last read revision.

Read the brief, gates, prerequisites, earlier decisions and any implementation
history. Refresh the relevant code and documentation; the original capture may
be stale. Explain current behavior and how the desired outcome would fit.
Identify the decisions that materially affect scope, user experience or cost.
Present plausible approaches with concrete tradeoffs, and recommend a path with
reasons. Do not manufacture alternatives for a settled or obvious detail.

Attribute implementation history to its workstream/specification and inspect
relevant actual checkout files and commits before relying on it locally,
especially after rebinding the durable workstream. Branch names, matching titles,
deployment reports and prose do not prove integration. Deliberate integration
of an open task needs a new target-local result with origin attempt/workstream,
actual source/target commits and target verification, then fresh independent
review; no origin review is inherited. Completed proof stays immutable, so a
later integration requirement is a new task. Capture these requirements when
they are part of the agreed scope; design alone records no implementation.

Prerequisites use canonical task/group IDs across any projects; shared membership
is not required. Links do not add remote work to local scope or share attempts,
reviews or code. Default `milestone="review"` clears on done or any current-spec
`passed`/`human_review` attempt; every member of a nonempty group must satisfy it.
Reserve explicit `milestone="signoff"` for rare cases where proceeding before
the user's verdict would very likely waste work. Dropped/deferred work still
blocks. Rework/spec changes can block review links again; dependent results stay.
Use compact prerequisite references to inspect identity/milestone/satisfaction,
and explicit full task reads for requirements or proof. Verify IDs and meaning before
replacing a known prose gate: add real links first, then resolve the old item.
Never infer dependencies by parsing prose or claim another branch is integrated.
Milestone satisfaction is canonical state, not proof of integration into the
dependent checkout; inspect actual code and commits before relying on it.

Add links with `add_prerequisite`. Remove a mistaken or obsolete link with
`remove_prerequisite(task_id, expected_revision, blocked_by_id, note)`, using the
dependent task's last revision and the actual decision note. Removal recalculates
the gate without changing specifications, acceptance, scope or proof. Only a real
deletion advances the task revision; an absent link returns `changed=false`.
Stale revisions and completed tasks still fail. Actor and note are audited;
removal records an actual scope/dependency decision, not a bypass of unsettled design.

Discuss one decision at a time, starting with the one that constrains the rest.
Answer questions and adapt the recommendation to the user's priorities. Resolve
ordinary reversible details through research and judgment. Record meaningful
decisions, reasons and remaining questions as the discussion progresses so a
new session can resume; label assumptions and recommendations as such. Fold
each decision into the relevant part of the brief and replace superseded text
instead of appending dated discussion notes; audit events keep prior revisions.

## Prepare the agreed work

Save the resulting goal, boundaries, chosen behavior, relevant implementation
direction, acceptance criteria and rationale with `update_task`. Editing these
fields without approval invalidates prior acceptance. When the user already
approved the resulting scope, include the common approval payload in this save
to accept it atomically. Follow feature-capture's title and body rules for the
final specification and any members; retitle while already revising. Preserve unresolved questions and unrelated
gates; call `resolve_unresolved` only for settled items, with an accurate decision
note. Keep the design gate until the requested discussion and material decisions
are complete. If the session ends early, leave the gate and save where to resume.

For one implementation task, save the final specification before clearing the
design gate, including actual approval when it covers the resulting scope.
Otherwise use `accept_task(task_id, expected_revision, approval={"basis":
"specific", "note": ...})` only when the user's informed authorization covers
that exact current specification. A decision already given for the presented
scope is sufficient; do not impose a second ceremonial approval. Otherwise
present the concrete scope for acceptance, or leave it pending if the user only
asked to design. Acceptance alone does not clear other unresolved items,
prerequisites, disposition or scope gates. Use the common approval payload for
creation, amendment and standalone acceptance: `specific` records a real exact-scope
request/decision; `delegated` records actual authority to select work within a
stated goal and its limits. Both need a nonempty note identifying the supporting
instruction. Origin (`source`) and `user_request` remain descriptive, including
on pending briefs, and never imply acceptance. Explicit pending/design-first
instructions take precedence over inferred authorization.

Use `withdraw_acceptance(task_id, expected_revision, note)` to correct mistaken
acceptance without editing the specification. Proof and decisions survive;
reapproval of the unchanged spec can reuse applicable review. Completed work
remains immutable. Compact create/update/accept/withdraw acknowledgements provide IDs,
revisions, acceptance and gates; request `get_tasks(specification=true)` for missing requirements and exact proof.

Split a larger feature only when concrete, independently deliverable members
improve execution. Present their boundaries, acceptance criteria and dependencies
as part of the design; do not infer approval of them from a broad feature wish.
`decompose_task` requires an open concrete task with no parent group, attempts,
unresolved items or gate proposals. Do not clear unsettled questions merely to
satisfy these constraints; keep the feature intact until they are settled, or
use separately captured pending proposals for optional future work.

For decomposition, first save the agreed split and rationale in the parent's
specification and verify it is pending acceptance. An unchanged patch does not
invalidate acceptance: do not remove the last gate from an accepted parent
while preparing a split. With the parent pending, resolve only settled gates
and adjudicate proposals on their merits, then call `decompose_task` with the
last returned revision. Do not accept the parent before conversion. The atomic
conversion preserves context and creates pending children; existing prerequisites
move to them. Read the children, add any agreed child dependencies/gates before
acceptance, and accept only the exact child specifications the user authorized.
Groups hold context, not execution gates or implementation acceptance. If the
parent is deferred, resume it only when requested, after saving the pending
specification; if attempts or a parent group prevent conversion, retain the
existing task and discuss a suitable follow-up structure instead of forcing it.

Acceptance does not place an inbox task into an executable workstream. When the
agreed plan includes implementation in the current or a named workstream, use
its last returned revision and `set_scope` to add the task/group,
preserving existing scope (for example, `<workstream-id> +<task-or-group-id>`).
Respect explicit exclusions and narrower placement decisions; otherwise retain
the inbox placement and report that workstream placement remains outstanding.
Do not ask again when the intended workstream is already clear from the user's
request. Use the successful acknowledgement to continue; inspect the queue only when placement is uncertain.

Re-read and reconcile revision conflicts. Inspect the board after uncertain
mutations before retrying creation or decomposition. Report the saved task/group
IDs, decisions, remaining questions and whether implementation is eligible.
Use the normal implementation workflow only when requested and eligible. Design
agreement is neither an implementation result nor independent review or human
sign-off; do not create delivery records for this conversation.


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
