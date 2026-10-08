# simtask installation and checkout layout

The workstation uses two Git checkouts:

- `/home/simon/workspace/simtask`: the live checkout on `main`.
- `/home/simon/workspace/simtask-dev`: the development worktree. Its existing
  `viewer-service` branch name is retained.

Both have their own editable `.venv`. The dev viewer uses `.dev/tasks.sqlite3`,
a disposable copy of the live database. Run `./run.sh --dev --tailscale` in the
dev checkout to print its link. `--restart` refreshes its database copy.

The live database, credentials, backups and traces live in
`~/.local/share/simtask`. `~/.local/share/task-mcp` is a compatibility symlink
to that directory. The filename remains `tasks.sqlite3`. The `tasks` MCP client
key and `mcp__tasks__` tool prefix remain unchanged.

The boot-enabled user services are:

- `simtask-viewer.service`: the live viewer, Tailscale HTTPS port 8787.
- `simtask-dev-viewer-simtask-dev-<path-hash>.service`: dev viewer, port 8788.
- `simtask-mcp.service`: persistent MCP HTTPS endpoints, port 8789.

The MCP service has its own `.mcp-venv` in the live checkout. It uses a regular
package installation, rather than an editable install. Changes to either
checkout do not change this running service's installed code. After merging
and validating a release in the live checkout, update it with:

```sh
.mcp-venv/bin/python -m pip install --force-reinstall --no-deps .
systemctl --user restart simtask-mcp.service
```

Saved client URLs and credentials survive the rename. Claude uses `/claude/mcp`
and Codex uses `/codex/mcp`, with their separate audit actors. The private MCP
credential remains next to the live database in `tasks.sqlite3.mcp.token`.

The former `task-mcp` command, `task_mcp` imports and `TASK_MCP_` settings remain
compatible. `SIMTASK_` settings take precedence. If only the former default
database exists, the new code selects it. If both default paths contain
different databases, initialization requires explicit reconciliation rather
than silently selecting one.

Checkout consolidation preserves the unfinished laptop dev-access work in
commit `04cc038` on `isolated-dev-setup`, and in an external binary patch.
All original branches remain. The rename backup contains a Git bundle, online
database backups, previous configurations, service units, dev database copies
and viewer credentials. Retired workstreams retain their recorded proof.

The project header relocation uses `scripts/relocate-project.py`, because the
MCP catalog has no project-edit tool. It performs an audited transaction,
validates the previous path and rejects a destination owned by another project.
Workstreams are rebound or archived with the normal Store/MCP operations.

Verification covers service restart, saved client configurations, viewer
identities, credential preservation, database integrity and unchanged historical
audit bytes. The machine is not rebooted during the rename.
