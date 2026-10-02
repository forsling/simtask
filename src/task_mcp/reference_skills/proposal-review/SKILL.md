---
name: task-mcp-proposal-review
description: Review ordinary pending proposals and unresolved items with the user; route feature design to feature-design.
---

# Proposal and unresolved-item review

For a feature awaiting design discussion, load `feature-design` from
`get_default_skills` and follow it for research, alternatives and recommendations.
An explicit design brief or `Feature design required (feature-design):` gate
identifies that work; a generic blocker alone does not. Capturing an exploratory
feature (including "add a design task") uses `feature-capture` first.

Use `list_tasks` and `get_tasks` to present each pending task's goal, boundaries,
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
those specification fields invalidates prior acceptance; record acceptance of
the exact current scope before autonomous implementation, using an informed
decision already given if it covers that scope. A session working on a task may
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

Origin (`source`) and `user_request` describe where a task came from, including
pending tasks; they never accept it. Use delegated approval only when the user's
actual instruction grants authority to select that scope within a stated goal,
and identify it in the note. Explicit pending/design-first requests take
precedence. Approval clears no other gates and queueing starts no implementation.
Use `withdraw_acceptance` with the last revision and a reason to correct mistaken
acceptance while retaining spec revision, decisions and proof. Reapproval of the
same spec may reuse current review; completed tasks remain immutable.
