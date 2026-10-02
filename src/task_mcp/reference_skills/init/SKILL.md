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

`none` begins a new workstream with an empty scope. A workstream ID/name as the
first scope expression term snapshots its current scope; pass that source's
last read `expected_revision`. `+task-id` and
`-task-id` adjust it. Group inclusion remains live for future members, while
explicit exclusions persist. Only members owned by this workstream's project
enter its executable queue; `groups` and `referenced_groups` show global
progress separately. Use `list_workstreams` for global or project
filtered candidates and `workstream_status` to inspect a scoped queue without
binding the current session to it. Those calls report registered bindings and
task state, never whether an agent is running. The older setup and `preflight`
primitives remain available to existing clients.

An ordinary "add a task" request does not automatically require feature design.
Record concrete user-authorized scope accepted without asking again; preserve
explicit unaccepted requests and leave agent-invented scope pending. Queueing
work does not start implementation.

For "add a design task" or an exploratory feature idea to save for later, fetch
`get_default_skills` and read/follow its `feature-capture` entry. For "let's
design X", "review design tasks" or `designrev`, use `feature-design`.
These are ordinary tasks with unresolved design gates; no client installation
or new task type is needed. Use `proposal-review` for ordinary proposals and
non-design unresolved items, and `signoff` for judging delivered work.
