# Local task workspace

Run `task-mcp ui` and open the printed private link. This works independently of
any MCP host or model. The browser shows and steers: you read tasks, ask
questions, save quick ideas, change workstreams, reorder and defer, resume or
drop work there. Agents write tasks and record results, reviews and decisions
with you; apart from quick ideas, the browser creates, edits and signs off
nothing. `--db /absolute/path/tasks.sqlite3` selects a database at
launch; the default follows `TASK_MCP_DB`, then `XDG_DATA_HOME`, then the normal
user data directory. The browser cannot select another database. The MCP
`open_task_viewer` tool explicitly starts/reuses the same viewer for its Store
and returns the link. No browser is launched automatically.

## Location URLs

After the launch link connects, the address bar shows a short, token-free path
for where you are, for example `/w/1c4684b6/t/id/readable-task-ids` (task `readable-task-ids` in
workstream `1c4684b6`). A workstream or group implies its project, so only the
project-wide views name one:

| Path | Location |
| --- | --- |
| `/w/<workstream>` and `/w/<workstream>/t/id/<task>` | A workstream, optionally with a selected task |
| `/p/<project>` and `/p/<project>/t/id/<task>` | The project's **All tasks** |
| `/p/<project>/g` | The project's **Task groups** |
| `/g/id/<group>` | A group, in the view it implies: its project's **Task groups**, or **Shared task groups** when it spans projects |
| `/p/<project>/g/id/<group>` | A group shown in a project's **Task groups** other than the one it implies (a shared group, for example) |
| `/sg` and `/sg/id/<group>` | **Shared task groups** |

New task/group IDs appear in full after `/id/`, for example `readable-task-ids`; exact public
names resolve independently of other names with the same prefix. Cards and detail
headers show the complete selectable ID, with **Copy ID** in details. Long IDs wrap
on narrow screens. Search includes the public ID. Existing `tsk_…` IDs stay unchanged.
Hexadecimal-only public IDs are valid: `/g/id/e1e1e1e1` selects the new group with
that exact ID, while `/g/e1e1e1e1` continues to select the matching legacy group.

Project/workstream and legacy task/group IDs appear in URLs as the first 8
hexadecimal characters after their type prefix
(`wst_1c4684b6…` becomes `1c4684b6`). Only when that prefix is ambiguous is the
full 32-character ID used: tasks resolve within the view's task list, projects
among all projects, and workstreams and groups across the database. Any longer
or shorter prefix also works in a typed address when it matches exactly one item.
Legacy task/group URLs omit `/id/`, for example `/w/1c4684b6/t/a5dfba02`.
Shared task groups name no project, so the sidebar keeps the project that was
open (after a reload, the group's own project or the first project).

Reloading returns to the same project, view and selection.
Back/forward move between locations visited in the viewer: sidebar links, task
and group clicks, group chips, and prerequisite, member and workstream links
each add an entry, while `j`/`k` moves and automatic first-task selection
update the current entry. A copied or bookmarked location reopens in a tab that
already holds the token; elsewhere, open the private launch link first, which
starts on the first project's first workstream. Location URLs never contain the
token. A location whose project, workstream, task or group no longer exists,
which the task has left, or whose prefix matches more than one item, opens the
nearest valid view (the open project's All tasks or Task groups, Shared task
groups, the first project, or the view's first item) with a short notice. The
earlier `#/project/...` addresses are not supported; they open the default view.

## Working with tasks

- The sidebar lists projects; the current project expands to its registered
  workstreams, each with a count of tasks that need you: exactly the tasks its
  Signoff and Design sections show. The server decides where every task stands
  by one rule, used for the sections, the task's details and these counts, and
  returns each workstream's counts with the workstream list. The viewer reads
  that list again on every refresh, group boards included, and reads no other
  workstream's tasks to count. A workstream where nothing needs you shows its
  task total instead (the tooltip says “none need you”); `?` means the server
  sent no count (restart an older viewer). If a refresh of the list fails, the
  viewer says so once, dims the last counts and shows **Counts may be out of
  date · Retry** until a later refresh succeeds. “All tasks” includes
  tasks outside any one workstream. Workstream names and counts describe
  recorded state, not a running agent.
- Above the list, the project's note and, in a named workstream, that
  workstream's note show as read-only plain text with when and by whom each was
  last updated; empty notes are not shown. Collapse either one with its header.
  Agents keep notes with the `set_note` MCP tool.
- Archived workstreams are hidden from the sidebar and never opened by default.
  **Show N archived** under a project's workstreams lists them, marked
  *archived*, until **Hide archived** (remembered for the tab). A link to an
  archived workstream still opens it, keeps it in the sidebar while open and
  shows its archive reason next to the task count. The add-to-workstream picker
  leaves archived workstreams out; removing a task from one still works.
  Archiving and unarchiving are agent operations (`archive_workstream`).
- The list groups tasks by where they stand for you, not by the agents'
  internal stage: **Signoff** (badge **Sign-off** when a current-spec result
  passed review or was human-reviewed), then **Design** (orange dot when any
  unresolved item holds the task, including design briefs and tasks revised
  at sign-off), **In progress** (a result is recorded and still with the
  agents: under review or being fixed after review; or an agent picked the task
  up through `get_next_action` in the last 4 hours, in this workstream or on All
  tasks in any, and has recorded nothing since, shown as "An agent picked this up 12 minutes ago"), **Open** (no result yet and no recent pick,
  ready or blocked; a blocker shows in the task's details), **Later** (deferred)
  and **Done** (done/dropped, collapsed). Cards carry no other badges; whether
  work is a first attempt or a rework round shows in the task's details and
  history, where the latest rejection appears only until a newer result exists.
  Search filters titles across every section. Keyboard: `/` search, `j`/`k` move,
  `r` refresh.
- In a named workstream, drag a task onto the upper or lower half of another
  task to place it before or after that task. A row's tooltip gives its position
  in that workstream (sections mix positions, so rows show no numbers); a drop
  line and hint state the result, including when the task
  stays in a different status section. Hidden, searched-out and collapsed
  members keep their relative order. Only the selected workstream changes; its
  members keep every membership, specification and proof. Escape, self drops
  and unchanged positions save nothing. A failed save or a stale order revision
  shows an error, puts back the loaded order and locks dragging until the
  recorded order has reloaded; clicking a task meanwhile does not cancel that
  reload. All tasks and group views have no order to edit.
- Each task opens with a single “next step” panel stating what, if anything,
  you can do now. A task waiting for sign-off offers **Sign off with an agent**:
  sign-off is a walkthrough with an agent, so the button copies a ready prompt
  (`Sign off tsk_<id> — <title>`) for you to paste into your agent and confirms
  with a short notice. The browser launches no agent and records no verdict.
  Where the clipboard cannot be written, the prompt appears selected beside the
  button for you to copy. A result that passed review stays under Signoff even
  while a prerequisite is still open; the panel names the open prerequisite in
  one line so the walkthrough can weigh it. Blocked tasks without a passed
  result show the prerequisites they wait for. Unresolved items are
  shown for reading under **Unresolved items**; you settle them with an agent,
  which records the answer. A passed result does not remove unresolved items or prerequisites;
  Store still validates every decision.
- Every open task with unresolved items (captured feature briefs, other
  questions, saved ideas and inbox tasks alike) offers one **Design with
  agent** button beside the **Unresolved items** heading; the next-step panel
  does not repeat it. It copies a prompt naming the task
  (`Design with me: <public-id> — <title>. …`, with older tasks keeping their
  `tsk_` ID) that asks the agent to read the current specification and
  unresolved items, work through them with you and record the decisions you
  agree on: a captured feature brief goes through the task-design skill, other
  items are handled by their own context. The prompt asks for discussion only,
  not implementation, and carries no copy of the questions. No workstream is
  needed first: an inbox task still shows its separate placement action, and a
  saved idea keeps **Go through it with an agent**. Copying confirms with a short
  notice, or shows the prompt selected beside the heading when the clipboard
  cannot be written; it launches no agent and changes no task, membership,
  question or proof. Deferred, dropped and completed tasks keep only their
  lifecycle actions.
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
- A task's **Actions** menu offers **Ask a question** (open tasks, including
  those sent back for rework), **Add to workstream**, **Remove from
  workstream**, and **Defer** and **Drop**, or **Resume** for deferred and
  dropped tasks. Completed tasks offer no actions.
  **Add to workstream** and **Remove from workstream** identify the workstream
  being changed; adding retains existing memberships and removing changes only
  the named one. Details list every effective membership accurately. The inbox
  contains tasks included in none. Controls require no typed note, preserve proof
  and clear no questions/prerequisites.
- **Ask a question** takes one short line; the question holds the task until an
  agent settles it with you. **Defer** and **Resume** (from Later) take effect at
  once. **Drop** asks “are you sure” and takes an optional short reason. Bringing
  back a dropped task with **Resume** asks why it is coming back, which the Store
  keeps as the instruction that revived it. Each change records a reason in the
  history: yours, or a plain stand-in such as “Deferred in the browser.”
  Workstreams, details and results are kept through every status change.
- **+ Idea** (beside Refresh, in All tasks and workstream views) saves a thought
  before it is lost: **Idea title (required)** (one line, up to 200 characters), an
  **ID** and a multiline **Details (optional)** area (up to 500). The ID is filled in
  as you type the title, with the server's short-slug rule (README, Task identity),
  and you can change it; until you do, saving lets the server derive it from the
  final title by the same rule. A changed ID follows the `public_id` rules (lowercase
  letters, digits and single hyphens, starting with a letter, up to 40 characters); an
  invalid, over-long or taken ID is refused with a plain message asking for another,
  and nothing is saved. The ID is permanent. Saving creates, in one audited
  Store step, a task in the project's inbox (no workstream, even when opened
  from one): the short title and details as description, source user and
  the exact text as its request, held by one open item, “Idea to process: turn
  into a proper brief or task with the user”. The viewer confirms and opens it
  in **All tasks** under **Design** with an orange dot. No
  agent picks it up as is. Its next step, **Go through it with an agent**,
  copies a prompt (`Go through my ideas, starting with <public-id> — <title>`);
  the task-capture skill then goes through your ideas one at a time with you,
  rewriting each into a proper brief or task (added to a workstream when you
  want it built), splitting it, or dropping it with your agreement, and
  resolves the idea item. This is a viewer operation only; there is no MCP tool
  for it.
- Latest rejection reasons appear with their originating attempt/workstream in
  the task. Sign-off decisions, including historical defer decisions and
  judgment fields, remain readable.
- Activity shows actor, action, outcome and decision notes; routine read
  events are counted rather than listed. Task text is rendered as a safe
  Markdown subset (paragraphs, lists, headings, code, bold/italic) built from
  DOM text nodes, never parsed as HTML. Link targets are shown as text and
  never followed or fetched. A single-line acceptance criterion joined by
  semicolons is displayed as a list; the stored text is unchanged. No remote
  fonts, scripts or image services load. Light and dark themes follow the OS.

Apart from **+ Idea**, the browser does not create or edit tasks, projects or workstreams, answer
unresolved items, record results or reviews, sign off, pick the next
agent action, edit group scope or membership, or handle gate proposals. Agents
do these through MCP, with you. The browser intentionally has no general Store
method, SQL or shell command endpoint.

## Concurrent edits

Every change submits the revision the browser read. If another browser or agent
changes the same task, the Store rejects the stale write. In a dialog, what you
entered stays open: show the current task, then explicitly choose that version
before submitting again. **Defer** and **Resume** from the menu save nothing on a
conflict; the task reloads and says so, and you can try again. The Store always
makes the final eligibility check. A network failure is not proof a write
failed: check the task's history before retrying.

## Security and lifecycle

The companion binds `127.0.0.1`, never a public interface, on an OS-selected
port. Host validation (a literal `127.0.0.1:<port>`) protects against DNS
rebinding; any loopback port is accepted so that an SSH tunnel from another
laptop port (`./run.sh --remote`) works. API calls require a high-entropy token
in a custom header, JSON content type, and an Origin header exactly matching
that Host. No CORS permission is granted; cross-site browser
requests are rejected. CSP disallows remote resources, framing and inline
scripts. The private link places its token in the URL fragment, which is not
sent to HTTP logs; the app immediately removes it from the address bar,
retains it in that tab's session storage and replaces it with a token-free
location path. The server serves the same app page for `/` and for well-formed
location paths (`/p/…`, `/w/…`, `/g/…`, `/sg…` with hexadecimal IDs) under the
same Host/Origin/fetch-site checks; the page carries no data, and every API call
still needs the token header. Other paths remain 404. Restarting the viewer rotates the
token. Other local processes with the same user's filesystem privileges remain
inside this trust boundary; actor labels and human assertions are not identity
authentication.

The launcher writes a mode-0600 `.viewer.json` sidecar beside the resolved
database and uses `.viewer.json.launch` / `.viewer.json.lock` files for POSIX
cross-process locking. Do not share the sidecar or private link. These are
runtime discovery files, not a task copy. The JSON file is removed on normal
shutdown; empty lock files can remain. A stale JSON file after a crash is
replaced on the next explicit launch. The database directory must be writable.

The viewer snapshots its HTML, JavaScript and CSS at startup so source changes
cannot expose newer controls through an older running backend. After updating
the code, stop and relaunch the viewer to load the backend and UI together.

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
Store, and browser changes have the same revisions, workflow checks and history
as MCP changes. Possession of the link grants access to all projects in that
configured database, not just the current workstream.

## Disposable verification

`python examples/viewer_demo.py` creates a temporary database with a realistic
two-project initiative, a reviewed result, open question, proposal and deferred
task. It prints the private link and exact stop command. All decisions in that
database are synthetic. `pytest tests/test_viewer.py` covers protected access,
the allowed membership, question, quick idea and status changes, rejection of removed
actions, stale revisions, Store gate enforcement, local/global scope
distinctions and cross-process launch/stop/restart.

Result details show worth-doing and approach concerns (stored kinds
`value`/`design`) with the implementer or reviewer name and original
attempt/workstream/specification.
These concerns are nonblocking; recorded gates and the user's actual verdict
retain their existing meaning. Other/superseded results keep their own concerns
in the existing labelled history.
