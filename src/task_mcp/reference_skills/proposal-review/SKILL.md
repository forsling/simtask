---
name: task-mcp-proposal-review
description: Review pending proposals and unresolved items with the user.
---

# Proposal and unresolved-item review

Use `list_tasks` and `get_tasks` to present each pending task's goal, boundaries,
acceptance criteria, scope, and open unresolved items. Agent-discovered work
starts pending through `create_task(source="agent")`; capturing it never grants
authorization. The user may accept the current specification with `accept_task`
after an informed decision. A task explicitly requested by the user may be
created accepted with `source="user"` and the request recorded in `user_request`.

Use `resolve_unresolved` only after the matter is settled, preserving the
decision in the task description or acceptance criteria where material. Editing
those specification fields invalidates prior acceptance; ask for fresh task
acceptance before autonomous implementation. A session working on a task may
add an unresolved item or prerequisite directly. An observer proposes a gate
with `handling="observer"`; it does not block until accepted. Resolve ambiguous
scope references with stable IDs. Resolve unresolved items and gate proposals
before converting a task into a group. Groups hold context and completion only;
put blockers on their concrete members. Preserve deferred context and audit
history.
