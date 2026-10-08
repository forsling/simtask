# Task MCP on simon-lws and the laptop

The live task database is on simon-lws at
`~/.local/share/task-mcp/tasks.sqlite3`. Workstation MCP clients use
`/home/simon/workspace/task-mcp/.venv/bin/task-mcp`. Laptop clients run that
executable through `ssh -T herdr-server`; an SSH failure makes the tools
unavailable. The renamed laptop database remains the final migration backup.

This documents the installed personal setup, not a database migration procedure.
The 2026-10-08 rework did not copy databases, change live MCP configurations,
restart viewers, or interrupt agent sessions. The original migration evidence
remains in Task MCP task `tsk_49cd1b5a400f4bc4b873687817078f16`.

## One source for user skills

Edit only the laptop's `~/.agents/skills`. Its `grill-me` copy was selected on
2026-10-08 because the existing workflow already uses that directory as the
source for the other nine skills. Its SHA256 is
`b443626c901eca37fac6484255506a306c7412a993af409b5225a55d2b23ee81`.

`~/.local/bin/sync-agent-skills` is installed on both machines from
[`scripts/sync-agent-skills`](../scripts/sync-agent-skills). It manages
`superdevloop`, `task-signoff`, `task-capture`, `task-design`, `bughunt`,
`rethink`, `bigthink`, `lean-bughunt`, `luna-bughunt`, and `grill-me`.
Claude uses `~/.claude/skills/<name>` symlinks to the same files in
`~/.agents/skills/<name>` on both machines.

Run on either machine:

```sh
~/.local/bin/sync-agent-skills          # sync content and repair Claude links
~/.local/bin/sync-agent-skills --check  # read-only comparison; nonzero if different
```

The laptop pushes to `herdr-server`. On simon-lws the script pulls from the
laptop through `simbuntu`, and repairs the laptop's own Claude links through
that connection. Both directions use the same laptop source. SSH uses
`BatchMode=yes` and `ConnectTimeout=10`; authentication failures stop the sync.
The workstation pull also works when the laptop's non-interactive environment
has no SSH agent to authenticate a push back to the workstation.

Before replacing differing skill content or a Claude directory/link, the script
moves the old entry into `~/.local/share/agent-skills-backups/<UTC-time>-<PID>`.
These backups are outside the skill discovery directories. Files removed from
the source are removed from the destination skill, with its previous directory
retained in the backup. Unmanaged skills are left alone.

The original differing copies were preserved at:

- Workstation: `/home/simon/.local/share/agent-skills-backups/20261008T181551Z-3567995/agents/grill-me`
  (SHA256 `74147eb6010a65957efef2b9e0f0b3ff935c1def7fc117697151b1d0f3610556`).
- Laptop: `/home/simon/.local/share/agent-skills-backups/20261008T181552Z-132176/claude/grill-me`
  (SHA256 `b443626c901eca37fac6484255506a306c7412a993af409b5225a55d2b23ee81`).

After changing the script itself, install the same version on both machines.
From the workstation checkout:

```sh
install -m 0755 scripts/sync-agent-skills ~/.local/bin/sync-agent-skills
scp scripts/sync-agent-skills simbuntu:/home/simon/.local/bin/sync-agent-skills
```

## Refusing laptop MCP selection

The laptop has an executable `/home/simon/.local/bin/task-mcp-refuse-local`,
installed from [`scripts/task-mcp-refuse-local`](../scripts/task-mcp-refuse-local).
It writes a clear refusal to stderr and exits with status 1. It accepts and
ignores existing arguments, imports no Task MCP code, and never opens a task
database. It emits no MCP output and cannot start a local fallback.

To select it in laptop Codex, replace only the `command` line under
`[mcp_servers.tasks]` in `~/.codex/config.toml` with this exact line:

```toml
command = "/home/simon/.local/bin/task-mcp-refuse-local"
```

To select it in laptop Claude Code, replace only the `command` property of
`mcpServers.tasks` in `~/.claude.json` with this exact line:

```json
"command": "/home/simon/.local/bin/task-mcp-refuse-local",
```

Keep the existing `args` and other properties. The stub ignores them, including
the SSH command's arguments. These are selection instructions; the installed
live configurations still use SSH. Reconnect the client after making a
selection. To resume SSH, change that one line back to `command = "ssh"`
(Codex) or `"command": "ssh",` (Claude).

Inspect the refusal without opening a database:

```sh
ssh -T simbuntu /home/simon/.local/bin/task-mcp-refuse-local
```

The expected status is 1, with an explanation that simon-lws holds the only
live task database. For installation from the workstation checkout:

```sh
scp scripts/task-mcp-refuse-local simbuntu:/home/simon/.local/bin/task-mcp-refuse-local
```

## Verification limits

Task `laptop-ssh-audit-auth` tracks the non-interactive laptop environment:
`SSH_AUTH_SOCK` is absent and laptop-to-workstation SSH fails with status 255.
This rework does not alter authentication. A successful workstation pull
verifies the skills, but does not verify a fresh laptop MCP connection or the
laptop browser's access to the workstation viewer. Historical migration and
viewer evidence are retained in the original task attempt; they were not
repeated during this rework.
