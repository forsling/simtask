"""MCP primitives for the local task service."""

import argparse
import json
import os
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from task_mcp.reference import default_skills
from task_mcp.runtime import RUNTIME_IDENTITY
from task_mcp.store import Store, TaskError, default_database
from task_mcp.tracing import TraceCollector, TraceConfig, default_trace_directory


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    body: str | None = None
    acceptance_criteria: str | None = None
    summary: str | None = None


CardInclude = Literal["blockers", "attempt", "concerns", "workstreams", "ids"]


class ConcernInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["value", "design"]
    text: str


def domain_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except TaskError as exc:
            raise ToolError(str(exc)) from None

    return wrapped


def create_server(
    store: Store, *, tracing: bool = True, trace_config: TraceConfig | None = None
) -> MCPServer:
    collector = (
        TraceCollector(trace_config or TraceConfig(default_trace_directory(store.path)))
        if tracing
        else None
    )

    @asynccontextmanager
    async def lifespan(server):
        if collector:
            collector.start()
        try:
            yield {}
        finally:
            if collector:
                collector.close()

    server = MCPServer(
        "task-mcp",
        version=RUNTIME_IDENTITY["package_version"],
        instructions=(
            "Explicit user instructions override workflow guidance. Init the target "
            "checkout/branch; retain its IDs and last returned revisions. Workstreams have "
            "nonexclusive memberships and independent order; inclusion starts no implementation. "
            "Fetch get_default_skills(name=...) on demand: init for setup, feature-capture for "
            "'add a design task', feature-design for 'let's design X'/'review design tasks', "
            "proposal-review for ordinary proposals/questions, superdevloop for implementation "
            "and independent review, signoff for reviewed-result walkthroughs and user verdicts."
        ),
        lifespan=lifespan,
    )
    if collector:
        server.middleware.insert(0, collector.middleware)
    # Audited reads and append-only writes both change local state. A revision or
    # updated_at bump alone does not erase business content, so append-only tools
    # can truthfully declare destructive_hint=False.
    additive = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
    )
    # Existing content, bindings, gates, order, or verdicts may be replaced here.
    editing = ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
    )
    catalog = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    )

    def runtime_identity() -> dict[str, Any]:
        """Startup-frozen runtime identity and the current database schema revision.

        init returns it as its runtime block; no separate tool or audit event is needed.
        """
        return {
            **RUNTIME_IDENTITY,
            "database_schema_revision": store.database_schema_revision(),
        }

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    @domain_errors
    def open_task_viewer() -> dict[str, Any]:
        """Explicitly start/reuse a protected loopback editor; return its private browser link.

        The companion outlives this stdio session. Stop it in the UI or with
        task-mcp ui --stop. This adds a local listener to whole-server trust.
        """
        from task_mcp.viewer import launch_viewer

        return launch_viewer(store.path)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_projects(limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """Discover initialized project IDs, names and canonical paths."""
        return store.list_projects(limit, offset)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def init(
        path: str,
        branch: str | None = None,
        workstream_name: str | None = None,
        action: Literal[
            "create_project", "new_workstream", "attach_workstream", "rebind_workstream"
        ]
        | None = None,
        project: str | None = None,
        workstream_id: str | None = None,
        scope_expression: str = "none",
        expected_revision: int | None = None,
        confirmed: bool = False,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        """Discover or resume a checkout binding; confirmed actions change setup atomically.

        Actions: create_project (new project and workstream), new_workstream (another
        branch of an attached checkout), attach_workstream (another checkout of `project`),
        rebind_workstream (move workstream_id to this path/branch; expected_revision).
        scope_expression seeds a new workstream: none, or a workstream base, +/-task/group.
        Passing workstream_id checks that it is bound to this checkout/branch; any
        mismatch returns state=mismatch with the requested binding, bound_workstream and
        choices; an unknown workstream_id is unknown_workstream. A ready queue shows the
        first ten active slim cards (as list_tasks); queue_hidden counts done/deferred/
        dropped unless include_inactive=true. runtime is the server identity/schema revision.
        """
        result = store.init(
            path,
            branch,
            workstream_name,
            action,
            project,
            workstream_id,
            scope_expression,
            expected_revision,
            confirmed,
            include_inactive,
        )
        return {**result, "runtime": runtime_identity()}

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_workstreams(
        project: str | None = None, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        """List workstreams globally or by project with task counts, not agent liveness."""
        return store.list_workstreams(project, limit, offset)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def workstream_status(
        workstream_id: str,
        limit: int = 20,
        offset: int = 0,
        include_scope: bool = False,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        """Read a paged list of active slim cards and current concern refs, with counts.

        Done/deferred/dropped tasks are counted in hidden (and status.counts) but not listed,
        here or in concern_tasks, unless include_inactive=true. concern_tasks is paged at
        limit/offset, prioritizing awaiting sign-off. include_scope=true also pages explicit scope.
        """
        return store.workstream_status(
            workstream_id, limit, offset, include_scope, include_inactive
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def create_task(
        project: str,
        title: str,
        body: str = "",
        acceptance_criteria: str = "",
        source: Literal["agent", "user", "unknown"] = "agent",
        user_request: str = "",
        workstream_id: str | None = None,
        group_id: str | None = None,
        group_expected_revision: int | None = None,
        summary: str | None = None,
        kind: Literal["task", "group"] = "task",
    ) -> dict[str, Any]:
        """Create with direct membership when workstream_id is supplied.

        Include agreed work; open design questions remain blocking gates.
        group_id creates a member; membership follows dynamic inclusion and exclusions.
        kind=group creates an empty shared group included in workstream_id's scope.
        """
        return store.compact_call(
            "create_task",
            project,
            title,
            body,
            acceptance_criteria,
            source,
            user_request,
            workstream_id,
            group_id,
            group_expected_revision,
            summary,
            kind,
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_tasks(
        project: str | None = None,
        workstream_id: str | None = None,
        state: str | None = None,
        limit: int = 20,
        offset: int = 0,
        include_inactive: bool = False,
        include: list[CardInclude] | None = None,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        """Active work as slim cards in workstream order, or the project baseline.

        Done/deferred/dropped tasks are omitted and counted in hidden; include_inactive=true
        (or state=done|deferred|dropped) lists them. Card: id, title, summary, state
        (ready|rework|blocked|question|review|signoff|inbox, or the closed status),
        revision, position (workstream order), blockers (unsatisfied prerequisite IDs),
        question_count, concern_count, rejected (a rejection awaits a new result);
        empty/zero/false fields are omitted. state filters by these words or the
        older view names (prerequisites, unresolved_items); ready includes rework;
        other values are rejected; any state filter omits hidden. include adds groups:
        blockers (prerequisite title/state/satisfied, gate diagnostics), attempt
        (current result, implementer, summary, review state, latest_rejection), concerns
        (texts, kind, author), workstreams (memberships and positions), ids (project,
        spec revision, status, group and other bookkeeping). state=group lists shared groups
        (in workstream scope, related to project, or all) with progress; group_id lists that
        group's members (project optional). project is otherwise required.
        """
        return store.list_tasks(
            project,
            workstream_id,
            state,
            limit,
            offset,
            include_inactive,
            include,
            group_id=group_id,
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def get_tasks(
        ids: list[str],
        specification: bool = False,
        workstream_id: str | None = None,
        attempt_ids: list[str] | None = None,
        include: list[CardInclude] | None = None,
    ) -> dict[str, Any]:
        """Read 1–20 slim cards (as list_tasks, with the same include groups);
        specification=true gives complete requirements and gates.

        Add up to 20 attempt_ids to retrieve exactly those full proofs in this call.
        Current concern prose shares the three-attempt summary window; counts/has_more
        expose omitted concerns. Chosen proof carries its concerns once in this response.
        Workstream scope filters current execution summaries, never proof provenance.
        Cards carry no specification_etag; full specs do. No preliminary card read is needed.
        """
        return store.read_tasks(ids, specification, workstream_id, attempt_ids, include)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_task_attempts(
        task_id: str,
        workstream_id: str | None = None,
        states: list[str] | None = None,
        current_spec_only: bool = True,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Page attempt summaries with state/workstream filters; history is explicit."""
        return store.list_task_attempts(
            task_id, workstream_id, states, current_spec_only, limit, cursor
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def get_attempt(attempt_id: str) -> dict[str, Any]:
        """Read one complete proof/review with original task/workstream/spec provenance."""
        return store.get_attempt(attempt_id)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def update_task(
        task_id: str,
        expected_revision: int,
        changes: TaskPatch | None = None,
        specification_etag: str | None = None,
        group_id: str | None = None,
        group_expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Save edits while retaining memberships and all historical proof.

        Whole body/criteria replacements require the current full specification_etag.
        Actual requirement changes advance spec_revision; older proof stays historical.
        No-op patches retain revisions; every call checks expected_revision.
        group_id (with group_expected_revision) attaches this task to a shared group.
        """
        return store.compact_call(
            "update_task",
            task_id,
            expected_revision,
            changes.model_dump(exclude_unset=True) if changes is not None else {},
            specification_etag,
            group_id,
            group_expected_revision,
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def add_to_workstream(
        task_id: str, workstream_id: str, expected_revision: int
    ) -> dict[str, Any]:
        """Add to this workstream, retaining all others; starts no implementation.

        A group ID includes the group, and so its live members, in this workstream.
        """
        return store.compact_call("add_to_workstream", task_id, workstream_id, expected_revision)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def remove_from_workstream(
        task_id: str, workstream_id: str, expected_revision: int
    ) -> dict[str, Any]:
        """Remove only this named workstream, retaining other memberships and all proof.

        A group ID removes the group's inclusion; a member ID excludes just that member.
        """
        return store.compact_call(
            "remove_from_workstream", task_id, workstream_id, expected_revision
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def set_disposition(
        task_id: str,
        expected_revision: int,
        disposition: Literal["open", "deferred", "dropped"],
        note: str,
        authorization: str | None = None,
    ) -> dict[str, Any]:
        """Pause/drop preserving memberships and proof. Revival needs actual authorization."""
        return store.compact_call(
            "set_disposition", task_id, expected_revision, disposition, note, authorization
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def add_unresolved(
        task_id: str,
        expected_revision: int,
        text: str,
        handling: Literal["active", "user"] = "active",
    ) -> dict[str, Any]:
        """Add a blocking unresolved item (a question awaiting a decision)."""
        return store.compact_call("add_unresolved", task_id, expected_revision, text, handling)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def resolve_unresolved(
        task_id: str, expected_revision: int, item_id: str, user_note: str
    ) -> dict[str, Any]:
        """Remove a resolved item after a user decision; workstream membership stays unchanged."""
        return store.compact_call(
            "resolve_unresolved", task_id, expected_revision, item_id, user_note
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def add_prerequisite(
        task_id: str,
        expected_revision: int,
        blocked_by_id: str,
        handling: Literal["active", "user"] = "active",
        milestone: Literal["review", "signoff"] = "review",
    ) -> dict[str, Any]:
        """Link a canonical task/group in any project as a blocking prerequisite.

        Default review clears on done or current-spec passed/human_review (every group member).
        Use signoff only exceptionally when proceeding before the user's verdict would very
        likely waste work. Dropped/deferred blockers stay unsatisfied; rework/spec changes can
        block review links again. A different milestone on an existing link is rejected.
        The link changes only the dependent gate, never workstream scope or remote proof.
        """
        return store.compact_call(
            "add_prerequisite", task_id, expected_revision, blocked_by_id, handling, milestone
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def remove_prerequisite(
        task_id: str, expected_revision: int, blocked_by_id: str, note: str
    ) -> dict[str, Any]:
        """Remove a prerequisite link with an actual decision note; recalculate the gate.

        Use the dependent task's last revision. A removed link advances it once;
        an absent link returns changed=false with the same revision. Workstream membership,
        specifications and proof survive. Completed tasks remain immutable.
        The configured actor and note are audited, including no-ops.
        """
        return store.compact_call(
            "remove_prerequisite", task_id, expected_revision, blocked_by_id, note
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def decompose_task(
        task_id: str, expected_revision: int, members: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Convert to a group and create required members in the parent scopes atomically."""
        return store.compact_call("decompose_task", task_id, expected_revision, members)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def reorder_tasks(
        workstream_id: str, task_ids: list[str], expected_order_revision: int
    ) -> dict[str, Any]:
        """Set the named workstream's ordered task-ID prefix in one atomic call.

        Unlisted members follow in their previous relative order; membership is unchanged.
        Use workstream_order_revision from the board/action/init/status. Empty or already
        matching requests are no-ops. Duplicate/nonmember IDs and stale revisions fail.
        Specifications, proof, completion and every other workstream list are unchanged.
        """
        return store.compact_call("reorder_tasks", workstream_id, task_ids, expected_order_revision)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def get_next_action(workstream_id: str) -> dict[str, Any]:
        """Read one implement/review action in workstream order.

        Full current spec/token and exactly one applicable local proof for review
        (or rework) are included. Autonomous actions require workstream membership and clear
        gates; passed/human_review waits for the user. Null gives bounded counts.
        Verify the actual checkout/artifacts before trusting recorded proof.
        """
        return store.get_next_action(workstream_id)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def record_result(
        task_id: str,
        workstream_id: str,
        expected_revision: int,
        implementer: str,
        summary: str,
        evidence: str,
        artifacts: list[dict[str, str]],
        verification: str,
        specification_etag: str,
        concerns: list[ConcernInput] | None = None,
    ) -> dict[str, Any]:
        """Record factual durable proof, even with gates; this grants no execution authority.

        First check the actual checkout/artifacts and current full specification.
        Supply its etag, concrete artifacts ({kind: artifact|commit, reference: ...}),
        actual verification and context evidence. Workstream membership, disposition and blockers
        stay unchanged. Optional concerns ({kind: value|design, text: ...}) are doubts
        that cannot be fixed without changing what the task says; they never affect gates.
        The ACK omits proof/concern prose; retrieve it deliberately with get_tasks.
        """
        return store.compact_call(
            "record_result",
            task_id,
            workstream_id,
            expected_revision,
            implementer,
            summary,
            evidence,
            artifacts,
            verification,
            specification_etag,
            [c.model_dump() for c in concerns] if concerns is not None else None,
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def record_review(
        attempt_id: str,
        expected_revision: int,
        reviewer: str,
        verdict: Literal["pass", "rework"],
        note: str,
        concerns: list[ConcernInput] | None = None,
    ) -> dict[str, Any]:
        """Record a declared independent review verdict on an implementation attempt.

        Optional concerns ({kind: value|design, text: ...}) are doubts that cannot be
        fixed without changing what the task says. They never affect gates or verdicts.
        Omission preserves existing concerns, including the implementer's contribution.
        """
        return store.compact_call(
            "record_review",
            attempt_id,
            expected_revision,
            reviewer,
            verdict,
            note,
            [c.model_dump() for c in concerns] if concerns is not None else None,
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def signoff_task(
        task_id: str,
        expected_revision: int,
        attempt_id: str,
        expected_attempt_revision: int,
        decision: Literal["approve", "rework", "revise", "drop"],
        reasons: str | None = None,
    ) -> dict[str, Any]:
        """Record the actual user's verdict and reasons on an exact reviewed result.
        Approve completes; rework returns to implementation; revise opens a design
        question with the reasons; drop closes without approval. Reasons are required
        for rework/revise and optional for approve/drop. Only when the user explicitly
        approves may approve accept a current-spec result awaiting independent review;
        its reasons must say so. Read full proof with get_tasks.
        """
        return store.compact_call(
            "signoff_task",
            task_id,
            expected_revision,
            decision,
            reasons,
            attempt_id,
            expected_attempt_revision,
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_events(
        project: str | None = None,
        task_id: str | None = None,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        limit: int = 20,
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Read local audit history. Pass returned through_sequence for stable pagination."""
        return store.list_events(
            project, task_id, after_sequence, through_sequence, limit, include_details
        )

    @server.tool(annotations=catalog, structured_output=True)
    def get_default_skills(name: str | None = None) -> dict[str, Any]:
        """Read the index or one skill: feature-capture for 'add a design task',
        feature-design for 'let's design X'/'review design tasks', superdevloop for
        workstream Build work/review and signoff for the user's Value/Design/Build verdict.
        Explicit user instructions override the reference guidance.
        """
        return default_skills(name)

    return server


def main():
    parser = argparse.ArgumentParser(description="Local Task MCP v1 server (stdio).")
    parser.add_argument("command", nargs="?", choices=("ui", "trace-report"))
    parser.add_argument("--stop", action="store_true", help="Stop the explicit local viewer")
    parser.add_argument("--db", type=Path, default=default_database())
    parser.add_argument("--actor", default=os.environ.get("TASK_MCP_ACTOR", "local-agent"))
    parser.add_argument("--export-workstream", metavar="WORKSTREAM_ID")
    parser.add_argument("--export-format", choices=("markdown", "legacy"))
    parser.add_argument("--exclude-closed", action="store_true", help="Omit done/dropped tasks")
    parser.add_argument(
        "--no-trace", action="store_true", help="Disable automatic MCP usage collection"
    )
    parser.add_argument(
        "--trace-dir", type=Path, help="Private usage trace directory (default: <database>.traces)"
    )
    parser.add_argument("--trace-max-age-days", type=float, default=30)
    parser.add_argument("--trace-max-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--trace-payload-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--since", help="Trace report start time, including timezone")
    parser.add_argument("--until", help="Trace report end time, including timezone")
    parser.add_argument("--connection", help="Trace report connection ID")
    parser.add_argument("--tool", help="Trace report tool or MCP method")
    parser.add_argument("--entity", help="Trace report explicit entity reference")
    parser.add_argument("--json", action="store_true", help="Machine-readable trace report")
    parser.add_argument(
        "--limit", type=int, default=20, help="Trace report detail rows (default: 20)"
    )
    parser.add_argument("--offset", type=int, default=0, help="Trace report detail offset")
    parser.add_argument("--all", action="store_true", help="Include all trace report details")
    args = parser.parse_args()
    trace_directory = args.trace_dir or default_trace_directory(args.db)
    if args.command == "trace-report":
        from task_mcp.trace_report import analyze, page_report, render

        try:
            report = analyze(
                trace_directory,
                since=args.since,
                until=args.until,
                connection=args.connection,
                tool=args.tool,
                entity=args.entity,
            )
            report = page_report(report, args.limit, args.offset, args.all)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(
            json.dumps(report, indent=2) if args.json else render(report),
            end="\n" if args.json else "",
        )
        return
    if args.command == "ui":
        from task_mcp.viewer import launch_viewer, stop_viewer

        if args.export_workstream or args.export_format or args.exclude_closed:
            parser.error("ui cannot be combined with export options")
        if args.stop:
            print("Viewer stopped." if stop_viewer(args.db) else "Viewer is not running.")
        else:
            print(launch_viewer(args.db)["url"])
        return
    if args.stop:
        parser.error("--stop requires ui")
    if not args.export_workstream and (args.export_format or args.exclude_closed):
        parser.error("--export-format and --exclude-closed require --export-workstream")
    store = Store(args.db, args.actor)
    if args.export_workstream:
        print(
            store.export_workstream(
                args.export_workstream,
                include_closed=not args.exclude_closed,
                format=args.export_format or "markdown",
            )["content"],
            end="",
        )
    else:
        try:
            trace_config = TraceConfig(
                trace_directory,
                max_age_days=args.trace_max_age_days,
                max_bytes=args.trace_max_bytes,
                payload_bytes=args.trace_payload_bytes,
            )
        except ValueError as exc:
            parser.error(str(exc))
        create_server(
            store,
            tracing=not args.no_trace and os.environ.get("TASK_MCP_TRACE", "1") != "0",
            trace_config=trace_config,
        ).run(transport="stdio")
