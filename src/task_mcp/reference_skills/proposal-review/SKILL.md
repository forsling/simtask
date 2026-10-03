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

Use `list_tasks` and `get_tasks(specification=true)` to present each pending task's goal, boundaries,
acceptance criteria, scope, and open unresolved items. Agent-discovered work
starts pending through `create_task(source="agent")`; capturing it never grants
authorization. After an informed decision, use
`accept_task(..., approval={"basis": "specific", "note": ...})` for the exact
current specification. A task explicitly requested by the user may be
created accepted with `approval={"basis": "specific", "note": ...}` identifying
the actual supporting instruction, and its origin/request separately recorded
when the request authorizes that implementation specification. "Add a task" can
supply that authorization without a separate acceptance keyword; queueing it
does not start implementation. Respect explicit requests to leave work unaccepted.
Exploratory ideas with material unresolved scope use the pending design-brief
path in `feature-capture`; ordinary concrete tasks do not require that workflow.

Use `resolve_unresolved` only after the matter is settled, preserving the
decision in the task description or acceptance criteria where material. Editing
those specification fields without approval invalidates prior acceptance. Save
an already-approved amendment with `update_task(..., approval=...)` so the exact
resulting scope is accepted atomically, reusing the informed decision. A session working on a task may
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

Origin (`source`) and `user_request` describe where a task came from, including
pending tasks; they never accept it. Use delegated approval only when the user's
actual instruction grants authority to select that scope within a stated goal,
and identify it in the note. Explicit pending/design-first requests take
precedence. Approval clears no other gates and queueing starts no implementation.
Use `withdraw_acceptance` with the last revision and a reason to correct mistaken
acceptance while retaining spec revision, decisions and proof. Reapproval of the
same spec may reuse current review; completed requirements and proof remain immutable.


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
