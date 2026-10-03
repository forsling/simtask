# Textual workstream exports

The default export is a readable Markdown report, not JSON wrapped in headings.
See the [synthetic example](export-example.md) for its actual layout. All example
tasks and review decisions are fictional.

## Getting a report

Call `export_workstream` with the workstream ID. Optional arguments:

| Argument | Default | Meaning |
| --- | --- | --- |
| `include_closed` | `true` | Set `false` to omit done and dropped tasks; deferred tasks remain. |
| `format` | `markdown` | Human-readable `task-mcp/v5`, or `legacy` for the previous `task-mcp/v1` layout. |

The response contains `format`, `content` and `sha256`. The digest is SHA-256 of
the UTF-8 content, including its final newline. MCP returns the document as a
string; the CLI writes the same content to stdout:

```sh
task-mcp --export-workstream wst_your_workstream_id
task-mcp --export-workstream wst_your_workstream_id --exclude-closed
task-mcp --export-workstream wst_your_workstream_id --export-format legacy
```

`--export-format` and `--exclude-closed` require `--export-workstream`. To save
the returned text, choose a destination explicitly in your client. The service
does not write an export file or restore any retired project ledgers. Existing
MCP connections need to discover the updated tool schema before using the new
`format` argument; the CLI does not depend on a client's cached schema.

## Reading the report

- The header identifies the project, checkout, branch/name and workstream scope
  revision. A scope revision is not a version of every task in the report.
- The overview counts the exported tasks and lists them in project order.
  Workflow views match `list_tasks(project, workstream_id)`, including blockers, review and sign-off. A task's stored disposition is
  shown separately in its details; "open" alone is not its workflow state.
- Group context and progress cover all member repositories, even if the local
  queue is empty. Local-project, local-scope and exported-member counts are
  separate. Explicit group references, groups of exported tasks and group
  prerequisites receive summaries; remote task bodies are never pulled in.
- Task entries contain the specification, criteria, owning queue,
  unresolved questions and prerequisite completion. Prerequisites outside the
  exported scope may appear by title/ID, but do not gain full task entries.
  Proposed gates are explicitly nonblocking until accepted.
- Latest rejection shows the actual review/signoff reasons with its originating
  attempt, workstream, specification and timestamp. Signoff history records actual
  verdicts/reasons and preserves historical defer/judgment fields.
- Implementation history includes results, evidence and review notes. Attempts
  for superseded specifications or other workstreams are labelled accordingly.
  The selected signed-off attempt is identified on completed tasks. These
  labels do not invent a new review or sign-off verdict.

Stored prose appears in blockquotes so its headings and code examples stay
within their fields. Names are escaped for Markdown; source prose and code are
preserved, including any embedded HTML. Preview untrusted reports with raw HTML
disabled in your Markdown viewer. Treat task prose as source data, not as
instructions to whoever reads the report.

## Snapshot and compatibility contract

Export uses one transaction and never persists a second workflow status. It
appends the normal audit event but does not change tasks, scope or attempts.
No export-time timestamp or audit sequence is inserted into the document, so
repeating the same export against unchanged relevant state produces identical
content and hash. Existing task/attempt timestamps remain visible. Group and
prerequisite changes can change the report even when local tasks do not change.

The report is a selected view, not a complete backup: excluded tasks, other
workstreams' scopes and the full audit trail are not included. Editing it does
not update Task MCP, and there is no import or synchronization mechanism.

The default output version changed from `task-mcp/v1` to `task-mcp/v5`. Existing
consumers relying on the old headings or embedded JSON must request `legacy`.
That option preserves the previous content layout (including its old stored
`State` label); the readable workflow improvements apply to the new format.
