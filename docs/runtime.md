# Runtime identity and stale capabilities

The coordinated main rollout is complete: the deployed protocol revision is
`14` and database schema revision is `10`. Reconnect existing MCP clients to
refresh their tool catalogs, and inspect each connection's runtime identity.
Future changes that could break active clients must remain isolated until a
coordinated rollout.

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
| `protocol_schema_revision` | Task MCP application tool/result contract revision (`14` in the deployed interface), independent of package version, task revisions, export formats and the negotiated MCP wire protocol. Bump for a contract change. Protocol 8/schema 5 add review/signoff prerequisite milestones with review defaults for existing links/proposals. Protocol 9 adds audited prerequisite removal. Rejected candidate 10/schema 6 introduced exclusive queues; 13/schema 9 restore nonexclusive live scopes. Protocol 11/schema 7 simplify signoff to four verdicts/reasons and project latest rejection with an indexed audit lookup; its catalog has 42 tools. Protocol 12/schema 8 add optional value/design concern inputs and compact current concern references, stored in a private attempt column without rewriting proof or inferring historical metadata. |
| `process_started_at` | UTC server runtime startup timestamp, captured when its identity module is first imported near process launch, rather than per request. |
| `process_id` | OS PID of the serving process. Compare it with the startup timestamp because PIDs can be reused. |
| `python_executable`, `package_path` | Interpreter and imported package location, useful for finding the wrong virtual environment or checkout. |
| `database_schema_revision` | Current persisted SQLite `PRAGMA user_version` of the configured database (`10` after deployed server startup). The diagnostic reads it through a read-only connection. |

The source identifier is a startup snapshot, not a fresh hash of files at each
call and not a Git commit ID. Editing an editable install while the process is
alive leaves its reported identifier unchanged. Two fresh processes started
from identical package bytes have the same source identifier even when their
PIDs and startup timestamps differ. Package metadata alone may retain the same
version throughout development. The identifier detects changed resources but
does not promise hot reload of resources or Python code.

Existing unnumbered databases have revision `0`. Candidate Store startup creates
fresh databases at revision `10`
directly. Before upgrading an existing revision `0`, `1`, `2`, `3`, `4`, `5`, `6`, `7`, `8` or `9` database, it creates
and verifies a fresh SQLite online backup under the writer lock, including
committed WAL data. It then transactionally upgrades the schema, checks integrity
and foreign keys, and sets revision `10` only after those checks pass. Existing
task IDs, content and audit history survive; the backup remains available after
success or rollback. A database with a higher revision is rejected rather than
downgraded. Future storage migrations must advance the revision after their
checks pass.

Schema `2` historically introduced persisted origin and purpose-decision storage.
Schema `9` restores original live nonexclusive scopes and removes the rejected
exclusive queue table. It recovers original references from retained tables and
schema 6–8 archives, retaining candidate additions without selecting an owner.
Mutable scoped legacy pending intent becomes a normal unresolved question;
membership and all specification/proof/history bytes survive. A private migration
archive preserves original task/scope facts and a verified online
`*.pre-schema-9.*.sqlite3` backup supports rollback. Current tools expose
`add_to_workstream`/`remove_from_workstream`, membership IDs and derived adoption;
separate purpose-decision fields remain retired.
Protocol `3` introduced the `signoff_task` decision/attempt-revision contract and
explicit dropped-task revival authorization. Historical purpose/result judgments,
exact revisions and original decision references remain immutable audit facts;
new signoff records use the actual user verdict. `Store.get_tasks` and viewer full details expose structured
`signoff_decisions` and historical decision facts. MCP
`get_tasks(specification=true)` exposes memberships and current gates without
signoff history. Retrieve historical decisions through paged
`list_events(task_id=..., include_details=true)`; detailed audit reads retain older
records.
Roll out code, stored schema, companion skills and refreshed client tool catalogs
together, then use the reconnect procedure below to verify the affected client.

Protocol `14` / schema `10` replace project-level anchor moves with
`reorder_tasks(workstream_id, task_ids, expected_order_revision)`: one ordered-ID
prefix, with all unlisted members following in their previous relative order.
Workstreams store independent order revisions and local positions. Every effective
list initially preserves existing project baseline order at revision 0. Scope,
group and exclusion expressions remain live; membership changes append new
members and remove departed ones transactionally. Boards/action/init/status and
exports expose `workstream_order_revision`; project-wide boards expose only a
deterministic baseline. Empty/already-matching requests preserve revision/keys.
Task/spec/proof revisions and other workstream lists never change on reorder.
Verified online `*.pre-schema-10.*.sqlite3` backups include committed WAL data;
DDL, list seeding and validation roll back together on failure. Existing project
order columns remain private historical storage, without a public reorder surface.
The coordinated rollout deployed the new schema and packaged guidance together.
Existing clients must reconnect to refresh their cached tool catalogs.

Protocol revision `5` adds global concrete prerequisites and compact blocker
references. Protocol revision `6` requires full-spec-bound artifact/commit
references and actual verification for factual result recording, without
clearing workflow gates. It replaces the implementation-only `get_next_task`
with `get_next_action`, selecting eligible implementation or one complete local
pending review in shared project order. Both revisions retain database schema
revision `3`.

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

Schema 6–8 pending-intent classification uses the archived migration reason,
so frozen acceptance columns cannot withdraw adoption after candidate spec edits.
A candidate queue membership that contradicts a retained task exclusion aborts
with `membership_migration_conflict`, leaving the database unchanged and its
verified backup available. Resolve the intended scope on a disposable copy and
rerun; the migration never guesses or erases an original exclusion. Archives
recover recorded references, not candidate-only intent that was never persisted.
