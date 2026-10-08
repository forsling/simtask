# Task MCP reference

The complete behaviour notes, section by section. The [README](../README.md) is the short version.


Task MCP is a local, opt-in task-state service for coding workflows. It stores
project tasks, workstream scopes, implementation attempts, review and human
sign-off in a private SQLite database. It exposes MCP primitives over stdio;
agents may use those primitives directly or follow the bundled reference
workflows. The service never edits repositories, client configuration or
installed skills. It writes an export file only when a caller explicitly saves
the returned export at a chosen destination.

## Install and verify

Requires Python 3.11 or newer. From this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests examples
.venv/bin/ruff format --check src tests examples
.venv/bin/python examples/demo.py
```

The demo uses a disposable database and a real subprocess MCP stdio connection.
It records only synthetic decisions. The configured executable for a client is
`/path/to/task-mcp/.venv/bin/task-mcp`; set `TASK_MCP_ACTOR` to a
descriptive attribution label if useful. The default database is
`$XDG_DATA_HOME/task-mcp/tasks.sqlite3`, falling back to
`~/.local/share/task-mcp/tasks.sqlite3`. Override with `TASK_MCP_DB` or `--db`.
No network listener is needed for stdio. Connect this command in each client's own MCP
configuration; Task MCP does not install its connection.

## Runtime identity and reconnecting

Every successful `init` response includes the server's runtime identity under
`runtime`. It reports package version, a startup-frozen SHA-256 source identifier,
Task MCP protocol/schema revision, process startup timestamp and PID, interpreter
and package paths, and the database's persisted schema revision. Use it after a
server update to verify which runtime the client actually reached.

An editable install updates files on disk; an existing Python process retains
its imported code. A client can also retain an old tool catalog after a new
process starts. These require different checks and both can happen together.
See [runtime fields and the reconnect procedure](runtime.md). Reinstalling,
toggling a connection, or passing a fresh subprocess demo alone does not prove
that the existing client has refreshed its process and discovered tools.

## Local browser workspace

An optional browser viewer works without an agent or model. It shows and steers;
agents write and decide with you. The quickest
way is `./run.sh`, which starts the viewer (or reuses the running one) and prints
its link; `./run.sh --restart` and `./run.sh --stop` restart or stop it, and
`TASK_MCP_DB=/path/tasks.sqlite3 ./run.sh` selects another database. From a dev
worktree, `./run.sh --dev` runs a separate viewer on a copy of the live database
(see the README's development section). The script wraps:

```sh
.venv/bin/task-mcp ui
# Select a different database explicitly at launch:
.venv/bin/task-mcp ui --db /absolute/path/tasks.sqlite3
# Stop that database's viewer:
.venv/bin/task-mcp ui --db /absolute/path/tasks.sqlite3 --stop
```

To use a viewer that runs on another machine from this one's browser, run
`./run.sh --remote [host]` (the host defaults to `$TASK_MCP_REMOTE_HOST`). Over SSH it runs
`run.sh` from the same checkout path on the host, which starts or reuses that
viewer with the host's default database, then opens or reuses an SSH tunnel from
a free `127.0.0.1` port here to the viewer's loopback port there and prints a
local link with the token. Both ends stay on loopback. `--remote [host] --restart`
restarts the remote viewer (new port and token) and its tunnel; `--remote [host]
--stop` closes the tunnel and stops the remote viewer. A live tunnel is reused
only while it still forwards to the viewer's current port. Tunnel state (an ssh
control socket) lives under `$XDG_RUNTIME_DIR/task-mcp/`. An unreachable host or
a host without the checkout and `.venv` is an error, with no local fallback, and
`TASK_MCP_DB` cannot be combined with `--remote`.

Open the private link printed by the command. The address bar then shows a
token-free location path such as `/w/1c4684b6/t/id/readable-task-ids` (workstream
by 8-character ID prefix and task by its public ID; see [docs/viewer.md](viewer.md#location-urls)),
so reload, back/forward and bookmarks keep your place in a tab that holds the
token. Repeated launches reuse the live
viewer for that database. The explicit `open_task_viewer` MCP tool returns the
same link for its configured database; it does not change client approvals or
open a browser automatically. The listener stays running after the CLI or MCP
session ends, until **Stop viewer**, `ui --stop`, or the process/machine stops.
There is no automatic startup or installed OS service. The current launcher
uses POSIX file locking (Linux/macOS).

Browse projects and workstreams, search/filter task titles, and read
specifications, evidence and audit history. Each project's **Unassigned** view,
below **All tasks**, lists its tasks in no workstream (what MCP tools call the
inbox), and All tasks marks those cards **Unassigned**. The viewer does not show
notes yet. Archived workstreams are hidden from navigation until **Show archived**.
Each project's **Task groups** page shows its discoverable groups, including shared groups and empty groups included in
that project's scope. **Shared task groups** shows only groups with task members in
more than one project. Both show whole-group progress and project counts;
**Project group**, **Shared group** and **Empty group** labels distinguish them.
Group details list workstreams that explicitly include the group, with
project/branch links to their task lists. Independently scoped member tasks
do not imply that the whole group is included in a workstream.
You can ask a question on an open task (including one sent back for rework),
add a task to or remove it from a workstream, drag to reorder a workstream, and
defer, resume or drop tasks (Drop asks for confirmation; reasons are optional
except when bringing back a dropped task). A task waiting for sign-off offers
**Sign off with an agent**, which copies a ready prompt to paste into your
agent: sign-off is a walkthrough with an agent, not a browser form. **+ Idea**
saves a thought before it is lost: one line plus an optional sentence, under a
short ID that is prefilled from the line and editable, becomes
an Unassigned task (in no workstream) held by an "Idea to process" item, shown
under Design in the project's **Unassigned** view until it is assigned, or
processed when you ask an agent to "go through my ideas" (the task-capture skill
turns each into a brief or task with you, splits it, or drops it). Task and
group details, and **Notes** for the open workstream and project, list the notes
that reference them; you can open, add, edit, archive and unarchive notes there,
choosing references with a picker. Apart from quick ideas and notes, the browser
does not create or edit tasks, answer questions, record reviews or sign off.
Concurrent changes keep what you entered and offer reconciliation.
The app uses the existing Store and database; it has no synchronized copy.
Task text is displayed as safe, whitespace-preserving text, including Markdown
source. Full task setup, result recording and uncommon workflow operations
remain available through MCP.

The listener binds only `127.0.0.1` on an ephemeral port. Its private launch link
is a bearer credential: keep it private. Host/Origin checks, a custom token
header, strict content policy and no external assets protect browser access.
The token is removed from the address bar and kept in per-tab session storage;
location URLs never contain it.
Reads still append Store audit events. Whole-server pre-approval now includes
the ability to explicitly launch this local listener and expose the configured
database to a token-holding browser; it is not a read-only viewer. Nothing here
changes Codex's approval settings.

For a disposable visual demo, run `.venv/bin/python examples/viewer_demo.py`.
See [viewer usage, security and lifecycle](viewer.md) for details.

## Performance and client approvals

Measure the server separately from the client that hosts it:

```sh
.venv/bin/python examples/benchmark.py --samples 20
```

The benchmark uses disposable databases and compares direct Store calls with
real subprocess stdio MCP calls. `--source /absolute/path/tasks.sqlite3` measures
SQLite-consistent, read-only-source backups; `--project ID --workstream ID`
select an existing scope in those copies. `--work-dir /existing/directory`
places the disposable copies on a chosen filesystem. Startup and per-call
samples are reported separately. It never benchmarks writes against the source
database or changes the client's approval policy.

Tool annotations describe actual behavior, not a performance preference.
Creation, additive membership/gates and new result attempts are non-destructive
writes. Overwrites, removals, lifecycle decisions and `init` (which can rebind an
existing workstream) retain destructive annotations. Audited reads are still
declared writes because they append events; the static skill catalog and runtime
diagnostic are read-only. Revision checks, workflow gates and audit history apply
regardless of the client's approval decisions.

In dogfooding, local mutations took milliseconds while the host's synchronous
approval review added seconds before execution. Accurate additive annotations
avoid incorrectly classifying creation as destruction; they do not guarantee
that a client will omit approval, nor eliminate approval for genuine edits.
See [the performance investigation](performance.md) for measurements and
the distinction between server validation and end-to-end verification. After
an annotation change, an existing client connection must refresh its discovered
tool descriptors; a fast fresh subprocess alone does not verify that refresh.

For diagnosis and optional, user-chosen per-tool or whole-server pre-approval,
see [troubleshooting slow client calls](performance.md#troubleshooting-slow-client-calls).
Those settings belong to the client, not the task database, and never replace
the workflow's required user decisions or human sign-off.

## Session init and workstreams

The stdio server automatically collects bounded private usage traces for offline
evaluation. See [usage traces](usage-traces.md) for `task-mcp trace-report`,
capture/retention settings and what the measurements can establish.

Call `init` with the absolute path of the target checkout and its branch, or an
explicit `workstream_name` for detached/non-Git work. The target is independent
of the MCP server process cwd and the agent session cwd. For example, a session
in a parent directory can call `init(path="/work/service-a", branch="main")`
and then `init(path="/work/service-b", branch="feature")`. Keep both returned
project/workstream IDs for subsequent task calls; there is no global current
project or persistent session entity. Repeating either call returns its own
`ready` context and workstream task list without changing the other.

An exact existing path/branch binding returns `ready` with project and
workstream identities, revision, binding and a compact list of the workstream's tasks (`queue`). No confirmation
is needed for ordinary resume. Passing a known `workstream_id` also checks that it
is bound to this checkout and branch; an unknown ID is an `unknown_workstream`
error.

Every other result has one shape, whatever the case:

- `state`: `mismatch` (the request does not match the recorded bindings: a
  `workstream_id` bound elsewhere or in another project, this branch bound at
  another checkout, or a `project` other than the one this checkout is attached
  to), `new_branch` (a known checkout on an unbound branch or name),
  `unregistered_checkout` (an unknown checkout) or `archived` (see
  [Archived workstreams](#archived-workstreams)).
- `message`: a fixed sentence for the state.
- `path`, `branch`, `workstream_name` and `project` (the project the request was
  checked in; `null` for an unknown checkout without `project`).
- `workstreams`: what exists, each with `id`, `project_id`, `name`, `branch`,
  `checkout_path`, `revision`, any `archive` block, and `roles`: `requested`
  (the `workstream_id` given), `bound` (bound to this branch or name in the
  project), `candidate` (one of the project's workstreams, at most ten, with
  `more_workstreams` and `archived_hidden`) or `name_holder` (it holds the name a
  new or moved workstream would take).
- `next`: the calls that work, each `{choice, tool, arguments}` with the exact
  arguments. `choice` is `resume` (resume that workstream here or at its own
  checkout and branch), `rebind` (move it here), `create` (`create_project`,
  `new_workstream` or `attach_workstream`), `check` (the same init within one
  project, which returns that project's calls) or `unarchive`
  (`archive_workstream` with `archived=false`; add a `reason`, then init again).

A call that would fail is left out: a rebind never crosses projects, and a new or
moved workstream whose name (`workstream_name` or the branch) is already used in
the project is not offered; its holder is listed as `name_holder` and the message
says another `workstream_name` avoids the conflict. A requested workstream is
always reported as itself and offered at its own binding, never swapped for the
one bound here. An unknown checkout naming a `workstream_id` is checked within that
workstream's project. Without `project`, an unknown checkout offers
`create_project` and a `check` of each listed project (`projects`, at most ten,
with `more_projects`), so names and branch bindings are checked before attaching
or moving anything. Candidates are recorded bindings, not scanned Git refs or
running agents. The initial call may append an audit event but changes no
project, task, scope or workstream state.

After choosing setup, call `init` again with `confirmed=true` and `action`:
`create_project` creates a project and its first workstream;
`new_workstream` creates a branch/name in the already attached checkout;
`attach_workstream` atomically attaches an unknown checkout to the specified
existing project and creates its workstream (these creating actions reject a
`workstream_id`, `workstream_id_not_used`, unless it already is this exact binding, and
`create_project` rejects a `project`, `project_not_used`); `rebind_workstream` moves a selected
durable workstream binding to this path/branch, preserving scope and history.
Rebind requires `workstream_id` and its last read `expected_revision`. Failed
setup calls roll back attachment and workstream changes together. An exact
binding reached by retry returns `ready` without duplicating state. New
workstreams take an explicit `scope_expression` (default `none`); no project
relationship or scope is inferred from directory/branch names. `list_projects`
is a global administrative catalog, not a current-project selector.
When a new workstream snapshots an existing workstream as its first scope
expression term, pass that source's last read revision as `expected_revision`.

`list_workstreams(project?, limit?, offset?, include_archived?)` lists registered workstreams
globally or within one project, with binding, revision and derived scoped task
counts; archived ones are only counted (`archived_hidden`) unless
`include_archived=true`. `workstream_status(workstream_id, limit?, offset?, include_inactive?)` returns that
workstream's active slim cards (see [Boards and cards](#boards-and-cards)) and count
diagnostics without init or rebind; its `concern_tasks` page follows the same
active-only default. Init returns at most ten active cards of the workstream's tasks with
total/next-page information and `queue_hidden` counts. Task/group
lists and status task lists default to twenty; events default to twenty metadata entries.
Workstream scope/group references are bounded; `workstream_status(include_scope=true)`
pages explicit members, group references and exclusions using limit/offset.
Its `counts` are disjoint task views (ready, unresolved,
prerequisites, review, sign-off, done, deferred, dropped). They count only
concrete tasks owned by that workstream's project; `scoped_count` equals their
sum. `groups` and `referenced_groups` identify explicitly included groups and
their whole-group progress separately, even when this project has no members.
The separate
`overlapping_gate_diagnostics` counts active gates on open/rework tasks and can overlap
for tasks with several gates. Card `gate_diagnostics` (include group `blockers`)
uses the same active-gate meaning: done, dropped and deferred tasks have an empty
list. Deferred tasks' gates become active again when resumed. Explicit `get_tasks(specification=true)` retains full requirements/gates;
`get_attempt`, paged `list_task_attempts` and audit events retain proof/history; retained
history does not create actionable gates on inactive tasks.
The single card `state` shown in `list_tasks` and status drill-down prioritizes
terminal disposition, then inbox, review/sign-off and unresolved/
prerequisite gates; use `include=["blockers"]` diagnostics to see every simultaneous gate.
`recorded_state=registered` means only that the binding exists;
`agent_liveness=not_tracked` means the service intentionally does not observe
agents, run heartbeats or maintain leases; it is not a failed observation. Recorded
task views and active gates describe durable workflow state, not running agents. IDs support
durable resume; offset pages are for browsing current state, not a snapshot of a
changing board. Completed tasks in different repositories remain separate;
these views do not claim an integrated feature is complete.

Continue on the same durable workstream ID. Before relying on recorded proof,
check the actual checkout, affected files and commits against the current full
specification, especially after rebinding. Another workstream's implementation
does not gate local alternatives. Deliberate merge/cherry-pick integration of an
open task uses a new target-local `record_result` with origin attempt/workstream,
source/target commits and target verification, followed by fresh independent
review. Completed proof stays immutable; later integration needs a new task.
See [continuation and integration guidance](continuation.md).

Workstreams contain ordered lists of shared tasks. A task can be included in
several workstreams concurrently. `add_to_workstream` with a group ID includes
that group, so its present and future local members enter unless excluded;
`remove_from_workstream` with a group ID removes the inclusion, and with a member
ID excludes just that member while its group stays included. Completed groups can
still be included or removed; that scope-only change leaves the group's revision
unchanged. A new workstream's
scope expression copies another workstream's references and exclusions. No scope change transfers tasks
from another workstream. Remote members stay outside the local task list.

## Notes

A note is a separate record with a title, text and references to the tasks,
groups, workstreams and projects it concerns. The title (1 to 120 characters)
says what the note is about, so an agent can judge relevance without reading the
text (1 to 4,000 characters). Rules for a repository belong in committed files
such as AGENTS.md, personal rules for every repository in user-level files, and
Task MCP workflow rules in the skills.

```text
create_note(title="Staging deploy and rollback",
            text="Staging runs build 41; roll back with deploy.sh 40.",
            references=[{"kind": "workstream", "id": workstream_id},
                        {"kind": "task", "id": "deploy-pipeline"},
                        {"kind": "project", "id": other_project_id}])
update_note(note_id, expected_revision=1, text="Rolled back to build 40.")
update_note(note_id, expected_revision=2, archived=true)   # hide; false restores
```

A reference is `{kind, id}` with kind `task`, `group`, `workstream` or `project`
and the entity's complete public ID (a project's `prj_…` ID, not its path or
name). Give at least one; any number and any mix of kinds and projects is fine,
and the note stays one record. Repeated references are saved once. Every
referenced entity must exist with that kind: an unknown ID fails with
`unknown_reference`, a wrong kind with `reference_kind_mismatch`, and either
saves nothing. Text never creates references, even when it mentions an ID.

`update_note` changes any of `title`, `text`, `references` and `archived` at the
note's `revision`; a stale one fails with `revision_conflict` (re-read with
`get_notes` and reconcile). Omitted fields stay. Supplied `references` replace
the whole set in the same atomic write. An edit keeps `created_at` and updates
`updated_at`, `updated_by` and `revision`; values equal to the current ones are a
no-op. Over-long text fails with `note_too_long`, stating the limit and length.
Every write, failed or not, is audited as `note.created` or `note.updated` with
the before/after note; `list_events(note_id=...)` returns a note's history.
Archiving hides a note from task reads and `list_notes`; there is no deletion.

Notes are never sent automatically: `init` returns no notes, titles or counts.
Find them by title:

- `get_tasks(specification=true)` and `get_next_action` list the active notes
  that reference the task or group as `notes` (`id`, `title`, `updated_at`),
  newest-updated first, at most 20, with `note_total`.
- `list_notes(reference={"kind": ..., "id": ...})` pages the same titles for any
  task, group, workstream or project (`limit`, `offset`, `next_offset`, `total`).
  Archived notes are counted in `archived_hidden` unless `include_archived=true`,
  which lists them with `archived: true`.
- `get_notes(ids)` reads 1 to 20 complete notes: title, text, references,
  `created_at`/`updated_at`, `created_by`/`updated_by`, `revision` and
  `archived`.

In the local viewer (docs/viewer.md), task and group details and the **Notes**
panel of a workstream and its project list the same titles; a note opens with
its full text, references and times, and the viewer adds, edits, archives and
unarchives notes at their revision, with references chosen in a picker.

Notes live in the `note_records` and `note_references` tables of database
schema 12, which replaces the earlier one-note-per-project/workstream slot
(`set_note` and the notes in `init`). On first start, new code takes a verified
`*.pre-schema-12.*.sqlite3` online backup, then migrates each nonempty project or
workstream note to a note titled "Project note" or "Workstream note" that
references its owner and keeps its text, time and author (`note.migrated` in the
history). The old `notes` table stays as frozen private history. A schema-11
server already running keeps working, including its `set_note`, until it stops,
but cannot reopen schema 12, and its later note writes are not carried over.
Restart every MCP server and the viewer on the new code. Rollback to schema 11
requires restoring the backup; writes made after it are not included.

Earlier schemas follow the same rule. Schema 11 migrated task identity with a
verified `*.pre-schema-11.*.sqlite3` backup: a schema-10 server already running
could continue, but cannot restart on schema 11. At the current schema, a
database missing an additive table (`workstream_archive`, `task_picks`) gets a
verified backup named after the missing tables, such as
`*.pre-workstream_archive.*.sqlite3`, before it is created.

## Archived workstreams

A workstream bound to a stale checkout, such as an archived snapshot folder,
misleads new sessions that discover it by path, branch or name. Archive it:

```text
archive_workstream(workstream_id, expected_revision=0, reason="Inactive snapshot")
archive_workstream(workstream_id, expected_revision=1, reason="Back in use",
                   archived=false)   # unarchive
```

`expected_revision` is the workstream's `archive.revision` (0 when it shows no
`archive` block); a stale one fails with `revision_conflict`. The reason is
required, at most 200 characters. Repeating the current state is a no-op that
keeps the earlier reason. Every call is audited as `workstream.archived` or
`workstream.unarchived` with the before/after archive state. Only discovery
changes: tasks, memberships (including the same tasks in other workstreams),
order, attempts, reviews, history, notes and the workstream's own revision are
untouched, and the binding keeps its branch and name.

Once archived, a workstream is left out unless a call passes
`include_archived=true`:

- `list_workstreams` omits it and counts it in `archived_hidden`.
- `init` leaves it out of `new_branch` and `unregistered_checkout` candidates
  (`archived_hidden` counts them). At its own checkout and branch, with or
  without its `workstream_id`, `init` returns `state=archived` instead of
  resuming, with the workstream and its `archive` block, a `resume` call with
  `include_archived=true` (the ready result keeps its `archive` block and says it
  is archived) and an `unarchive` call (`archive_workstream`; add a `reason`, then
  init again).
- `init action=rebind_workstream` refuses to move it (`workstream_archived`).
- `workstream_status` returns its header and counts with
  `listing_skipped="archived"` but no task cards, concern list or scope pages.
- `get_next_action` selects nothing (`diagnostics.workstream_archived`).

Init lists an archived workstream with its `archive` block, and each `next` call
that resumes or moves one includes `include_archived=true`, so it works as given. An archived workstream still holds its branch and name in the
project, so a new workstream cannot take them; rebind or unarchive it instead.
Its `archive` block (`archived`, `reason`, `revision`, `updated_at`,
`updated_by`) appears wherever the workstream does once it was ever archived.
The viewer hides archived workstreams from navigation until **Show archived**.

Archive state lives in its own `workstream_archive` table. Its original
schema10-only rollout was additive; later schema migrations follow the
migration and restart requirements described under [Notes](#notes). An existing
database lacking only this table gets a verified
`*.pre-workstream_archive.*.sqlite3` backup first.

## Picked tasks

When `get_next_action` hands out an implement or review action it also records a
picked marker for that task in that workstream (task, workstream, action, time),
at no extra call. A new pick of the same task there replaces the marker. The
marker is information only: `get_next_action` does not skip or lock picked tasks,
and nothing requires an agent to act on it. It counts for 4 hours, and stops
counting as soon as a result or review is recorded for that task in that
workstream after the pick. While it counts, full `get_tasks` reads show it as
`picks` (`workstream_id`, `action`, `picked_at`), the `attempt` include group of
`list_tasks`/`get_tasks` shows it as `picked`, and the viewer lists the task
under **In progress** with "An agent picked this up 12 minutes ago". Afterwards
an unfinished task is **Open** again. Side effect: a coordinator that only asks
for the next action, without working on it, marks that task picked for up to 4
hours.

Markers live in their own `task_picks` table. Older already-running servers
ignore it and keep selecting and recording; a result they record still ends the
marker. Restart compatibility follows the schema requirements under [Notes](#notes).
An existing database lacking only this table gets a verified
`*.pre-task_picks.*.sqlite3` backup first; expired markers are pruned when the
next action is picked.

## Task identity

New tasks and groups have permanent, short public IDs, for example
`readable-task-ids`. Pass `public_id` to `create_task` (including `kind=group`), or in each
`decompose_task` member. These IDs are accepted anywhere a task/group reference
is needed, including prerequisites, scope expressions, reads, edits and sign-off.
Use up to 40 lowercase ASCII letters, digits and single hyphens, beginning with a
letter; a longer ID is refused with `invalid_public_id`, which states the limit and
the submitted length, and nothing is saved. IDs are globally unique across projects
and groups, and remain reserved for done/dropped tasks. An explicit collision returns
`public_id_conflict`: choose a more specific name and retry. Creation is atomic,
including concurrent collisions and decomposition. IDs never change with
titles/specifications, and IDs created before the 40-character limit (up to 96
characters) stay valid.

An omitted `public_id` becomes a short slug of the title (`Store.short_task_slug`):
the title is folded to lowercase ASCII, apostrophes are removed and everything else
that is not a letter or digit separates words. Filler words (articles, most
prepositions and conjunctions, pronouns and auxiliary verbs such as *the*, *of*,
*on*, *each*, *instead*, *is*, *my*) are dropped unless every word is one. The slug
keeps at most five words, in order, while it stays within 30 characters; the first
word is always kept and cut at 30 if it alone is longer. A slug starting with a digit
gets a leading `task` word, and an empty one is `task`. Duplicates get `-2`, `-3`, …,
never exceeding 40 characters. For example, "Count each workstream's Needs input on
the server instead of re-reading every card list" becomes
`count-workstreams-needs-input`. Browser ideas prefill their **ID** field with this
rule, asked from the server, and use it when the field is left as prefilled.

Existing tasks retain their exact `tsk_…` IDs and all existing references/history.
Private UUIDs are stored separately and never appear in tools, exports or the
viewer. Cards and detail headers display the complete public ID; details offer
**Copy ID**, and task search includes IDs. Project/workstream/attempt IDs retain
their existing format.
New task/group locations put the complete public ID after `/id/`, keeping them
distinct from existing shortened legacy URLs even for hexadecimal-only names.

## Task lifecycle

Origin and `user_request` are descriptive metadata. Create with a workstream for
direct membership there; without one, existing group inclusion may still apply.
Adoption is derived from at least one effective membership. Adding another
workstream retains all earlier memberships; removing names one workstream and
retains the rest. Edits preserve scope. Genuine requirements advance the spec
revision and leave old proof historical; summary corrections/no-op edits retain
spec revision. Workstream membership, active disposition, clear questions and
prerequisites and this workstream's current-spec attempts govern readiness.

`get_tasks(ids=[...], workstream_id?, include?)` returns the same slim cards as
boards (see [Boards and cards](#boards-and-cards)). Cards contain no body preview,
acceptance criteria or replacement token. With `workstream_id`, state and the
`attempt` include group use that workstream's current-spec local attempts; without
it they reflect current results in any workstream and imply no branch readiness.
Call `get_tasks(ids=[...], specification=true, workstream_id=..., attempt_ids=[...])`
when you need complete current requirements and exactly chosen proof together.
No preliminary card read is needed. A group member's `parent_group` is a
reference: the group's `id`, `title`, `summary` and `summary_stale`. Specification
reads, `get_next_action` and the viewer's details never embed the group's body or
acceptance criteria. When a task needs them, read the group by ID: add it to a
member's specification read, `get_tasks(ids=[task_id, group_id], specification=true)`,
or read it alone.
Current-spec attempt summaries are limited to three, actionable first, with totals
and `has_more`. Page additional attempts with `list_task_attempts(states=[...],
current_spec_only=true)`; `get_attempt(attempt_id)` retrieves one complete proof.
Explicit proof retains its original task, workstream and specification provenance.

### Boards and cards

`list_tasks`, `workstream_status` and the task list `init` returns show active work only: open
and rework tasks, including those awaiting review or sign-off. Done, deferred and
dropped tasks are omitted and counted per status in `hidden` (`queue_hidden` on
init), for example `{"done": 217, "deferred": 150}`. Pass `include_inactive=true`
to list them too (history or backlog); `list_tasks(state="done"|"deferred"|"dropped")`
lists exactly that status. `hidden` is omitted whenever `include_inactive=true` or
any `state` filter is set: a filtered page counts only its own matches. Positions
keep the whole workstream order, so hidden tasks leave gaps rather than
renumbering the board.

A slim card carries `id`, `title`, `summary`, `state`, `revision`, `position` (in the
named workstream's order), `blockers` (unsatisfied prerequisite IDs), `question_count`,
`concern_count` and `rejected` (a review or sign-off rejection awaits a newer
recorded result). Empty, zero and false fields are omitted, as is `position`
without a workstream. Closed cards keep `question_count` (a deferred task keeps
its open questions) but have no `blockers`; the `blockers` group still lists
their prerequisites. `state` is one word: `ready`, `rework`, `blocked`,
`question`, `review`, `signoff`, `inbox`, `out_of_scope`, or the closed status
`done`/`deferred`/`dropped`. Groups have `state="group"` with `progress`,
`complete` and `project_count`. The `state` filter accepts these words and the
older view names `prerequisites` and `unresolved_items`; `ready` also matches
`rework`. Any other value, such as `sign-off`, is rejected with `invalid_state`
and the list of accepted values.

`list_tasks` and `get_tasks` accept `include=[...]` groups that restore what slim
cards leave out, without a full specification read:

| Group | Adds |
| --- | --- |
| `blockers` | `prerequisites` (every link with title, state, milestone, satisfied/blocking), `gate_diagnostics` |
| `attempt` | `attempt` (current result: state/review status, implementer, summary, revision), `attempt_counts`, `alternative_attempt_count`, `attempt_scope`, `selected_attempt_id`, `latest_rejection` (without reasons) |
| `concerns` | `concerns` (text, kind, source, author and attempt provenance for up to three concerned attempts), `concern_count`, `concern_attempt_total`, `concern_attempt_references`, `concern_attempts_has_more` |
| `workstreams` | `workstreams` (every membership with its position), `adopted`, `in_scope` |
| `ids` | `project_id`, `object_type`, `status`, `spec_revision`, `summary_spec_revision`, `summary_stale`, `order_key`, `parent_group_id`, `specification_complete`, `view`, `workstream_id`, and `origin_project_id` for groups |

Unknown group names are rejected. With `specification=true` the full specification
is returned as before; include groups only add fields it lacks (for example
`workstreams` positions). The browser viewer reads unabridged cards, including
closed tasks, through `Store`.

Body and criteria are whole-field replacements. Use the `specification_etag` from
that full read or from a create acknowledgement for the complete specification you
authored. A valid token-bearing update returns its refreshed token. Unchanged specs
retain the same token. Other updates expose no token. Continue with the last returned
entity revision, even after a user pause; successful writes need no confirming read.
Fetch and reconcile only on conflicts, uncertainty or missing information. The token
is a stale-read/data-loss guard. Title and summary-only edits need no full-spec read.

Task/group `summary` is optional intent or settled constraints, at most 240 Unicode
characters on one line. Overlong, multiline and whitespace-only values fail; no
truncation or generated fallback exists. Omission preserves it; null clears it.
Summary-only edits advance the entity revision while retaining spec revision,
workstream membership and proof, including completed tasks/groups. A later spec edit makes
`summary_stale=true`; simultaneous summary edits stamp the resulting spec revision.
Reaffirming the same stale text refreshes it; current identical text is a no-op.
Summary freshness does not certify correctness; full requirements remain authority.

Resolving an unresolved item,
changing order, or adding evidence does not change workstream membership.
Unresolved items are live gates; resolve them after settling the matter and
record any material decision in the task description. A `blocked_by` prerequisite
clears by default once the blocker is done or has a current-spec passed
independent review (`passed`) or recorded human review (`human_review`). A task
may depend on a group; every member of a nonempty group must satisfy the
link's required milestone.

Use `decompose_task` to turn a task into a first-class group while retaining its
ID, description and audit history. The call atomically
creates required members in the parent's workstreams, or inbox members from an inbox parent. Groups cannot nest, have no implementation
attempts or execution gates, and derive completion from all members being
complete. An empty group is incomplete and mutable. `create_task(kind="group",
workstream_id=..., title=...)` creates the same kind of group directly, without
selecting a home project; it is included in that workstream's explicit group scope.
The group has one global ID and can hold concrete tasks from several projects.
`list_tasks(state="group")` discovers groups globally, through any member/scoped
project, or in one workstream's scope; `get_tasks(specification=true)` on its ID
shows whole-group progress and at most three member references;
`list_tasks(group_id=...)` pages every member's card (optionally within a project
or workstream). All public group details
show `project_id=null`; `origin_project_id` is optional legacy provenance for
groups created before this storage change, not an ownership boundary.

Pass `group_id` and the group's last read `group_expected_revision` to
`create_task` to create a new member in its own project, or pass them to
`update_task` with the existing task's revision to attach it. Membership
changes are atomic. Existing members keep their independent specifications,
implementation, review and sign-off. A group becomes complete only when it has
at least one member and every member is signed off; pending, deferred or dropped
members keep it incomplete. Completed groups cannot gain members or be edited.
A concrete task may depend on a canonical task or group in any project, without
shared-group membership. `add_prerequisite` defaults
to `milestone="review"`, satisfied by done or any current-spec passed/human-reviewed
attempt, including another workstream. Use `milestone="signoff"` only as a rare
exception when proceeding before the user's verdict would very likely waste work;
it waits for done (every member for a group). Dropped/deferred work remains
unsatisfied, even with a retained passed review. Satisfaction is computed: rework
or a spec change with no current-spec satisfying attempt blocks review links
again; existing dependent results are retained. A canonical milestone does not
prove that another branch's code was integrated. Links never expand workstream
scope or transfer attempts, reviews or code. Self-edges and cycles through prerequisites
and implicit group-to-member completion edges fail atomically, including during
membership changes. Linking changes the
dependent task revision, leaving both specifications and workstream membership intact.
Use `remove_prerequisite(task_id, expected_revision, blocked_by_id, note)` for a
mistaken or obsolete link, with the dependent task's last revision and the actual
decision note. It removes either milestone link atomically, immediately recalculates
the gate, and audits the configured actor, note and removed link. A real deletion
advances the dependent revision once; an absent link returns `changed=false`
without advancing it. Stale revisions still fail, and completed tasks are immutable.
Removal preserves specifications, workstream membership, attempts, reviews and other links.
Resolve unresolved items before decomposition;
existing prerequisites move to the concrete members. A session actively handling
a task may add an unresolved item or prerequisite; a new prerequisite is created
with `create_task` and linked with `add_prerequisite`. `handling` accepts
`active` or `user`. The service records the asserted handling role but does not
authenticate agent identity.

Full task reads, workstream task lists and project lists include compact `prerequisites`
references with ID, title, project ID/name, canonical `state`/`complete`, required
`milestone`, computed `satisfied` and `blocking` facts. `complete` still means
human-signed-off completion; `satisfied` can become true earlier. Workstream
diagnostics include the same link facts. Duplicate links with a different
milestone fail rather than silently replacing it. A global group's project
identity is null and its state is complete/incomplete. These references do not
expand remote specifications,
attempts, evidence, history or workstream task lists; use explicit task reads to inspect them.
The viewer renders these references directly and opens remote details only on
deliberate navigation. Verify actual IDs and meaning before replacing a known
prose gate: add the real links first, then resolve the old unresolved item.
There is no automatic prose parsing. Review milestone satisfaction does not
expand execution scope or inherit review proof into a local attempt.

Nonblocking observer gate proposals are retired. Proposal rows written by an
older server stay in the database, readable on full task reads and in exports,
and no longer block decomposition.

Reading or selecting a task does not create an attempt. The implementer calls
`record_result` only on leaving or recovering an actual durable result. Read the
full current spec, check the actual checkout/artifacts, and send the last-read
`expected_revision` and `specification_etag`, actual `implementer`, `summary`,
context `evidence`, concrete `artifacts=[{kind: "commit"|"artifact", reference: ...}]`
and `verification` describing actual checks/outcomes and limits. It records a local
unreviewed attempt even when workstream membership, unresolved or prerequisite gates remain,
or the task is deferred/dropped. This factual record never grants approval,
resumes work, clears a gate or satisfies prerequisites. Groups and completed tasks
are protected. The concise ACK includes attempt `id`/`revision`, `task_revision`,
unchanged `workstream_ids`/`status` and gate diagnostics; retrieve full proof deliberately.
The server stores declarations; callers verify artifacts and reviewer provenance.
New proof uses a structured envelope in the existing evidence TEXT column, with
legacy text proof unchanged and no schema migration. Dispatch review actions to a
fresh independent reviewer and implementation actions to an implementer. Other
workstreams can produce independent attempts
on the same task. An independent reviewer named differently from the implementer
returns a verdict; the coordinator records it with `record_review`. Only the
user's explicit approval can skip independent review: `signoff_task` approve then
accepts a current-spec result still awaiting review, its reasons must say so, and
the attempt records that human review. Agents must not self-issue it.
Implementers follow the specification, record worth-doing or approach concerns
and carry on, or save an unexpected blocker and move on. Independent review
checks the work against the specification and judges how well it is built:
anything an implementer could fix without changing what the task says is
rework; doubts about the task itself are concerns. When following the
specification faithfully causes a real problem, the review passes with a
serious concern, and optional extra features are new ideas. Use the task's
`latest_rejection` reasons when implementing/reviewing.

"Sign off X" requests asked/built/verified presentation followed by the user's
verdict; explicit "approve X" is valid without a walkthrough. Address every
recorded concern and answer "Worth doing?", "Right approach?" and "Built well?"
in that order, each independently, never using an earlier judgment as a
premise. Recommend approval only when all three hold. Generic rejection with
reasons needs a confirmed mapping: "Built well?" failures are rework, a
different approach is revise, and work not worth doing (or obsolete) is drop.
Only the user approves. Before recording an approval, show any still-open
prerequisite and ask whether it affects the verdict.
One `signoff_task` decision selects the exact reviewed attempt.
`approve` completes it; `rework` requests repair and fresh review; `revise` returns
to design with the reasons as an open question; `drop` closes without approval.
When dropped delivered code must be removed, add a removal task to the current
workstream in the same step.
The single `reasons` field is required for rework/revise and optional for
approve/drop. Workstream membership and factual history survive every verdict.
Deferral is an ordinary status change. The service stores the actual verdict and
reasons, without separate agent or quality judgments.
Completed tasks are immutable; changed requirements
become new tasks. The service records assertions and cannot authenticate
reviewer independence or the actual human verdict. Workflows must obtain and
record them truthfully.

Implementers and independent reviewers may supply `concerns=[{kind: "value"|"design", text: ...}]`
to `record_result` and `record_review`. A concern is a worth-doing (`value`) or
approach (`design`) doubt that cannot be fixed without changing what the task says. Concerns never change gates,
review verdicts or prerequisite satisfaction. Omission preserves all existing
contributions; reviewer additions retain the implementer's concerns and label
both sources/authors. The write ACK returns a count, without concern prose.
`get_attempt` and chosen attempt proof show complete concerns. Full specification
reads show concern prose from the same three applicable attempt window as their
attempt summaries, with totals and `concerns_has_more`; proof included in the same
response carries its own concerns once. Older concerns remain available through
explicit attempt/history reads. `get_next_action` includes the chosen local
review proof and its implementer concerns in one call. `workstream_status` exposes
`concern_tasks`, a separate page at its `limit`/`offset`, prioritizing tasks awaiting
sign-off. These references/counts include only current-spec attempts on that
workstream. The viewer shows full concerns on results; exports retain them in
labelled attempt history.

The ordinary path is:

```text
init → proposal-review → superdevloop → task-signoff
```

For a feature whose design is still open, use the two-phase path:

```text
init → task-capture → task-design → implementation → review → task-signoff
```

An ordinary **"add a task to do X"** request can authorize a concrete specification
without another confirmation. Create it with the intended `workstream_id` to add
it there, or call `add_to_workstream` for an existing task. Explicit placement
instructions take precedence. Agent-suggested additions go to the inbox after
user confirmation. User-requested design briefs belong in the current workstream
with active design gates; membership starts no implementation.

Say **"add a design task for X"** or ask to save an exploratory idea to use
`task-capture`. The agent does bounded preliminary research and saves a
specification in the layout the MCP server instructions set, with its material
open questions and a short public ID of a few words. You do not need to answer all
of those questions during capture. Create the brief in the inbox, add a
`Feature design required (task-design): ...` gate, then add user-requested work
to the current workstream. The gate keeps it from implementation selection.
Briefs saved before the rename say `(feature-design)`; task-design treats them
the same. Say **"go through my ideas"** to process ideas saved with **+ Idea**:
`task-capture` takes them one at a time with you, never unattended.

Say **"let's design X"**, **"review design tasks"**, or **"designrev"** to use
`task-design`. First decide whether the work is worth doing, then research
current behavior, compare approaches and work through decisions. Save the
resulting specification and acceptance
criteria, preserving unsettled questions. Larger features can become a group of
concrete implementation tasks. Add user-requested results to the workstream
where the user is working, after adding member gates. Keep unsettled questions
blocking and respect explicit placement instructions. Membership and design
discussion start no implementation; membership changes need no separate scope
step.

"Design task" is conversational shorthand for this workflow, not a stored task
type. The design-gate prefix is a readable skill convention, not parsed server
metadata. Inbox briefs display as `inbox` even with a design
gate; the design skill reads their details as well as tasks in `unresolved_items`.
Ordinary proposals and unrelated blockers still use `proposal-review`.

The canonical reference skill files are packaged under
`src/task_mcp/reference_skills/`. `get_default_skills()` returns a names/versions/hashes/descriptions index;
`get_default_skills(name="task-design")` returns that complete skill in one call.
The skills are `init`, `task-capture`, `task-design`, `proposal-review`,
`superdevloop` and `task-signoff`. MCP startup instructions name them and each
description routes request phrases to it, so a fresh connected session can fetch
and follow one without prior chat history or installing client skills. Tool
descriptions hold tool mechanics; skills hold workflow and judgment. Existing
connections may need to reconnect to receive changed server instructions.
Agents may also use the primitives directly. Copying
them to a client's native skill location requires explicit user authorization;
the server does not install or update clients. Single-agent devloop and guided
task flows are possible using the same primitives, but reference adapters for
those rare workflows are deferred.

## MCP tools

| Area | Tools |
| --- | --- |
| Project and workstream | `init` (actions `create_project`, `new_workstream`, `attach_workstream`, `rebind_workstream`; checkout/branch match check; `runtime` identity), `archive_workstream`, `list_projects`, `list_workstreams`, `workstream_status` (archived workstreams hidden unless `include_archived`) |
| Notes | `create_note`, `update_note`, `get_notes`, `list_notes`; task reads list note titles |
| Scope and order | `add_to_workstream`, `remove_from_workstream` (task or group IDs), `list_tasks`, `get_tasks`, `list_task_attempts`, `get_attempt`, `reorder_tasks`, `get_next_action` |
| Groups | `create_task(kind="group")`, `update_task(group_id=...)`, `list_tasks(state="group")`, `list_tasks(group_id=...)`, `decompose_task` |
| Specification and gates | `create_task`, `update_task`, `set_disposition`, `add_unresolved`, `resolve_unresolved`, `add_prerequisite`, `remove_prerequisite`, `decompose_task` |
| Delivery | `record_result`, `record_review`, `signoff_task` |
| Inspection | `list_events`, `get_default_skills` (exports are a CLI surface) |
| Local browser | `open_task_viewer` (explicit loopback listener/editor launch) |

Mutations that change a task or workstream use the last returned entity revision, checked atomically; user pauses do not invalidate it.
Workstream board/action/init/status responses expose `workstream_order_revision`.
Reorder one named workstream's list with one call:

```text
reorder_tasks(workstream_id, task_ids=[third_id, first_id],
              expected_order_revision=board.workstream_order_revision)
```

The supplied IDs become the exact prefix. Every unlisted effective member follows
in its previous relative order. This changes no memberships, canonical task/spec
revisions, selected completion, proof or other workstream lists. Completed tasks
can be reordered. Duplicate IDs, IDs outside the named workstream (including
groups), invalid lists and stale revisions fail atomically. An empty list or an
already-matching prefix is a no-op and preserves the revision and stored keys.
A changed order advances the target order revision once. The compact ACK returns
project/workstream identity, `workstream_order_revision`, `changed`,
`supplied_count` and `total`, without echoing tasks or the list.

Newly included members append in deterministic project baseline order; retained
members keep their positions. Removal affects only that list; re-adding appends.
Live group inclusions and exclusions retain their existing scope semantics.
Initial schema 10 migration preserves each effective list's prior project order
at revision 0. Scoped boards, next-action selection, status and exports use local
order; scoped cards include `position`. Project-wide lists report
`ordering=project_baseline` and remain deterministic without a reorder surface.
In the viewer, drag a task onto the upper or lower half of another task in a
named workstream's list to place it before or after that task. The drop line and
hint state the resulting position; the task stays in its status section. One
`reorder_tasks` call sends the prefix through the moved task with the loaded list
revision, so hidden, filtered and collapsed members keep their relative order.
Cancelled, self and unchanged drops send nothing. A failed save or stale revision
shows an error and reloads the recorded order; nothing is overwritten. All tasks
and group views have no list order to edit.
Reads never reorder. Actual requests and configured audit actors retain existing
attribution; assess usage from existing events after rollout, without automatic
reshuffling or routine reporting.

For a synthetic A/B browser preview with four shared tasks and independent
orders, run `python examples/workstream_order_demo.py`. It creates a fresh
disposable database and prints its private viewer link and stop command. A opens
with Gamma first and its tasks draggable; B keeps Alpha/Beta/Gamma/Delta.
Use the sidebar and named membership actions to inspect the resulting behavior.

Concurrent writes to one revision permit one winner and return
`revision_conflict` to the other. Task creation is not deduplicated: inspect the
board before retrying an uncertain response. Every handler call except the
static skill catalog appends a local audit event, including
reads and domain errors. Task mutations retain before/after snapshots. The configured actor label
is not authenticated. `list_events` supports a stable pagination ceiling.

## Text exports

Exports are a CLI surface, not an MCP tool. `task-mcp --export-workstream
WORKSTREAM_ID` prints a human-readable Markdown snapshot (`task-mcp/v5`); the
underlying Store export also returns its SHA-256 hash.
It starts with project/checkout identity and an ordered workflow overview, then
shows specifications, acceptance criteria, questions, prerequisites, evidence
and review history in text. Workflow labels match `list_tasks` for that
workstream: "Awaiting sign-off" is distinct from the stored "open" disposition.
Shared-group progress is global; detailed task entries stay in the local scope.

The output is deterministic for unchanged state and is export-only: editing it
does not update the service.

```sh
.venv/bin/task-mcp --export-workstream wst_your_workstream_id
.venv/bin/task-mcp --export-workstream wst_your_workstream_id --exclude-closed
.venv/bin/task-mcp --export-workstream wst_your_workstream_id --export-format legacy
```

`--exclude-closed` omits done and dropped tasks, not deferred tasks. The previous
embedded-JSON layout remains available as `--export-format legacy`
(`task-mcp/v1`); consumers of that
layout should select it explicitly. Export itself never creates a file.
See [the format details](exports.md) and a
[synthetic example report](export-example.md).

Creation, amendments and named membership changes return compact IDs, task/spec
revisions, `workstream_ids`, derived `adopted`, disposition and gate diagnostics.
`add_to_workstream(task_id, workstream_id, expected_revision)` retains all other
memberships. `remove_from_workstream` requires the affected workstream and removes
only that membership, including group inheritance via a local exclusion. Inbox
means zero effective memberships. Edits preserve memberships; actual requirement
changes advance `spec_revision` and leave old proof historical. Whole-field edits
require the full `specification_etag`. Failed concurrency/audit writes roll back;
no-op additions/removals preserve revisions. A real direct change advances the
task and named workstream revisions once.

A new workstream's `scope_expression` retains original internals: `none`, a
workstream expression base, and +/-task/group references with exclusions. Groups expand live to future local
members. A base copies references/exclusions without removing tasks elsewhere.
Decomposition includes the parent group in every direct parent scope so all new
members enter those workstreams. Bulk scope changes advance the named workstream
revision and leave specifications/proof unchanged. Each workstream now keeps its independent local order (protocol 14/schema 10).

Protocol 13/schema 9 replace the rejected exclusive membership tools with `add_to_workstream` and
`remove_from_workstream` (42 tools total). Separate acceptance operations/notes,
basis and spec-acceptance tracking are absent from current payloads. Adoption is
derived from effective membership. Autonomous eligibility requires membership
in the particular workstream, active disposition and clear questions/prerequisites;
its unfinished implementation/review uses only its own current-spec attempts.

Migration preserves original live scope tables and recovers missing references
from the rejected schema 6–8 candidate archives; candidate additions remain
nonexclusive. It drops `queue_members`, keeps private historical authority/archive
facts, and archives original state alongside any pending-intent question.
Scoped mutable legacy tasks lacking current-spec intent retain membership and get
an ordinary unresolved migration question. Done/dropped rows stay historical;
resuming deferred pending work still requires resolving its question. Normal
resolution, deferral and removal remain available. Every specification, completed
selection, proof/concerns/review and old audit byte survives.

Before upgrading, the service takes and verifies a private SQLite online backup
under its writer lock, including WAL commits. The adjacent
`*.pre-schema-9.*.sqlite3` backup survives success or transactional rollback. Stop
all writers before restoring with SQLite's backup API; reconnect matching clients.
Rehearse on disposable copies before deployment. The coordinated main rollout
is complete; reconnect existing MCP clients to load the current tool catalog
and packaged workflow guidance.

`signoff_task` accepts exactly `approve`, `rework`, `revise` and `drop`. Send the
actual user's `reasons`, exact reviewed `attempt_id`, and last returned task and
attempt revisions. Reasons are required for rework/revise, optional for
approve/drop. Current-spec passed/human-reviewed proof is required, except that
an explicit user approve may accept a current-spec result still awaiting
independent review when its reasons say so (the decision records
`independent_review=false`). Approve refuses while an unresolved question remains
(`task_not_ready_for_signoff`), but not for an unsatisfied prerequisite: a reviewed
result stays awaiting sign-off, and the user judges whether the open blocker affects
the verdict. The approval's decision and response list the prerequisites still open
(`open_prerequisites`: ID, title, milestone, state; empty when none), and exports
show them in sign-off history. Dependents of the approved task follow the ordinary
prerequisite rules. Rework returns the attempt to
implementation and requires fresh review. Revise copies the reasons into an open
question without inventing a specification revision. Drop closes without approval;
creating a removal task when delivered code must go is a workflow decision.
Deferring uses `set_disposition`, rather than a signoff verdict. Leaving `dropped`
needs actual revival `authorization`. Completed work stays immutable.

Protocol 11/schema 7 simplify that contract and expose `latest_rejection` on full
task reads and `get_next_action`: source (`review`/`signoff`), verdict, reasons,
originating attempt/workstream/specification, timestamp and decision reference.
Each actual reviewer rework or signoff rework/revise replaces that context; later
passes and approvals do not fabricate a new rejection. Slim cards show
`rejected: true` until a newer result is recorded; `include=["attempt"]` adds the
provenance without the reasons. Current execution/proof remains local to
its branch. Earlier rejection rounds remain in audit/signoff history, including
old defer decisions and judgment fields unchanged. Schema 7 adds only an indexed
projection over factual audit records, with no business-row rewrite. Existing
databases use the same verified online backup/transactional migration mechanism,
now with a `*.pre-schema-7.*.sqlite3` backup. Markdown export v5 includes latest
rejection context and signoff history.

## Deferred work

V1 does not import TASKS.md, support nested groups,
persistent dynamic scope filters, mandatory claims or leases, dedicated native
client packages, automatic client configuration, or editable export
synchronization. A complete Codex dogfood trial and then Claude Code, OpenCode
and Pi access/catalog validation remain product proof beyond the unit and stdio
suite. See [DESIGN.md](../DESIGN.md) for the current design and boundary.

Protocol 12 uses schema 8 and the same 42 tools. Optional attributed concerns live
in a separate private attempt JSON column. Existing databases receive a verified
online `*.pre-schema-8.*.sqlite3` backup before transactional migration. Migration
initializes empty metadata without inferring concerns from historical evidence;
every original evidence byte survives, including reserved JSON lookalikes.
Reviewer additions never rewrite evidence, and omitted/null/empty input preserves
both evidence and existing metadata bytes. Refresh client discovery to see the
optional inputs and concern projections.

Schema 6–8 pending-intent classification uses the archived migration reason,
so frozen acceptance columns cannot withdraw adoption after candidate spec edits.
A candidate `queue_members` row that contradicts a retained task exclusion aborts
with `membership_migration_conflict`, leaving the database unchanged and its
verified backup available. Resolve the intended scope on a disposable copy and
rerun; the migration never guesses or erases an original exclusion. Archives
recover recorded references, not candidate-only intent that was never persisted.
