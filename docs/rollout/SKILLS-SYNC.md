# Rollout: AGENTS.md and user-level skill sync

Task tsk_005b1aff5757484ca6c0f720278624c4. Prepared in the integrated `dev`
branch, initially carried from the historical `workflow-overhead` preparation.
**NOT APPLIED.** This document and `AGENTS.md.proposed` are review artifacts.
All commands below are proposed future rollout steps, not instructions to run
while preparing or reviewing this branch. Apply only after the user approves a
rollout window and the reviewed development code is merged to `main`.

The combined development target is protocol **16**, database schema **11** and
skill catalog **2.0.0**, as defined in `src/task_mcp/runtime.py`,
`src/task_mcp/store.py` and `src/task_mcp/reference.py`. Do not infer the live
runtime or database revision from these branch files. Follow the migration and
restart requirements in [README.md](../../README.md#notes)
and [DESIGN.md](../../DESIGN.md), and the client process/catalog checks in
[docs/runtime.md](../runtime.md#minimum-reliable-reconnect-procedure).

## 1. Packaged skills after this change

Catalog version 2.0.0. Keys, directories and frontmatter `name` are identical:

| Catalog key | Installed user-level? |
| --- | --- |
| `superdevloop` | yes |
| `task-signoff` (was `signoff`) | yes |
| `task-capture` (was `feature-capture`) | yes |
| `task-design` (was `feature-design`) | yes |
| `proposal-review` | no (served by `get_default_skills`) |
| `init` | no; installing it as `init` would collide with Claude Code's built-in `/init` |

Because the packaged frontmatter `name` now equals the directory name, the
installed copies are verbatim copies of the packaged files (no renaming step).

## 2. Sync the four installed skills

Current layout: real directories `~/.agents/skills/<name>/SKILL.md`, with
`~/.claude/skills/<name>` symlinks pointing at them. Leave every other entry
alone: `bughunt`, `rethink`, `bigthink`, `lean-bughunt`, `luna-bughunt` (all
symlinked from `~/.agents/skills`), `grill-me` (a real directory in both
places) and both `synced/` directories.

```sh
SRC=/path/to/task-mcp/src/task_mcp/reference_skills
for name in superdevloop task-signoff task-capture task-design; do
  mkdir -p "$HOME/.agents/skills/$name"
  install -m 0644 "$SRC/$name/SKILL.md" "$HOME/.agents/skills/$name/SKILL.md"
  [ -L "$HOME/.claude/skills/$name" ] || ln -s "$HOME/.agents/skills/$name" "$HOME/.claude/skills/$name"
done
```

Verify:

```sh
for name in superdevloop task-signoff task-capture task-design; do
  cmp "$SRC/$name/SKILL.md" "$HOME/.agents/skills/$name/SKILL.md" && echo "same $name"
  readlink "$HOME/.claude/skills/$name"
  /usr/bin/python3 ~/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/scripts/quick_validate.py "$HOME/.agents/skills/$name"
done
```

## 3. Required one-line fix in five unrelated skills

`bigthink`, `bughunt`, `rethink`, `lean-bughunt` and `luna-bughunt` each contain
the fallback `get_default_skills(name="feature-capture")`. Catalog 2.0.0 has no
`feature-capture` key, so that fallback would now return `unknown_skill`. They
already name the `task-capture` skill, so only the fallback key changes:

```sh
for name in bigthink bughunt rethink lean-bughunt luna-bughunt; do
  sed -i 's/get_default_skills(name="feature-capture")/get_default_skills(name="task-capture")/' \
    "$HOME/.agents/skills/$name/SKILL.md"
done
rg -n 'name="(feature-capture|feature-design|signoff)"' "$HOME"/.agents/skills/*/SKILL.md   # expect no output
```

The pattern matches only retired catalog keys passed to `get_default_skills`.
Plain mentions of `(feature-design)` are expected: the synced task-design skill
deliberately recognises that older gate phrase.

## 4. Replace this project's AGENTS.md

Replace the untracked `/path/to/task-mcp/AGENTS.md` with
`AGENTS.md.proposed` (next to this file). The old file restated workflow rules
that now live once in tool descriptions and skills. The edits proposed in the
historical tool-trim notes (the sign-off bullet and the group/membership
bullet) are superseded: the approve-without-review rule
is in `signoff_task`'s description and group membership is in the
`add_to_workstream`/`remove_from_workstream` descriptions. The one project fact
kept from the old file beyond paths is "track work in Task MCP, not in
TASKS.md/BACKLOG.md/ARCHIVE.md".

```sh
cp /path/to/task-mcp/docs/rollout/AGENTS.md.proposed \
   /path/to/task-mcp/AGENTS.md
```

## 5. Coordinated migration and reconnect

The original protocol15-only skill/catalog preparation did not need a database
migration. That is historical context: the combined `dev` target includes the
public identity migration to schema11. Its notes, archive and picked-marker
additions do not make schema11 restart-compatible with schema10 code.

During the future user-approved rollout window:

1. Record the live `init.runtime` identity and current database revision, the
   executable/database configurations and the prior code/catalog/installed-skill
   versions needed for rollback. Rehearse the combined migration on a disposable
   SQLite-consistent copy first.
2. Back up the live database before migration using SQLite's online backup API,
   including committed WAL data; verify integrity, foreign keys and the source
   schema revision, and keep the private backup. Do not use a raw file copy of
   an active WAL database. The new Store also creates and verifies an adjacent
   `*.pre-schema-11.*.sqlite3` backup under its writer lock before transactional
   migration; preserve that backup after success or failure.
3. Coordinate all MCP and viewer writers for the chosen database. Upgrade the
   reviewed main code, companion skills and AGENTS reminder together with the
   refreshed client catalog. Stop/restart the affected processes in this window;
   use the new code for every process that reopens the migrated database. An
   already-running schema10 server can continue its existing operations, but
   schema10 code refuses to reopen schema11. An editable install alone leaves
   old imported code and client catalog caches alive.
4. Reconnect each affected MCP client to the intended executable/database,
   refreshing its tools and server instructions. Check `init.runtime` in that
   actual connection: new PID/startup time, expected paths and source identifier,
   protocol revision16 and persisted database revision11. Confirm discovery of
   the current 28 tools and their descriptions/input schemas. `record_result`
   declares `{kind: artifact|commit, reference}` with no extra artifact keys.
   Check the viewer separately after its coordinated restart.
5. Confirm `get_default_skills()` returns catalog version `2.0.0` and exactly
   `init`, `task-capture`, `task-design`, `proposal-review`, `superdevloop` and
   `task-signoff`. Verify the four installed skills and symlinks as above. Both
   process identity and refreshed discovery must pass; a fresh subprocess demo
   alone does not verify an existing client's connection.

For rollback, stop **all writers** before restoring the retained pre-migration
backup with SQLite's backup API. Never restore while MCP or viewer writers are
active. Account explicitly for writes made after the backup: they are not in it.
Restore matching prior code, installed guidance and catalogs and reconnect the
clients; restarting old code against schema11 is not a rollback.

Existing briefs keep their `Feature design required (feature-design):`
questions; task-design treats them like the new `(task-design)` prefix, so no
question rewrite is needed. `export_workstream` remains CLI-only
(`task-mcp --export-workstream WORKSTREAM_ID`); runtime identity is the
`runtime` block of every `init` response.
