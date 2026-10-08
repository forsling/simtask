---
name: task-capture
description: Save a task, bug or researched feature brief with its open design questions, or go through saved ideas. Use in projects using simtask for "add a task", "add a design task", an idea that needs design or "go through my ideas"; use task-design for the design discussion.
---

# Task capture

1. Look for duplicates and earlier decisions with `list_tasks` (including
   inactive tasks) and the relevant full specifications.

2. Research just enough code and documentation to describe the current
   behavior, integration points and constraints. Say what you could not verify.

3. Title the task with its observable outcome in about 70 characters: one main
   fact, no internal slugs or file names. Pass a short `public_id` of about 2-4
   words (for example `idea-ids`) on every create; it never changes. On a
   conflict, choose another.

4. Write the body as the specification only. Separate the user's decisions
   from suggestions. Acceptance criteria go in their own field; evidence
   belongs in attempts. Pass ordinary text with actual line breaks through
   your JSON serializer; do not pre-escape the specification.

5. For a bug, write a reproduction requirement into the task: the work must
   demonstrate the bug before changing code.

6. Decide placement. Tasks the user requested go to the current workstream.
   Ideas you suggested are saved only after the user confirms them, and they
   stay in the inbox with the rest of the backlog. Explicit placement
   instructions win.

7. Save it with `create_task`; concrete, settled work is then ready. For an
   idea that still needs design, create it without a workstream, add this open
   question with `add_unresolved`, and only then `add_to_workstream` if the user
   requested it, so it is never eligible without its gate:

   > Feature design required (task-design): decide whether the work is worth
   > doing, then agree the approach, scope and acceptance criteria with the user.

   Tailor the text, and add separate questions for decisions that can be
   settled independently. Routine, reversible details belong to the
   implementer, not to questions. Ask the user now only if ambiguity prevents
   a useful capture.

8. Report the task ID, title, findings and open questions. Capture starts no
   design discussion or implementation.

## Go through my ideas

Browser ideas are inbox tasks held by an `Idea to process:` item, which cards
don't show: read `state=inbox` cards with questions using `get_tasks` with
`specification`. On request, go one at a time with the user, never unattended:
rewrite in place, add to a workstream only if they want it built, decompose,
or drop by agreement. Then resolve the item.
