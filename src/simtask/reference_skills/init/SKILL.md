---
name: init
description: Set up or resume the simtask binding for a checkout and branch. Use in projects using simtask when init reports an unregistered checkout, a new branch, a mismatch or an archived workstream.
---

# Init

1. Call `init` with the absolute checkout path and branch, or a
   `workstream_name` for detached or non-Git work. The current directory
   suggests a path but does not choose it.

2. When the result is ready, keep the returned project and workstream IDs
   and continue with the user's request.

3. Otherwise, pick the fitting call from `next` and show the user the path,
   branch, project, workstream and scope it would set up. Names and history do
   not imply scope; ask. Make the confirmed call with its arguments as given,
   adding `scope_expression` to a create call when the user chose a scope.

4. Resume, archive or restore an archived workstream (`archive_workstream`)
   only when the user directs.

5. After a rebind, the workstream's scope and history move but its files do
   not. Check the actual checkout and commits against the specification before
   relying on recorded results.

6. Fetch the skill that fits the request from `get_default_skills`.
