# Local task workspace

Run `task-mcp ui` and open the printed private link. This works independently of
any MCP host or model. `--db /absolute/path/tasks.sqlite3` selects a database at
launch; the default follows `TASK_MCP_DB`, then `XDG_DATA_HOME`, then the normal
user data directory. The browser cannot select another database. The MCP
`open_task_viewer` tool explicitly starts/reuses the same viewer for its Store
and returns the link. No browser is launched automatically.

## Location URLs

After the launch link connects, the address bar shows where you are, for example
`/#/project/<project-id>/workstream/<workstream-id>/task/<task-id>`. The view is
`all` (All tasks), `workstream/<id>`, `groups` (the project's task groups) or
`shared-groups`, optionally followed by `task/<id>` or, in group views,
`group/<id>`. Reloading returns to the same project, view and selection.
Back/forward move between locations visited in the viewer: sidebar links, task
and group clicks, group chips, prerequisite, member and workstream links and **Next agent
action** each add an entry, while `j`/`k` moves and automatic first-task selection
update the current entry. A copied or bookmarked location reopens in a tab that
already holds the token; elsewhere, open the private launch link first, which
starts on the first project's first workstream. Location URLs never contain the
token. A location whose project, workstream, task or group no longer exists, or
which the task has left, opens the nearest valid view (the project's All tasks,
the first project, or the view's first item) with a short notice.

## Working with tasks

- The sidebar lists projects; the current project expands to its registered
  workstreams, each with a count of tasks that need you. “All tasks” includes
  tasks outside any one workstream. Workstream names and counts describe
  recorded state, not a running agent.
- The list groups tasks by what they need: **Needs you** (sign-off, inbox, open questions), **In progress** (in review, ready, blocked),
  **Later** (deferred) and **Closed** (done/dropped, collapsed). Search filters
  titles across every section. Keyboard: `/` search, `j`/`k` move, `n` new
  task, `e` edit, `r` refresh.
- Above the list, the project's note and, in a named workstream, that
  workstream's note show as read-only plain text with when and by whom each was
  last updated; empty notes are not shown. Collapse either one with its header.
  Agents keep notes with the `set_note` MCP tool.
- In a named workstream, drag a task onto the upper or lower half of another
  task to place it before or after that task. Rows show their position in that
  workstream; a drop line and hint state the result, including when the task
  stays in a different status section. Hidden, searched-out and collapsed
  members keep their relative order. Only the selected workstream changes; its
  members keep every membership, specification and proof. Escape, self drops
  and unchanged positions save nothing. A failed save or a stale order revision
  shows an error, puts back the loaded order and locks dragging until the
  recorded order has reloaded; clicking a task meanwhile does not cancel that
  reload. All tasks and group views have no order to edit.
- Each task opens with a single “next step” panel stating what, if anything,
  you can do now, with its buttons. A review/sign-off state does not remove
  open questions or prerequisites; Store still validates every decision.
- Prerequisites show compact ID/title, project name/ID, the required review or
  sign-off milestone, satisfaction and canonical blocking/completion
  state, including blockers from any project. Rendering these rows fetches no
  remote attempts, evidence or queue; click a link to deliberately open its task
  or global group. Default review links clear on done or a current-spec passed
  independent review/recorded human review; every member must satisfy a group
  link. Explicit sign-off links are rare exceptions for work very likely wasted
  before the human verdict. Dropped/deferred work still blocks, and rework/spec
  changes can block review links again. Satisfaction proves no local integration.
- Shared groups show all members across all projects and global completion.
  Opening a member moves to its own project's queue. A group's completion does
  not mean a particular local workstream delivered all of its members.
- New tasks are added to the selected workstream, or have no direct membership
  from All tasks (existing group inclusion may still apply). Edits preserve scope.
  **Add to workstream** and **Remove from workstream** identify the workstream
  being changed; adding retains existing memberships and removing changes only
  the named one. Details list every effective membership accurately. The inbox
  contains tasks included in none. Controls require no typed note, preserve proof
  and clear no questions/prerequisites.
- Answer questions or defer/resume/drop with an actual decision note. Dropped and
  deferred context remains recoverable; revival from dropped needs authorization.
  Completed requirements cannot be edited; summary corrections remain available.
- Review the result and evidence before recording a human review. The dialog
  records your review or your explicit instruction that additional independent
  review is unnecessary; it does not sign off. A separately confirmed sign-off
  offers approve (complete), rework (repair implementation), revise (return to
  design with an open question), and drop (close without approval). Its single
  reasons field is required for rework/revise and optional for approve/drop.
  Deferral remains an ordinary status change. Latest rejection reasons appear
  with their originating attempt/workstream in the task; list rows carry a flag.
  Historical defer decisions and judgment fields remain readable.
- Activity shows actor, action, outcome and decision notes; routine read
  events are counted rather than listed. Task text is rendered as a safe
  Markdown subset (paragraphs, lists, headings, code, bold/italic) built from
  DOM text nodes, never parsed as HTML. Link targets are shown as text and
  never followed or fetched. A single-line acceptance criterion joined by
  semicolons is displayed as a list; the stored text is unchanged. No remote
  fonts, scripts or image services load. Light and dark themes follow the OS.

This first browser version does not create projects/workstreams, edit bulk group scope
or group membership, or record implementation results. Those operations remain
available in MCP. It intentionally has no general
Store method, SQL or shell command endpoint.

## Concurrent edits

Dialogs submit the revision they read. If another browser or agent changes the
same task, the Store rejects the stale write. Your unsaved fields and note stay
open. Load the current version alongside your draft, reconcile the contents,
and explicitly choose that revision before submitting again. Full-detail reads
also supply the replacement token used when saving the body/criteria. Reconciliation
refreshes both revision and token. Decision dialogs require their confirmation checkbox again after reconciliation.
Placement conflicts preserve the chosen branch until explicit review of current state. The Store always makes the
final eligibility check. A network failure is not proof a write failed: inspect
the queue/history before retrying a create, which is not deduplicated.

## Security and lifecycle

The companion binds `127.0.0.1`, never a public interface, on an OS-selected
port. Exact Host validation protects against DNS rebinding. API calls require
a high-entropy token in a custom header, JSON content type, and the exact
same-origin Origin header. No CORS permission is granted; cross-site browser
requests are rejected. CSP disallows remote resources, framing and inline
scripts. The private link places its token in the URL fragment, which is not
sent to HTTP logs; the app immediately removes it from the address bar,
retains it in that tab's session storage and replaces it with a token-free
location URL. Location URLs are fragments too, so the server still serves only
`/` and its assets. Restarting the viewer rotates the
token. Other local processes with the same user's filesystem privileges remain
inside this trust boundary; actor labels and human assertions are not identity
authentication.

The launcher writes a mode-0600 `.viewer.json` sidecar beside the resolved
database and uses `.viewer.json.launch` / `.viewer.json.lock` files for POSIX
cross-process locking. Do not share the sidecar or private link. These are
runtime discovery files, not a task copy. The JSON file is removed on normal
shutdown; empty lock files can remain. A stale JSON file after a crash is
replaced on the next explicit launch. The database directory must be writable.

The detached process outlives the launching terminal or stdio session. Stop it
with the confirmed **Stop viewer** action, or `task-mcp ui --db PATH --stop`.
Stopping the browser companion does not stop the MCP stdio server. Merely
closing a tab does not stop the listener. No startup/login hook, service or
Codex setting is installed or changed. The current launcher targets POSIX
systems (Linux/macOS); Windows is not supported by this launcher.

The launch MCP annotation is a non-read-only, non-destructive, idempotent,
closed-world operation. It creates/reuses a local listener and discovery files;
it is not a static read. Whole-server trust/pre-approval therefore also covers
this explicitly invoked capability. Browser reads append audit records through
Store, and browser edits have the same revisions, workflow checks and history
as MCP edits. Possession of the link grants access to all projects in that
configured database, not just the current workstream.

## Disposable verification

`python examples/viewer_demo.py` creates a temporary database with a realistic
two-project initiative, a reviewed result, open question, proposal and deferred
task. It prints the private link and exact stop command. All decisions in that
database are synthetic. `pytest tests/test_viewer.py` covers protected access,
allowed edit and decision flows, stale revisions, Store gate enforcement,
local/global scope distinctions and cross-process launch/stop/restart.

Result details and the sign-off dialog show value/design concerns with the
implementer or reviewer name and original attempt/workstream/specification.
These concerns are nonblocking; recorded gates and the user's actual verdict
retain their existing meaning. Other/superseded results keep their own concerns
in the existing labelled history.
