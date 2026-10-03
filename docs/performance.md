# Mutation latency investigation

## Finding

The measured multi-second delay occurs in the client's synchronous policy
approval stage before Task MCP executes the mutation. It is not the measured
SQLite/stdio execution cost. Two legitimate calls on 2026-09-26 were timed in
the actual client and correlated with the live task audit and host approval
logs; no synthetic tasks were added to the live database.
The second call used the retired specification-acceptance mutation. These
historical timings do not measure the current `queue_task` operation.

| Call | Client start (UTC) | Approval starts (UTC) | Task audit (UTC) | Client end (UTC) | Elapsed |
| --- | --- | --- | --- | --- | --- |
| `update_task` | 11:02:16.723 | 11:02:16.727603 | 11:02:24.912778 | 11:02:24.924 | 8,201 ms |
| Retired specification-acceptance mutation | 11:02:46.158 | 11:02:46.164589 | 11:02:52.227132 | 11:02:52.250 | 6,092 ms |

Host policy-review logs show execution resuming at 11:02:24.904 and
11:02:52.218 respectively, immediately before the task audit writes. Only
11.2 and 22.9 ms remain between each audit insertion and the client return.
These logs identify the approval stage, not its internal model/network timing.
The measured calls really overwrite task state, so their destructive
classification must not be hidden to obtain a faster call.

## Troubleshooting slow client calls

If writes pause for seconds while reads return immediately, first compare a
disposable local benchmark with real client timings. Fast local execution and
a delay before the task audit event point toward the client; check its approval
logs before changing database settings. After server updates, refresh the
client's discovered tool descriptors as well.
Use [runtime identity and reconnect checks](runtime.md) to verify both the
serving process and its discovered capabilities in the affected client.

Task MCP's workflow tools operate on its own task database, not repository files
or external services. Its explicit `open_task_viewer` tool also starts or reuses
a local browser companion that exposes that database to a token-holding browser
on the same computer. See [the viewer's access and lifecycle](viewer.md).
That is a useful boundary for deciding to trust this server,
but its task data still matters: an edit or verdict can overwrite state. There
is no "owns its data, therefore read-only" exemption. Keep annotations accurate;
configure trust in the client, never by hiding effects in the server.

### Optional Codex approval settings

These are explicit user security choices, not installation defaults. The
[official MCP configuration guide](https://learn.chatgpt.com/docs/extend/mcp)
documents server defaults and per-tool overrides in `~/.codex/config.toml`.
This file is shared by local Codex clients on that host; the setting applies
across sessions and projects using that server. Trusted projects can instead
use `.codex/config.toml`. ChatGPT web does not read this local file.

For narrowly selected tools, add overrides such as:

```toml
[mcp_servers.tasks.tools.update_task]
approval_mode = "approve"

[mcp_servers.tasks.tools.queue_task]
approval_mode = "approve"
```

Alternatively, if you trust the whole Task MCP server, add this key to its
existing table, retaining its command, environment and other settings:

```toml
[mcp_servers.tasks]
default_tools_approval_mode = "approve"
```

`tasks` is the configured server name, not a required name; substitute yours.
Merge existing tables rather than declaring a TOML table twice. Per-tool policy
overrides the server default. The whole-server option covers current and future
tools, including edits, removals, review and sign-off calls, plus explicit launch
of the local browser companion. Pre-approval does not launch the viewer
automatically. Reconsider this setting if the server gains capabilities beyond
its private task data and local viewer boundary.
Neither option changes other servers or the global approval policy. Plugin
installations use a different configuration namespace; see the official guide.

Automatic tool-call approval is not authority to queue or build a task,
implementation, review or sign-off. Revision checks, workflow gates and audit
history still apply, and agents must still obtain the required user decisions.
The prototype does not authenticate actor labels or human-verdict assertions;
do not mistake workflow validation for an authorization boundary.

Reload the client configuration/MCP connection after editing, then verify with
a legitimate operation in the actual client. Do not assume a running session
has adopted a file edit. Client versions and managed policy can constrain these
settings; [managed approval requirements take precedence](https://learn.chatgpt.com/docs/app-server).
To undo the change, remove only the overrides you added (or restore their prior
values), then reload again. A valid configuration or fast local benchmark alone
does not prove the live approval delay has disappeared.

## Local baseline

The repository benchmark used read-only SQLite backup connections to create
transactionally consistent copies of the roughly 10.6 MB live database. Copies
on the same ZFS dataset as the live database were tested over 20 cycles per
operation using `examples/benchmark.py --samples 20 --source ... --project ...
--workstream ... --work-dir ...`. No live task or approval setting was changed
by the benchmark. This verifies the earlier Sol investigation with the bundled
reproducer below.
The acceptance row records the same retired mutation; these historical baseline
measurements also predate `queue_task`.

| Operation | Direct Store median | Local stdio MCP median |
| --- | ---: | ---: |
| List tasks | 3.01 ms | 7.30 ms |
| Create proposed task | 2.72 ms | 7.02 ms |
| Edit specification | 2.59 ms | 6.35 ms |
| Retired specification-acceptance mutation | 2.54 ms | 5.66 ms |
| Record result | 3.00 ms | 7.24 ms |

All 80 stdio mutation samples were below 14.6 ms. Process initialization was
414 ms, measured separately. Both copies passed database integrity checks.
This rules out the original different-filesystem benchmark as the explanation
for these seconds-long calls; the live host timing provides the stronger
evidence about where they actually waited.

## Scoped correction

The server previously reused its destructive annotation for every mutation,
including creating a task and adding a new implementation attempt. Additive
operations now declare additive writes, with an exhaustive descriptor test.
Overwrites and removals retain destructive annotations. `init` is conservatively
classified for its supported rebind action, even though ordinary resume only
adds an audit event. No handler, revision check, human-verdict gate, transaction
durability setting or host approval policy is weakened by this change.

The [OpenAI tool reference](https://developers.openai.com/plugins/reference)
distinguishes writes from destructive operations; the
[app-server documentation](https://learn.chatgpt.com/docs/app-server)
describes destructive annotations triggering approval. Client policy remains
authoritative and may also require approval for additive writes. This correction
is not proof that all end-to-end latency is fixed.

## Reproduce safely

Run `.venv/bin/python examples/benchmark.py --samples 20` for an isolated
synthetic scope. For representative existing data, also pass `--source` and
the matching `--project` and `--workstream` IDs. Use `--work-dir` to select a
parent directory on the filesystem being investigated. The benchmark creates
fresh private databases, opens the source read-only, checks the copies'
integrity, and removes its temporary copies. It prints startup separately from
per-operation median/min/max and raw samples. No fixed timing threshold is
asserted in CI, since machine load and storage vary.

The local budget is tens of milliseconds per warm mutation; the provisional
client target is under 100 ms. These are diagnostic targets, not promises to
bypass a client's required approval. Local stdio has no host approval stage.
On this host, subprocess stdio initialization timed out inside the filesystem
sandbox but succeeded after an approved out-of-sandbox run. That separate
sandbox failure must not be counted as normal server latency.

For end-to-end verification, refresh the actual client's tool descriptors and
time legitimate task operations there. Pair monotonic durations and UTC
start/end timestamps with their matching task-audit and approval events. Do
not create junk live tasks, change security hints dishonestly, or disable
approval policy to obtain benchmark numbers. If an approval-policy change is
desired, it is a separate explicit user security choice, not a server fix.
