---
name: task-mcp-init
description: Explicitly initialize or attach a Task MCP project and workstream.
---

# Init

Inspect the checkout's canonical path and Git branch. Show the user the project
path, proposed branch or explicit workstream name, and initial scope before
calling any operation with `confirmed=true`. An unknown project offers three
choices: initialize it, attach this checkout to an existing project, or create a
new workstream in an already attached checkout. Use `list_projects` and
`list_workstreams` to distinguish them. Detached HEAD or non-Git work requires
the user to choose a workstream name.

For a new project, call `init_project`. For another checkout, call
`attach_checkout`; then call `init_workstream` if its branch needs a separate
scope. For a new branch in an attached checkout, call `init_workstream` with an
explicit set expression. `none` is an empty scope; a workstream name/ID copies
its current scope once; `+task-id` and `-task-id` change that snapshot. A group
reference is a live relation for future members. Do not infer scope from a
branch name or Git history. Call `preflight` before unattended work, and stop on
a mismatched binding until the user chooses `rebind_workstream` or a new one.
