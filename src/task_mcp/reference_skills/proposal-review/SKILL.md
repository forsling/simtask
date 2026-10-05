---
name: proposal-review
description: Review inbox proposals and open questions with the user and record their decisions. Use in projects using Task MCP for ordinary proposals or questions; use task-design for feature design.
---

# Proposal review

1. List inbox proposals and tasks with open questions with `list_tasks`, then
   fetch the relevant full specifications.

2. Hand any task with a `Feature design required` question to task-design.
   Everything else is reviewed here.

3. Present one proposal at a time: its goal, scope, acceptance criteria,
   settled decisions and open questions. Settle whether it is worth doing
   before discussing how.

4. Record what the user decides. Rewrite the specification with each answer
   (`update_task`), then resolve only the settled questions. Add questions or
   blockers the user identifies. Raise a new question only when it risks
   material wasted work or needs a decision only the user can make.

5. Adopt what the user wants done with `add_to_workstream`. Leave the rest in
   the inbox, or defer or drop it with `set_disposition` and the user's reasons.

6. Report the decisions, the task IDs that changed and anything still open.
