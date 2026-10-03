---
name: task-mcp-init
description: Discover or resume an explicit Task MCP checkout and workstream.
---

# Init

At the start of every task-using session, call `init` with the absolute path of
the repository actually being worked on and its branch. Session cwd is only a
possible path default; a session in a parent directory may name any child repo.
For detached HEAD or non-Git work, supply an explicit `workstream_name`. Retain
the returned project and workstream IDs for subsequent task calls. A session may
retain several returned contexts at once; do not treat `list_projects` as a
current-project selector.

An exact binding returns `ready`, its scope revision and compact scoped queue.
Resume it directly, with no confirmation or separate preflight. `new_branch`
lists registered workstreams in that project. Choose a new scope or an explicit
rebind; neither branch names nor Git history imply scope. `unregistered_checkout`
lists candidates and offers `create_project`, `attach_workstream`, or
`rebind_workstream`. A mismatch requires resolving the named conflicting binding.
Show the target path, branch/name, chosen project/workstream and scope to the
user before calling `init` again with the chosen `action` and `confirmed=true`.
Rebind also requires the selected `workstream_id` and its last read
`expected_revision`. Repeat calls for an already exact binding return `ready`
without another mutation.

Continue on that durable workstream ID; rebind preserves its scope and recorded
history, not the files in the checkout. Before relying on an existing result,
read the current full specification and inspect the actual checkout, working
diff and relevant commits, especially after the binding moves. Verify the
required behavior in the current tree. Registered bindings, branch names and
old prose do not establish applicability. This is ordinary task verification,
not an extra repository scan or progress checkpoint at every step.

`none` begins with an empty queue. A workstream ID/name as the first expression
term snapshots its actual queue. With `set_scope`, pass the destination's last
returned `expected_revision`. `+task-id` and `-task-id` adjust that snapshot.
Explicit `+group-id` queues its current local members; later membership preserves
placement. Exclusions apply to the current expression. Selected tasks move from
their previous queues, so each task keeps one owner. Only members owned by this workstream's project
enter its executable queue; `groups` and `referenced_groups` show global
progress separately. Use `list_workstreams` for global or project
filtered candidates and `workstream_status` to inspect a scoped queue without
binding the current session to it. Those calls report registered bindings and
task state, never whether an agent is running. The older setup and `preflight`
primitives remain available to existing clients.

An ordinary "add a task" request does not automatically require feature design.
Queue concrete user-authorized scope on the intended branch without asking again;
preserve explicit inbox/design-first requests and leave agent-invented scope in the inbox. Queueing
work does not start implementation.

For "add a design task" or an exploratory feature idea to save for later, fetch
`get_default_skills(name="feature-capture")` and follow it. For "let's
design X", "review design tasks" or `designrev`, use `feature-design`.
These are ordinary tasks with unresolved design gates; no client installation
or new task type is needed. Use `proposal-review` for ordinary proposals and
non-design unresolved items, and `signoff` for judging delivered work.


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
