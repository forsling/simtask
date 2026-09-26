---
name: task-mcp-superdevloop
description: Implement scoped accepted tasks with a fresh implementer and reviewer.
---

# Superdevloop

Run `init` for the explicit target checkout and branch/name. Resume its `ready`
context; settle `new_branch`, `unregistered_checkout` or `mismatch` through the
init workflow before work. Retain that returned workstream ID, even when the
session is handling other repositories. Use
`get_next_task(workstream_id)` to select one full eligible task without claiming
it. An empty scope or only gated tasks is a useful stop; report its diagnostics.
Project ordering is advisory, but scope, acceptance, unresolved items, and
prerequisites are hard gates. Never invent an accepted task or move work into
another workstream. New work is a pending proposal in this scope or the inbox.
Group references are context, not executable tasks. A shared group expands only
to concrete members owned by this workstream's project; inspect whole-group
progress separately with `get_tasks(group_id)` or `workstream_status`.

Assign the selected task to a fresh implementer. The implementer changes code,
verifies it, and calls `record_result` with concrete evidence and its identity.
This is the first durable attempt record; no start or claim call is required.
Use a fresh reviewer who did not implement it. The reviewer returns findings to
the coordinator, which calls `record_review` with the reviewer's identity and
verdict. If rework is requested, repeat implementation and independent review;
after the workflow's bounded retry cap add a concrete unresolved item explaining
failed rounds and recovery options. `human_review` is reserved for an actual
user review or an explicit user direction to skip further review. Do not
self-issue it. Review passed makes the result ready for human sign-off; it does
not complete the task. Multiple workstreams may record alternatives, which the
user can compare at sign-off.
