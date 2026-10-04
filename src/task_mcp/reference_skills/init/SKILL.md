---
name: task-mcp-init
description: Discover or resume an explicit Task MCP checkout and workstream.
---

# Init

Follow explicit user instructions over this guidance. Call `init` with the
absolute target checkout and branch, or `workstream_name` for detached/non-Git
work. Cwd suggests a path but does not select it. Keep returned IDs for each
context. `list_projects` does not select a current project.

An exact binding returns `ready` and a compact task list. Resume without
confirmation. Passing a known `workstream_id` checks its checkout/branch match.
`new_branch` lists workstreams; `unregistered_checkout` offers create/attach/
rebind; `mismatch` identifies the conflicting binding and lists `choices` that
work when followed. Choose setup and show path, branch/name, project/workstream
and scope, then call `init(action=..., confirmed=true)`: `create_project`,
`new_workstream` (attached checkout), `attach_workstream` (another checkout of
`project`) or `rebind_workstream`.
Rebind needs `workstream_id` and its last `expected_revision`; exact retries
return ready. `runtime` reports the server identity. Names/history do not imply scope.
Rebind retains scope/history, not files. Check the full current spec, actual
checkout/diff and relevant commits before relying on proof, especially after rebind.

Archived workstreams are stale bindings: `list_workstreams`, init candidates,
`workstream_status` listings and `get_next_action` leave them out unless
`include_archived=true`. `archived` means this checkout's binding is archived:
do not resume it unless the user directs; then follow its `choices`. Archive or
unarchive only when the user directs, with `archive_workstream(workstream_id,
expected_revision, reason, archived)`; it changes no tasks, memberships or proof.

## Notes

`ready` includes any nonempty project note (personal rules for this repository)
and workstream note (this branch's live state and rules). Read them before
working. Update the workstream note with `set_note(kind, target_id,
expected_revision, text)` when the live state it describes changes (for example
on deploy or rollback) and when the user directs; use the note's revision, or 0
when init shows none. Notes hold at most 2,000 characters: replace outdated
content rather than appending.

## Membership and order

Workstreams have independent ordered lists of shared tasks. A task can be in
A and B; unfinished attempts/reviews remain workstream-local. Inbox means zero
effective memberships. **Add to workstream** uses `add_to_workstream(task_id,
workstream_id, expected_revision)` and retains all other memberships. **Remove
from workstream** uses `remove_from_workstream` with the same arguments and
affects only the named workstream. Edits retain memberships; inclusion starts
no implementation.

User-requested work belongs in the current workstream, including actively gated
design briefs; confirmed agent ideas may stay in the inbox. Respect explicit
placement and reuse concrete authorization. Create settled work with
`create_task(workstream_id=...)` or add an existing task.

A new workstream's `scope_expression` retains dynamic groups/exclusions. `none`
starts empty; a workstream base copies scope, not order, and needs the source's
`expected_revision`. `+task-id`/`-task-id` adjust membership; `+group-id`
includes present/future local members unless excluded. Later, pass a group ID to
`add_to_workstream`/`remove_from_workstream` to include or remove the group; a
member ID excludes just that member. Other workstreams remain intact. `list_workstreams` discovers bindings; `workstream_status` reads counts/
scope without rebinding. State tracks no agent liveness.

Use prerequisites for dependencies and local order for scheduling intent.
**Reorder tasks** is `reorder_tasks(workstream_id, task_ids,
expected_order_revision)`: one exact prefix; unlisted members keep relative order.
Reuse `workstream_order_revision` from ordinary reads/explicit-target write ACKs
without routine re-reads or reshuffling. New inclusions append deterministically;
project order is only a baseline.

## Route and continue

Fetch `get_default_skills(name=...)`: `feature-capture` for "add a design task";
`feature-design` for "let's design X"/"review design tasks"/`designrev`;
`proposal-review` for ordinary proposals/questions; `superdevloop` for autonomous
implementation/independent review; `signoff` for reviewed-result walkthroughs
and user verdicts. "Sign off X" requests a walkthrough; "approve X" supplies
a verdict without requiring another walkthrough.

Reuse last revisions after writes/pauses; reconcile conflicts and inspect
the board before retrying uncertain creation. No confirming read is needed.
Cards are not specs. Whole body/criteria replacements need the full-spec etag;
valid updates return its successor. Fetch relevant specs/chosen proof together
with `get_tasks(specification=true, attempt_ids=[...])`, or one `get_attempt`;
page `list_task_attempts` or `list_tasks(group_id=...)` deliberately.
