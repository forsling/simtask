# Local task workspace

Run `task-mcp ui` and open the printed private link. This works independently of
any MCP host or model. The browser shows and steers: you read tasks, ask
questions, save quick ideas, keep notes, change workstreams, reorder and defer,
resume or drop work there. Agents write tasks and record results, reviews and
decisions with you; apart from quick ideas and notes, the browser creates, edits
and signs off nothing. `--db /absolute/path/tasks.sqlite3` selects a database at
launch; the default follows `TASK_MCP_DB`, then `XDG_DATA_HOME`, then the normal
user data directory. The browser cannot select another database. The MCP
`open_task_viewer` tool explicitly starts/reuses the same viewer for its Store
and returns the link. No browser is launched automatically. On any database other
than the live one (a `./run.sh --dev` copy, `TASK_MCP_DB` or `--db`) the
header shows a `DEV` badge on every view, with the database path in its tooltip,
and the tab title starts with `[DEV]`.

## Location URLs

After the launch link connects, the address bar shows a short, token-free path
for where you are, for example `/w/1c4684b6/t/id/readable-task-ids` (task `readable-task-ids` in
workstream `1c4684b6`). A workstream or group implies its project, so only the
project-wide views name one:

| Path | Location |
| --- | --- |
| `/w/<workstream>` and `/w/<workstream>/t/id/<task>` | A workstream, optionally with a selected task |
| `/p/<project>` and `/p/<project>/t/id/<task>` | The project's **All tasks** |
| `/p/<project>/u` and `/p/<project>/u/t/id/<task>` | The project's **Unassigned** tasks |
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
- **Unassigned**, directly below **All tasks** in each project, lists that
  project's tasks that belong to no workstream: zero effective memberships,
  counting direct members and members inherited through a group a workstream
  includes, and honouring the workstream's exclusions of the task or its group. A
  membership in an archived workstream still counts, so archiving a workstream
  never moves its tasks here. The server filters and pages over these tasks
  only (a viewer-only `list_tasks` option, not an MCP argument), so assigned
  tasks never fill a page. The list uses the usual sections and each task's
  project-wide standing, as in All tasks; search, `j`/`k`, task details,
  questions, prerequisites, results and sign-off work as there. It is a
  placement filter, not a status or an agent's queue, and has no order to
  drag. **Add to workstream** stays in each task's next step and **Actions**
  (a task whose result passed review shows **Sign off with an agent** in its
  next step instead, in any placement, and keeps placement in **Actions**);
  after adding, the task leaves Unassigned on the next refresh, and removing a
  task's last membership brings it back. With nothing to show it says **No
  unassigned tasks**. In **All tasks**, each card of a task in no workstream
  carries a small **Unassigned** marker after its title, so that a task under
  Open is not taken for work an agent can pick up; workstream cards have none.
  Task details show the placement as *Unassigned* (agents' MCP tools call it the
  inbox, for example `state=inbox`).
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
  work is a first attempt or a rework round shows in the task's **Activity**,
  where every result, review and rejection appears.
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
- Task and group details have two tabs, **Spec** and **Activity**, below the
  title, public ID, current status and the next-step panel (which keeps **Sign
  off with an agent**); the **Actions** menu stays in the top bar. Selecting a
  task or group opens **Spec**; a refresh of the same one (the Refresh button,
  `r`, returning to the tab, or reloading after an action) keeps the selected
  tab, the loaded history, opened results and the scroll position. The tabs
  are an ARIA tablist: Left/Right arrows, Home and End move between them; they
  stay pinned below the top bar while the details scroll. The selected tab is
  not part of the location URL.
- **Spec** holds what was asked and where it stands: current unresolved items,
  prerequisites, the current-result card, the specification, acceptance
  criteria, the original request, the task's notes and any gate proposals; for
  a group, its summary, context, done-when criteria, its notes, included
  workstreams and members, with progress in the header above the tabs.
- The **current-result card** on Spec shows one result, if one is relevant: for
  a task awaiting sign-off, the reviewed result (passed review or reviewed by
  you) for the current specification in view; for a completed task, the
  accepted result; otherwise the latest current-specification result still with
  the agents (under review or sent back), if any. "In view" is the open
  workstream, or every workstream on project-wide boards; a result from another
  workstream says so. The card gives the state in plain words, the short
  summary, the workstream, the specification revision (current or superseded)
  and the evidence handles as recorded (each artifact or commit reference). It
  does not repeat evidence, verification, review or concerns. **Open full
  result** switches to Activity and opens that result's entry, even when it is
  outside the loaded pages: the viewer reads the newest page and the page that
  starts with the result, not the history between them, and shows a **Load
  entries in between** row there until the gap is filled.
- **Activity** is the task's or group's meaningful history from the paged
  `activity` read below, newest first: creation, edits, questions and answers,
  prerequisites, workstream and status changes, group membership, every result
  (the current one included), independent and human reviews and sign-off
  decisions, each once. Routine reads and failed requests are not shown. It
  loads when first shown: 20 entries, then **Load more** adds up to 20 more
  below, keeping what is loaded and the scroll position, and disappears at the
  beginning of history. A short page that reached the server's scan bound
  continues automatically. With nothing to show it says **No activity yet**. A
  page that fails keeps the loaded history and shows the error with **Retry**
  in place. When the server has entries newer than the top one (checked on each
  refresh while Activity is shown), **Newer activity is available · Show
  newer** adds them on top. Each result entry shows its state, workstream,
  specification revision and whether that specification is current or
  superseded (imported results are marked); **Show full result** reads that
  result with the `attempt` read and shows its complete evidence, artifacts,
  verification, review and worth-doing/approach concerns. Review and sign-off
  entries show their verdict, reasons and the result's workstream and
  specification, with **Show the result**; sign-off entries include the full
  reasons and any historical judgment fields of the recorded decision. A group's
  Activity shows the group's own changes and member joins only, not its
  members' history. The former Result, Other results, Sign-off decisions and
  Latest rejection sections are gone: those records are entries in Activity.
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
- The **Unresolved items** section is a plain list on the normal background,
  marked by a thin orange rule at its left edge and a count beside the heading
  (read out as "N current items"). Each current item appears in full, in stored
  order, through the same safe Markdown rendering as the specification, with a
  thin divider between items; nothing is truncated or folded. Answered items
  leave the list and stay in **Activity**. The section is hidden when a task has
  no current unresolved items.
- Every open task with unresolved items (captured feature briefs, other
  questions, saved ideas and Unassigned tasks alike) offers one **Design with
  agent** button beside the **Unresolved items** heading; the next-step panel
  does not repeat it. It copies a prompt naming the task
  (`Design with me: <public-id> — <title>. …`, with older tasks keeping their
  `tsk_` ID) that asks the agent to read the current specification and
  unresolved items, work through them with you and record the decisions you
  agree on: a captured feature brief goes through the task-design skill, other
  items are handled by their own context. The prompt asks for discussion only,
  not implementation, and carries no copy of the questions. No workstream is
  needed first: an Unassigned task still shows its separate placement action, and a
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
  the named one. Details list every effective membership accurately.
  **Unassigned** lists tasks included in none. Controls require no typed note, preserve proof
  and clear no questions/prerequisites.
- **Ask a question** takes one short line; the question holds the task until an
  agent settles it with you. **Defer** and **Resume** (from Later) take effect at
  once. **Drop** asks “are you sure” and takes an optional short reason. Bringing
  back a dropped task with **Resume** asks why it is coming back, which the Store
  keeps as the instruction that revived it. Each change records a reason in the
  history: yours, or a plain stand-in such as “Deferred in the browser.”
  Workstreams, details and results are kept through every status change.
- **+ Idea** (beside Refresh, in All tasks, Unassigned and workstream views) saves a thought
  before it is lost: **Idea title (required)** (one line, up to 200 characters), an
  **ID** and a multiline **Details (optional)** area (up to 500). The ID is filled in
  as you type the title, with the server's short-slug rule (README, Task identity),
  and you can change it; until you do, saving lets the server derive it from the
  final title by the same rule. A changed ID follows the `public_id` rules (lowercase
  letters, digits and single hyphens, starting with a letter, up to 40 characters); an
  invalid, over-long or taken ID is refused with a plain message asking for another,
  and nothing is saved. The ID is permanent. Saving creates, in one audited
  Store step, a task saved to the project's **Unassigned** tasks (no workstream,
  even when opened from one; the Store's inbox) until it is assigned or
  processed: the short title and details as description, source user and
  the exact text as its request, held by one open item, “Idea to process: turn
  into a proper brief or task with the user”. The viewer confirms and opens it
  in **Unassigned** under **Design** with an orange dot. No
  agent picks it up as is. Its next step, **Go through it with an agent**,
  copies a prompt (`Go through my ideas, starting with <public-id> — <title>`);
  the task-capture skill then goes through your ideas one at a time with you,
  rewriting each into a proper brief or task (added to a workstream when you
  want it built), splitting it, or dropping it with your agreement, and
  resolves the idea item. This is a viewer operation only; there is no MCP tool
  for it.
- **Notes** are titled records that reference tasks, groups, workstreams and
  projects explicitly; agents keep the same notes with `create_note`,
  `update_note`, `get_notes` and `list_notes`. Task and group details have a
  **Notes** section on the **Spec** tab listing the active notes that
  reference them, by title and update time, newest-updated first. **Notes** in
  the list header opens the
  open workstream's notes and its project's notes in the detail pane (only the
  project's in All tasks, Unassigned and Task groups; Shared task groups have
  none). Archived notes stay out of every list until **Show N archived** (per
  list, for the tab), which lists them marked *archived*; **Hide archived**
  hides them again. Lists show 20 notes at a time, with **Show more**.
- Clicking a note opens it in the detail pane: its title and ID, *Active* or
  *Archived*, when and by whom it was created and last updated, its full text
  through the same safe Markdown rendering as task text, and its references
  (kind, name and ID; click one to open that task, group, workstream or
  project). Back returns to the task or the Notes panel. A refresh keeps the
  note open; a task click, `j`/`k` or navigation closes it. Notes have no
  address of their own.
- **+ Note** in a notes list opens **Add a note** with that list's task, group,
  workstream or project already referenced; **Edit** opens the same form for an
  open note. Its title (up to 120 characters), text (up to 4,000) and references
  are saved together in one Store call. References come only from the picker.
  Type to search by title: the project's tasks and groups (in any workstream),
  every workstream and project, and other projects' tasks (read once you start
  typing, up to 500 per project). Press Enter or click a match to add it. A
  task or group that isn't found is added by its complete ID (**Look up … by
  ID**). Tasks are read through the board's read-only task listing. Each
  reference is a chip that can be removed. A note needs at least one
  reference, since notes are found only through them, so the form refuses to
  save without one. The text is never scanned for references. **Archive** and **Unarchive** take
  effect at once. Notes cannot be pinned or deleted. Notes written here record
  the viewer's actor, `local-browser-human`.
- Rejection reasons (reviewer rework, sign-off rework or revise) appear in the
  review or sign-off entry in Activity, with the result's workstream and
  specification. Sign-off decisions, including historical defer decisions and
  judgment fields, remain readable there. Task text is rendered as a safe
  Markdown subset (paragraphs, lists, headings, code, bold/italic) built from
  DOM text nodes, never parsed as HTML. Link targets are shown as text and
  never followed or fetched. A single-line acceptance criterion joined by
  semicolons is displayed as a list; the stored text is unchanged. No remote
  fonts, scripts or image services load. Light and dark themes follow the OS.

Apart from **+ Idea** and notes, the browser does not create or edit tasks, projects or workstreams, answer
unresolved items, record results or reviews, sign off, pick the next
agent action, edit group scope or membership, or handle gate proposals. Agents
do these through MCP, with you. The browser intentionally has no general Store
method, SQL or shell command endpoint.

## Activity history read

The viewer dispatch action `activity` (`Store.task_activity`) reads one task's
or group's meaningful history, newest first, in pages. It has no MCP tool and
does not change `list_events`; agents keep `list_task_attempts` and
`get_attempt`. The viewer's Activity tab renders it. Opening a result entry uses
the viewer dispatch action `attempt` (`Store.get_attempt`, the same attempt read
as the MCP tool; its `attempt.read` audit event is hidden from Activity).

- **Input**: `task_id`, plus either `cursor` (entries older than that event
  sequence; pass the previous page's `next_cursor`) or `target` (an attempt ID of
  this task: the page that starts with that result, without reading newer
  history).
- **Output**: `items` (at most 20), `newest_sequence` (the newest meaningful
  entry now, so the viewer can offer a refresh when it is newer than its top
  entry), `next_cursor` (absent when exhausted), `object_type`, and
  `scan_limited: true` only when the scan bound below was reached. Each entry has
  `sequence`, `timestamp`, `actor`, `kind`, `action` and a one-line `summary`;
  free text (review reasons, notes, questions) is clipped to 300 characters.
  Results add `attempt_id`, `workstream_id`/`workstream_name`, `spec_revision`,
  the attempt's current `state`, `implementer` and the short summary; reviews add
  `verdict`, `reviewer` and `reasons`; sign-offs add `decision`, `reasons` and
  the resulting `disposition`. Full proof is not included: open a result with
  the existing attempt read (the viewer's `attempt` action).
- **Classification**: `ACTIVITY_KINDS` in `store.py` maps each meaningful audit
  action to an entry kind: creation and import (`created`, `imported`, legacy
  `corrected`), specification and card-summary edits (`updated`), questions
  (`question_added`, `question_resolved`), prerequisites, workstream
  membership, disposition, decomposition, group membership (`member_added`, on
  the group), results, independent reviews, human reviews, sign-off decisions,
  and the retired `accepted` and `proposal_dismissed`. `ACTIVITY_HIDDEN` lists
  every other action with its reason: reads (including `activity.read` itself),
  project/session/workstream/note changes that are never recorded on a task, and
  workstream ordering. Failed requests are never shown. `tests/test_activity.py`
  reads every action the code under `src/` can emit and fails on any action in
  neither map: `Store._run` and `Store._event` action literals (or conditionals
  between literals), and the action column of each literal `INSERT INTO events`
  passed to `execute` or `executemany`, as an SQL literal or a literal
  parameter. It also fails on anything it cannot read: a non-literal action
  (except `_run` forwarding its own to `_event`), or `INSERT INTO events` text
  held in a variable, built at run time or given unreadable parameters.
- **Filtering and bounds**: the meaningful-action filter runs in SQL before
  paging, so hidden reads never shorten a page. Each paging query examines at
  most 2000 newest-first events below the cursor: the task's own, or for a group
  its own and its members' together (the busiest real task had about 150 events,
  the busiest group with its members about 600). If that bound is reached first,
  the page holds what was found, `scan_limited` is set, and `next_cursor`
  resumes exactly where the scan stopped, so nothing is skipped. If a first page
  is empty because of the bound (more than 2000 hidden events in a row, never
  seen in real data), `newest_sequence` is null until a later page finds an
  entry. Two lookups fall outside the bound, because the events table has no
  index on action and this read adds no schema change: telling imported results
  from live ones (only for a task that has results), and finding a `target`'s
  `attempt.recorded` event. Each walks the task's own events once through the
  `task_history` index, so its cost grows with that task's event count, not
  with the database.
- **Stable paging**: cursors are event sequences. New events only get higher
  sequences, so writes between page loads cause no duplicates or gaps.
- **Imported results**: attempts with no `attempt.recorded` event (the
  2026-09-26 TASKS.md migration) are shown as `result` entries with
  `imported: true` at the import event's sequence, newest first, directly above
  the import entry (which counts them in `imported_results`). That group is
  never split across pages: if it does not fit, the page ends before it. (Only a
  task with more than 19 imported results would make a page exceed 20 entries;
  the real data has at most one per task.) A result with an `attempt.recorded`
  event is shown only from that event, so each result appears once.
- **Groups** show their own events, plus the membership changes recorded on a
  member: `create_task` or `update_task` with `group_id` writes the join on the
  new member's own task, so the group shows each as a `member_added` entry
  (`via: "create_task"` or `"update_task"`, with `member_id` and `title`) at that
  event's sequence, paged with the group's own events under the same cursor and
  bound. `add_group_member` is recorded on the group (`via:
  "add_group_member"`). Nothing else of a member's history appears on the
  group: members' history is not aggregated. Members are found by their current
  group; a task cannot leave or change group (`update_task` rejects
  reassignment, and repeating the current group changes nothing and is not
  shown; only the one-off 2026-09-26 migration correction removed members, and
  it is recorded on the group). A member created by decomposition starts with a `created` entry at the
  group's decomposition event (`via: "decomposition"`), since that member has no
  creation event of its own.

## Concurrent edits

Every change submits the revision the browser read. If another browser or agent
changes the same task, the Store rejects the stale write. In a dialog, what you
entered stays open: show the current task, then explicitly choose that version
before submitting again. **Defer** and **Resume** from the menu save nothing on a
conflict; the task reloads and says so, and you can try again. The Store always
makes the final eligibility check. A network failure is not proof a write
failed: check the task's history before retrying.

Notes work the same way with the note's revision. If an agent or another tab
changed a note after you opened it, saving an edit saves nothing: the note
reloads, and the form keeps your draft beside the current version. **Edit the
current version** loads that version into the form; **Keep my draft** makes your
next save replace it. **Archive** or **Unarchive** on a changed note saves
nothing, reloads it and says so.

## Security and lifecycle

The companion binds `127.0.0.1`, never a public interface, on an OS-selected
port. Host validation (a literal `127.0.0.1:<port>`) protects against DNS
rebinding; any loopback port is accepted so that an SSH tunnel from another
laptop port (`./run.sh --remote`) works. With `--public-origin` (what
`./run.sh --tailscale` passes), the one published name and port are accepted as
well, for requests `tailscale serve` forwards with the browser's own Host
header; the link then uses that origin. API calls require a high-entropy token
in a custom header, JSON content type, and an Origin header exactly matching
that Host. No CORS permission is granted; cross-site browser
requests are rejected. CSP disallows remote resources, framing and inline
scripts. The private link places its token in the URL fragment, which is not
sent to HTTP logs; the app immediately removes it from the address bar,
retains it in this origin's local storage across tabs and browser restarts and replaces it with a token-free
location path. The server serves the same app page for `/` and for well-formed
location paths (`/p/…`, `/w/…`, `/g/…`, `/sg…` with hexadecimal IDs) under the
same Host/Origin/fetch-site checks; the page carries no data, and every API call
still needs the token header. Other paths remain 404. Restarting the viewer preserves
the token in its private mode-0600 `<database>.viewer.token` file. That file also
survives a fresh dev database copy, so a bookmarked Tailscale Serve link stays valid. Other local processes with the same user's filesystem privileges remain
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
Codex setting is installed or changed by the launcher; a systemd user service
is installed only on request (`./run.sh --tailscale --install-service`, see the
README), and `systemctl --user stop` ends that viewer cleanly through SIGTERM.
The current launcher targets POSIX systems (Linux/macOS); Windows is not
supported by this launcher.

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
the allowed membership, question, quick idea, note and status changes, rejection of removed
actions, stale revisions, Store gate enforcement, local/global scope
distinctions and cross-process launch/stop/restart.

Result details show worth-doing and approach concerns (stored kinds
`value`/`design`) with the implementer or reviewer name and original
attempt/workstream/specification.
These concerns are nonblocking; recorded gates and the user's actual verdict
retain their existing meaning. Other and superseded results keep their own
concerns in their Activity entries.
