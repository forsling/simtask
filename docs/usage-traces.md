# Evaluating real MCP usage

The stdio server automatically records its incoming MCP requests and observed
results in private local JSONL files. Consumers need no reporting calls or
additional workflow instructions. Collection adds no tools, response fields or
MCP notifications, and does not change task state or the existing semantic audit.

The default directory is `<database-path>.traces` beside the database. For the
standard database it is `~/.local/share/task-mcp/tasks.sqlite3.traces`. It contains
full request parameters and SDK-shaped results, including task text, evidence and
private viewer links. The directory is private to its owner and files use mode
0600. Existing directories must already be private; collection does not change
permissions on arbitrary directories.

Defaults are 30 days and 256 MiB **across all processes using that directory**.
Files rotate at approximately 4 MiB. Individual request/result capture is capped
at 1 MiB; larger payloads retain a UTF-8 JSON prefix, original byte count and
SHA-256 fingerprint, with `truncated=true`. The prefix is not a complete JSON
document. Queue capacity is 8 MiB per connection. A background writer handles
disk operations; task operations never wait for retention locks. Full queues and
storage failures lose observations rather than failing or retrying the task.
Later successful records report cumulative collection failures. A local stderr
diagnostic is rate-limited to once per minute. Shutdown flush is best-effort and
bounded to two seconds; abrupt termination can leave incomplete calls.
Exception messages have a separate 4,096-character cap with explicit truncation.
The first implementation uses POSIX ownership and file-locking APIs.

Retention removes old segments as new records arrive, using their first
observation time rather than their last write. Active segments also expire;
regular appends cannot prolong the retention of earlier payloads. Each segment
retains runtime/source identity and configured capture/retention limits so these
remain available in reports after older segments are pruned. Metadata bytes
count toward the shared storage budget. Reports describe only
retained observations; absence of a record does not establish absence of a call.
An inaccessible directory or process killed before any successful write may
leave no diagnostic record. Keep any traces needed for longer comparisons in a
separate private archive outside the rotating collection directory.

Use `--no-trace` or `TASK_MCP_TRACE=0` to disable collection. Server options
`--trace-dir`, `--trace-max-age-days`, `--trace-max-bytes` and
`--trace-payload-bytes` override the corresponding defaults. A running server
must restart/reconnect before it uses changed configuration or new tracing code.

## Offline report

```sh
task-mcp trace-report
task-mcp trace-report --tool get_tasks --since 2026-10-03T00:00:00Z
task-mcp trace-report --entity tsk_example --json
task-mcp trace-report --connection CONNECTION_ID --all --json
```

`--db` selects the default trace directory without opening the database;
`--trace-dir` selects one directly. Reports never initialize or write task state.
Tool statistics cover the selected data, while sequence and candidate details
default to 20 entries. Use `--offset`/`--limit` to page, or `--all` to deliberately
include every detail. Raw request/results remain in the JSONL files; the report
does not repeat their bodies.

The report gives per-tool call counts, outcomes and p50/p95 server handling
durations, payload totals, largest responses and chronological call references.
Candidates include unchanged specification rereads, card-to-spec sequences,
reads after write acknowledgements and retries after errors. Each candidate
includes supporting call IDs/timestamps. Review, verification, negotiation and
human follow-up can legitimately repeat calls or return errors: a candidate is
an invitation to inspect, never an automatic misuse verdict.

## Measurement boundaries

Each stdio server connection gets a generated ID, and calls get distinct IDs
even when a client reuses MCP request IDs. The advertised client name/version
is descriptive. Connections are not agent conversations; a shared connection
can serve several agents/projects/workstreams. Entity filters use explicit IDs
in the calls/results and never infer a project from the last `init`.

Sizes count compact UTF-8 JSON with non-ASCII characters unescaped. Request
sizes cover `{method, params}`; result sizes cover the SDK-shaped result object.
They exclude the outer JSON-RPC ID/envelope and transport framing. Structured
content and content-block JSON sizes are recorded separately where present;
they are components of the total, not additional context to sum again. Error
records describe observed exceptions, not a reconstructed wire error response.

Handling duration wraps the SDK handler/validation/result shaping and excludes
trace preparation/writes and host approval/queuing before dispatch. The server
cannot observe calls rejected before reaching it, malformed transport input
rejected before middleware, consumer edits/tests/thinking, host repetition of
catalog instructions, or the precise bytes/tokens inserted into model context.
Viewer HTTP requests and direct Store/CLI operations are outside this MCP trace.

For repeatable enabled/disabled overhead measurements on disposable synthetic
data, run `python examples/trace_benchmark.py --output /tmp/trace-benchmark.json`.
The benchmark interleaves modes with alternating call order across two fresh
connection pairs. Local scheduling and background writes can affect either
mode; negative differences are noise, not evidence that tracing makes calls
faster.
