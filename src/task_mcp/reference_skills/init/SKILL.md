---
name: task-mcp-init
description: Discover or resume an explicit Task MCP checkout and workstream.
---

# Init

Follow explicit user instructions over this guidance. At the start of a
task-using session call `init` with the absolute target repository path and
branch, or `workstream_name` for detached/non-Git work. Session cwd may suggest
a path, including a child repo, but does not select it. Retain returned project/
workstream IDs; a session may hold several contexts. `list_projects` is not a
current-project selector.

An exact binding returns `ready` and a compact scoped queue. Resume without a
confirmation or separate preflight. `new_branch` lists workstreams;
`unregistered_checkout` offers create/attach/rebind; `mismatch` identifies a
conflicting binding. Choose setup, show its target path, branch/name, project/
workstream and scope, then call `init` with the chosen `action` and
`confirmed=true`. Rebind also needs `workstream_id` and its last
`expected_revision`. Exact retries return ready without another mutation.
Names and Git history never imply queue scope.

Rebind preserves the durable workstream's queue/history, not checkout files.
Before relying on proof inspect the current full specification, actual checkout/
diff and relevant commits, especially after a binding move. Verify behavior in
the tree; registered bindings or prose do not establish applicability. This is
ordinary verification, not an extra scan/checkpoint at every step.

`none` starts empty. A workstream ID/name as the first `set_scope` term snapshots
its queue; `+task-id`/`-task-id` adjust it. Explicit groups snapshot current local
members, preserving future members' placement. Selected tasks move ownership,
never duplicate it. Pass the destination's last workstream revision. Only its
project's concrete tasks execute; groups/referenced groups show global context.
Use `list_workstreams` for global/project discovery and `workstream_status` for
scoped counts/gates without binding this session. State does not track agent
liveness. Older setup/preflight primitives remain available.

User-requested tasks belong on the current branch, including gated design
briefs; confirmed agent-invented ideas belong in the inbox. Respect explicit
placement instructions and reuse concrete authorization without asking again.
Queueing starts no implementation. Express real ordering with blockers, not
queue position. Default review links clear on current-spec passed/human-reviewed
work; signoff links are only for work very likely wasted before the verdict.
Satisfaction proves no integration into the dependent branch.

Fetch `get_default_skills(name=...)` for the relevant workflow:
`feature-capture` for "add a design task"/saving an exploratory brief;
`feature-design` for "let's design X"/"review design tasks"/`designrev`;
`proposal-review` for other proposals/questions; `superdevloop` for autonomous
queued implementation/review; `signoff` for a walkthrough of delivered work.
"Sign off X" requests that walkthrough and a verdict; "approve X" supplies the
user's verdict and does not require another walkthrough. Skills need no client
installation or new task type.

Continue from last returned revisions after writes/pauses. Reconcile conflicts;
inspect the board before retrying uncertain creation. No routine confirming
reads are needed. Cards/summaries are not specifications. Whole-field body/
criteria replacements need the full-spec etag; valid updates return its successor.
Fetch only needed full specifications and chosen proof with `get_tasks(
specification=true, attempt_ids=[...])` or `get_attempt`, and page deliberate
history/membership with `list_task_attempts`/`list_group_members`.
