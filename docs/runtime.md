# Runtime identity and stale capabilities

The coordinated main rollout deployed protocol revision `14` and database schema
revision `10`. Protocol `15` trims the catalog to 26 tools, then adds `set_note` (27 tools) and `archive_workstream` (28 tools) on the same schema `10`; notes, archive state and `get_next_action` picked markers use additive tables that schema 10 servers ignore. Reconnect existing MCP clients to
refresh their tool catalogs, and inspect each connection's runtime identity.
Future changes that could break active clients must remain isolated until a
coordinated rollout.

Call `init` on the client connection being diagnosed; every successful response
carries these fields under `runtime`. It does not select a new executable or
refresh the client's catalog, and retains its usual setup and audit behavior.

| Field | Meaning |
| --- | --- |
| `package_version` | Installed `task-mcp` distribution metadata, also used in MCP server initialization. Uninstalled source usage reports metadata unavailable. |
| `source_identifier` | `sha256:` fingerprint of package Python sources, bundled reference skills and viewer assets, captured once at runtime startup. Relative paths and file bytes are hashed; Git metadata and bytecode are excluded. Works in editable checkouts and installed wheels, including uncommitted source edits. |
| `protocol_schema_revision` | Task MCP application tool/result contract revision (`17` in this interface), independent of package version, task revisions, export formats and the negotiated MCP wire protocol. Bump for a contract change. Protocol 8/schema 5 add review/signoff prerequisite milestones with review defaults for existing links/proposals. Protocol 9 adds audited prerequisite removal. Rejected candidate 10/schema 6 introduced exclusive queues; 13/schema 9 restore nonexclusive live scopes. Protocol 11/schema 7 simplify signoff to four verdicts/reasons and project latest rejection with an indexed audit lookup; its catalog has 42 tools. Protocol 12/schema 8 add optional value/design concern inputs and compact current concern references, stored in a private attempt column without rewriting proof or inferring historical metadata. Protocol 15 trims the catalog from 42 to 26 tools: setup actions, the checkout/branch match check and runtime identity live in `init`; group creation, membership and listing use `create_task`, `update_task`, `list_tasks` and the workstream membership tools; observer gate proposals are retired; export is CLI-only; and an explicit user approve may accept a result awaiting independent review. It then adds `set_note` and ready `init` notes (27 tools), and `archive_workstream` with `include_archived` discovery filters and init state `archived` (28 tools). Protocol 16 combines these changes with descriptive public task/group IDs and schema11 private UUID identity storage. Protocol 17 gives every `init` result that is not ready one shape: a fixed `message`, `workstreams` with `roles` (`requested`, `bound`, `candidate`, `name_holder`) and `next` calls (`resume`, `rebind`, `create`, `check`, `unarchive`) with exact arguments. It replaces `choices`, `workstream`, `bound_workstream`, `name_holder`, `requested_project`, `candidates`, `candidate_total`, `more_candidates`, `workstream_candidates`, `more_workstreams` (now for `workstreams`) and `project_candidates` (now `projects`); states and error codes are unchanged. |
| `process_started_at` | UTC server runtime startup timestamp, captured when its identity module is first imported near process launch, rather than per request. |
| `process_id` | OS PID of the serving process. Compare it with the startup timestamp because PIDs can be reused. |
| `python_executable`, `package_path` | Interpreter and imported package location, useful for finding the wrong virtual environment or checkout. |
| `database_schema_revision` | Current persisted SQLite `PRAGMA user_version` of the configured database (`11` after server startup). The diagnostic reads it through a read-only connection. |

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

1. Record `init.runtime` from the affected client, including PID, startup
   time, source identifier and paths. If the response has no `runtime`, that
   connection is serving code from before this diagnostic.
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


Protocol 15/schema 11 add descriptive public task/group IDs. Creation tools and
decomposition members accept optional `public_id`; existing `tsk_…` references
remain unchanged. A private UUID registry is backfilled without changing any task,
relationship, proof or audit row. Verified `*.pre-schema-11.*.sqlite3` online backups
include committed WAL data; identity backfill and validation roll back atomically.
Rehearse on copies before coordinated live rollout and reconnect clients afterward
to refresh the creation-tool catalog.
