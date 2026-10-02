# Runtime identity and stale capabilities

Call `runtime_info` on the client connection being diagnosed. Successful `init`
responses carry the same fields under `runtime`, so an existing client that
already knows `init` can inspect the process even if its tool catalog omits the
new diagnostic. Neither surface selects a new executable or refreshes the
client's catalog. `init` retains its usual setup and audit behavior;
`runtime_info` writes no database state or audit event.

| Field | Meaning |
| --- | --- |
| `package_version` | Installed `task-mcp` distribution metadata, also used in MCP server initialization. Uninstalled source usage reports metadata unavailable. |
| `source_identifier` | `sha256:` fingerprint of package Python sources, bundled reference skills and viewer assets, captured once at runtime startup. Relative paths and file bytes are hashed; Git metadata and bytecode are excluded. Works in editable checkouts and installed wheels, including uncommitted source edits. |
| `protocol_schema_revision` | Task MCP application tool/result contract revision (currently `1`), independent of package version, task revisions, export formats and the negotiated MCP wire protocol. Bump for an incompatible contract change. |
| `process_started_at` | UTC server runtime startup timestamp, captured when its identity module is first imported near process launch, rather than per request. |
| `process_id` | OS PID of the serving process. Compare it with the startup timestamp because PIDs can be reused. |
| `python_executable`, `package_path` | Interpreter and imported package location, useful for finding the wrong virtual environment or checkout. |
| `database_schema_revision` | Current persisted SQLite `PRAGMA user_version` of the configured database (currently `1`). The diagnostic reads it through a read-only connection. |

The source identifier is a startup snapshot, not a fresh hash of files at each
call and not a Git commit ID. Editing an editable install while the process is
alive leaves its reported identifier unchanged. Two fresh processes started
from identical package bytes have the same source identifier even when their
PIDs and startup timestamps differ. Package metadata alone may retain the same
version throughout development. The identifier detects changed resources but
does not promise hot reload of resources or Python code.

Existing unnumbered databases have revision `0`; normal Store startup runs the
existing schema creation/migration and foreign-key checks, then transactionally
sets revision `1`. Existing task IDs, content and audit history survive. A
database with a higher revision is rejected rather than downgraded. Future
storage migrations must advance the revision after their checks pass.

## Minimum reliable reconnect procedure

1. Record `runtime_info` (or `init.runtime`) from the affected client, including
   PID, startup time, source identifier and paths. If neither response includes
   identity, that connection is serving code from before this diagnostic.
2. Disconnect the Task MCP connection and stop the specific stdio server
   process owned by it. Verify that its PID has exited using the host's process
   manager. If toggling the connector leaves it alive, shut down the owning
   client or MCP host process and verify termination there. An editable
   reinstall does not terminate an old server. Avoid stopping an unrelated
   Task MCP server or the separately launched local browser companion.
3. Reconnect using the intended executable and database configuration. Ask the
   client to refresh/discover its tool list (`tools/list`) and server
   initialization instructions. A new chat alone may reuse the same host
   process and cached descriptors.
4. Call the identity diagnostic in that actual client connection. Verify a new
   startup timestamp/PID, the expected interpreter/package paths and package
   version, and a new source identifier when package bytes changed. Confirm the
   refreshed catalog contains the expected tools and annotations.

An old startup timestamp/PID identifies a surviving old process. A fresh process
with the wrong paths identifies an executable or installation mismatch. A fresh
process with the expected new source identity but an old tool list points to a
stale client capability cache. Refresh that host's discovery cache or restart
its owning process, then repeat the live checks. Both process and catalog checks
must pass; the server cannot clear client caches. A reboot is a fallback for a
host that cannot otherwise be terminated/refreshed, not the normal required
step. Client-specific UI controls vary; the verification above defines success.

The disposable stdio demo (`.venv/bin/python examples/demo.py`) checks a fresh
subprocess and persisted task behavior. It is useful to verify the installation,
but it does not prove the affected client's cached catalog has changed.

Protocol/database schema revision 2 introduces independent persisted origin,
classified `{basis, note}` approval for create/accept, and audited withdrawal.
Schema 0/1 upgrades require the service's fresh verified online backup. Roll out
code, stored schema and refreshed client tool catalogs together. During a
protected period with old clients, use only isolated candidate code and explicit
disposable databases; prepare native skills without installing or reloading them.
