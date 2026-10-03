---
name: task-mcp-proposal-review
description: Review ordinary pending proposals and unresolved items with the user; route feature design to feature-design.
---

# Proposal and unresolved-item review

For a feature awaiting design discussion, load `feature-design` from
`get_default_skills(name="feature-design")` and follow it for research and decisions.
An explicit design brief or `Feature design required (feature-design):` gate
identifies that work; a generic blocker alone does not. Capturing an exploratory
feature (including "add a design task") uses `feature-capture` first.

Use `list_tasks` and full `get_tasks` to present inbox proposals and unresolved
items with their goals, boundaries, acceptance criteria and questions.
Agent-suggested work starts in the inbox. Queue concrete agreed work on the
intended branch using `queue_task`; a concrete user request can authorize creation
with `workstream_id` without another confirmation. Queueing starts no implementation.
Explicit inbox/design-first requests take precedence. Resolve only settled items,
folding material decisions into the spec. Edits retain queue placement; real
requirement edits advance spec revision and leave older proof historical.
A session working on a task may
add an unresolved item or prerequisite directly. An observer proposes a gate
with `handling="observer"`; it does not block until accepted. Resolve ambiguous
scope references with stable IDs. The active coordinator should use
`accept_gate_proposal` only for a valid gate, or `dismiss_gate_proposal` with a
clear decision note for an unwanted, stale, or invalid proposal. Dismissal is
audited and does not apply the gate; it still works if a proposed prerequisite
was later dropped. Resolve unresolved items and gate proposals
before converting a task into a group. `create_group` makes an empty group in
the current workstream scope, while `list_groups` discovers the same group
through its member projects. Pass the current group revision when creating or
attaching a member, including a member in another project. Groups hold context
and whole-group completion only; put execution blockers on concrete members.
Preserve deferred context and audit history.

Use `add_prerequisite` to add a link. For a mistaken or obsolete active link, use
`remove_prerequisite(task_id, expected_revision, blocked_by_id, note)` with the
dependent task's last revision and the actual decision note. It removes either
milestone link and immediately recalculates the gate, preserving specification,
queue placement and proof. A deletion advances the task revision once; an absent
link returns `changed=false` without advancing it. Stale revisions still fail,
completed tasks stay immutable, and actor/note are audited even for a no-op.
Use proposal dismissal for a pending observer gate; do not remove a real blocker
merely to make the queue eligible.

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

Origin and user_request are descriptive. Respect actual delegated authority and
its limits; never infer it from an idea or general encouragement. Correct mistaken
placement with `unqueue_task`, preserving requirements/revisions and proof. Queueing
clears no other gates. Completed requirements and proof remain immutable.

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
