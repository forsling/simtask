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
from pydantic import BaseModel, ConfigDict, Field

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


class Approval(BaseModel):
    """User authority covering the exact specification, independent of origin."""

    model_config = ConfigDict(extra="forbid")
    basis: Literal["specific", "delegated"]
    note: str = Field(min_length=1)


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
            "Init an explicit checkout/branch and retain its IDs. Continue with returned "
            "revisions; reconcile conflicts. Record only actual user authority, independent "
            "review and informed human verdicts. Full specifications govern work. "
            "Prerequisites clear on current-spec passed/human review by default; "
            "signoff links are rare exceptions for work very likely wasted "
            "without a human verdict. "
            "Use add_prerequisite to link a blocker; remove_prerequisite with the last "
            "dependent revision and an actual decision note to remove a mistaken or obsolete "
            "link. Removal preserves acceptance and proof; an absent link is a no-op. "
            "A satisfied canonical milestone does not prove integration into this checkout."
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

    @server.tool(annotations=catalog, structured_output=True)
    def runtime_info() -> dict[str, Any]:
        """Read startup-frozen runtime identity and the current database schema revision.

        No task state or audit event is written. Compare this identity after
        reconnecting to distinguish an old server process from a stale tool catalog.
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
    ) -> dict[str, Any]:
        """Discover or resume a checkout binding; confirmed actions change setup atomically."""
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
        )
        return {**result, "runtime": runtime_info()}

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def init_project(
        path: str,
        branch: str | None = None,
        workstream_name: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Explicitly initialize a project and default workstream after user confirmation."""
        return store.compact_call("init_project", path, branch, workstream_name, confirmed)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def attach_checkout(project: str, path: str, confirmed: bool = False) -> dict[str, Any]:
        """Attach another canonical checkout path to an existing project."""
        return store.compact_call("attach_checkout", project, path, confirmed)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def init_workstream(
        project: str,
        path: str,
        branch: str | None = None,
        name: str | None = None,
        scope_expression: str = "none",
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Initialize a distinct workstream and explicit initial scope."""
        return store.compact_call(
            "init_workstream", project, path, branch, name, scope_expression, confirmed
        )

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
        workstream_id: str, limit: int = 20, offset: int = 0, include_scope: bool = False
    ) -> dict[str, Any]:
        """Read a paged local queue/counts; include_scope=true pages explicit scope."""
        return store.workstream_status(workstream_id, limit, offset, include_scope)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_groups(project: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """Discover shared groups globally or through a member/scoped project."""
        return store.list_groups(project, limit, offset)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def create_group(
        workstream_id: str,
        title: str,
        body: str = "",
        acceptance_criteria: str = "",
        summary: str | None = None,
    ) -> dict[str, Any]:
        """Create an empty global group and include it in this workstream's scope."""
        return store.compact_call(
            "create_group", workstream_id, title, body, acceptance_criteria, summary
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def add_group_member(
        group_id: str, expected_revision: int, task_id: str, expected_task_revision: int
    ) -> dict[str, Any]:
        """Atomically attach a local task to a shared group with revision checks."""
        return store.compact_call(
            "add_group_member", group_id, expected_revision, task_id, expected_task_revision
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def rebind_workstream(
        workstream_id: str,
        expected_revision: int,
        path: str,
        branch: str | None = None,
        name: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Preserve a workstream's identity/history when its branch or checkout changes."""
        return store.compact_call(
            "rebind_workstream", workstream_id, expected_revision, path, branch, name, confirmed
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def preflight(
        project: str, path: str, branch: str | None = None, workstream_id: str | None = None
    ) -> dict[str, Any]:
        """Verify checkout and branch match a bound workstream before autonomous work."""
        return store.preflight(project, path, branch, workstream_id)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def set_scope(workstream_id: str, expected_revision: int, expression: str) -> dict[str, Any]:
        """Replace scope using auditable set expression: none, a workstream base, +/-task/group."""
        return store.compact_call("set_scope", workstream_id, expected_revision, expression)

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
        scope: Literal["inbox", "workstream"] = "inbox",
        group_id: str | None = None,
        group_expected_revision: int | None = None,
        approval: Approval | None = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        """Persist origin/request; only supplied approval atomically accepts this exact spec.

        Omit approval for pending/design-first work. Approval needs real specific
        or delegated user authority and its supporting instruction, and clears no
        other gates. Group membership requires the group's read revision.
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
            scope,
            group_id,
            group_expected_revision,
            approval.model_dump() if approval is not None else None,
            summary,
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_tasks(
        project: str,
        workstream_id: str | None = None,
        state: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Compact project or workstream queue in one project-level order."""
        return store.list_tasks(project, workstream_id, state, limit, offset)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def get_tasks(
        ids: list[str],
        specification: bool = False,
        workstream_id: str | None = None,
        attempt_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Read 1–20 cards; specification=true gives complete requirements and gates.

        Add up to 20 attempt_ids to retrieve exactly those full proofs in this call.
        Workstream scope filters current execution summaries, never proof provenance.
        Cards carry no specification_etag; full specs do. No preliminary card read is needed.
        """
        return store.read_tasks(ids, specification, workstream_id, attempt_ids)

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

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_group_members(
        group_id: str, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        """Page group member cards in creation order; ordinary group reads show three."""
        return store.list_group_members(group_id, limit, cursor)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def update_task(
        task_id: str,
        expected_revision: int,
        changes: TaskPatch,
        approval: Approval | None = None,
        specification_etag: str | None = None,
    ) -> dict[str, Any]:
        """Save edits and optional exact-scope approval atomically; return compact change flags.

        Body/acceptance_criteria are whole-field replacements: supply the full-spec etag you
        already read or authored. get_tasks(specification=true) supplies it if missing.
        Use returned revisions/tokens; reconcile conflicts. Title/summary need no full read.
        Actual specific/delegated approval accepts the resulting spec; unapproved
        spec changes become pending. Other gates and old-spec proof stay intact.
        Unchanged patches are no-ops unless approval changes; still check revision.
        """
        return store.compact_call(
            "update_task",
            task_id,
            expected_revision,
            changes.model_dump(exclude_unset=True),
            approval.model_dump() if approval is not None else None,
            specification_etag,
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def accept_task(task_id: str, expected_revision: int, approval: Approval) -> dict[str, Any]:
        """Approve the exact current spec using classified actual authority; keep other gates."""
        return store.compact_call("accept_task", task_id, expected_revision, approval.model_dump())

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def withdraw_acceptance(task_id: str, expected_revision: int, note: str) -> dict[str, Any]:
        """Withdraw acceptance with an audited reason, preserving spec and delivery history."""
        return store.compact_call("withdraw_acceptance", task_id, expected_revision, note)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def set_disposition(
        task_id: str,
        expected_revision: int,
        disposition: Literal["open", "deferred", "dropped"],
        note: str,
        authorization: str | None = None,
    ) -> dict[str, Any]:
        """Pause preserving approval; drop revokes it. Revival needs actual authorization."""
        return store.compact_call(
            "set_disposition", task_id, expected_revision, disposition, note, authorization
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def add_unresolved(
        task_id: str,
        expected_revision: int,
        text: str,
        handling: Literal["active", "observer", "user"] = "active",
    ) -> dict[str, Any]:
        """Add a blocking unresolved item; observers submit a nonblocking gate proposal."""
        return store.compact_call("add_unresolved", task_id, expected_revision, text, handling)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def resolve_unresolved(
        task_id: str, expected_revision: int, item_id: str, user_note: str
    ) -> dict[str, Any]:
        """Remove a resolved item after a user decision; acceptance remains valid."""
        return store.compact_call(
            "resolve_unresolved", task_id, expected_revision, item_id, user_note
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def add_prerequisite(
        task_id: str,
        expected_revision: int,
        blocked_by_id: str,
        handling: Literal["active", "observer", "user"] = "active",
        milestone: Literal["review", "signoff"] = "review",
    ) -> dict[str, Any]:
        """Link a canonical task/group in any project, or propose a nonblocking observer gate.

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
        an absent link returns changed=false with the same revision. Acceptance,
        specifications and proof survive. Completed tasks remain immutable.
        The configured actor and note are audited, including no-ops.
        """
        return store.compact_call(
            "remove_prerequisite", task_id, expected_revision, blocked_by_id, note
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def propose_prerequisite(
        task_id: str,
        expected_revision: int,
        title: str,
        body: str = "",
        acceptance_criteria: str = "",
        workstream_id: str | None = None,
        milestone: Literal["review", "signoff"] = "review",
    ) -> dict[str, Any]:
        """Create and link a pending prerequisite in this scope or inbox.

        Default review clears on done or current-spec passed/human_review. Use signoff only
        exceptionally when work would very likely be wasted without the user's verdict.
        Satisfaction is reversible; it neither grants approval nor proves code integration.
        """
        return store.compact_call(
            "propose_prerequisite",
            task_id,
            expected_revision,
            title,
            body,
            acceptance_criteria,
            workstream_id,
            milestone,
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def accept_gate_proposal(proposal_id: str, expected_revision: int) -> dict[str, Any]:
        """Activate an observer gate, checking global task/group cycles for prerequisites."""
        return store.compact_call("accept_gate_proposal", proposal_id, expected_revision)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def dismiss_gate_proposal(
        proposal_id: str, expected_revision: int, note: str
    ) -> dict[str, Any]:
        """Dismiss a pending observer gate with a recorded coordinator decision."""
        return store.compact_call("dismiss_gate_proposal", proposal_id, expected_revision, note)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def decompose_task(
        task_id: str, expected_revision: int, members: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Convert a task in place to a group and atomically create required pending members."""
        return store.compact_call("decompose_task", task_id, expected_revision, members)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def reorder_tasks(
        project: str,
        task_id: str,
        anchor_id: str,
        position: Literal["before", "after"],
        expected_order_revision: int,
        instruction: str,
    ) -> dict[str, Any]:
        """Move one task before/after an anchor with the board's project_order_revision.

        Use only for actual scheduling intent; record its supporting instruction or
        authority. Shared ordering never changes requirements/proof or workstream gates.
        """
        return store.compact_call(
            "reorder_tasks",
            project,
            task_id,
            anchor_id,
            position,
            expected_order_revision,
            instruction,
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def get_next_action(workstream_id: str) -> dict[str, Any]:
        """Read one implement/review action in shared order, without claiming or reordering.

        Full current spec/token and exactly one applicable local proof for review
        (or rework) are included. Autonomous actions require acceptance and clear
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
    ) -> dict[str, Any]:
        """Record factual durable proof, even with gates; this grants no execution authority.

        First check the actual checkout/artifacts and current full specification.
        Supply its etag, concrete artifacts ({kind: artifact|commit, reference: ...}),
        actual verification and context evidence. Acceptance, disposition and blockers
        stay unchanged. The ACK omits proof; retrieve it deliberately with get_tasks.
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
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def record_review(
        attempt_id: str,
        expected_revision: int,
        reviewer: str,
        verdict: Literal["pass", "rework"],
        note: str,
    ) -> dict[str, Any]:
        """Record a declared independent review verdict on an implementation attempt."""
        return store.compact_call(
            "record_review", attempt_id, expected_revision, reviewer, verdict, note
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def human_review(attempt_id: str, expected_revision: int, user_note: str) -> dict[str, Any]:
        """Record an explicit user's review or instruction to skip further independent review."""
        return store.compact_call("human_review", attempt_id, expected_revision, user_note)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def signoff_task(
        task_id: str,
        expected_revision: int,
        attempt_id: str,
        expected_attempt_revision: int,
        decision: Literal["approve", "rework", "revise", "drop", "defer"],
        user_note: str,
        result_judgment: Literal["accepted", "rework", "not_judged"] | None = None,
        result_note: str | None = None,
        specification_question: str | None = None,
    ) -> dict[str, Any]:
        """Record one actual human decision on purpose and result. Approve covers both;
        specific purpose approval may be reused. Direction-only decisions leave human
        technical quality unjudged unless separately supplied with its actual note.
        Revise needs a concrete specification question. Read full proof with get_tasks.
        """
        return store.compact_call(
            "signoff_task",
            task_id,
            expected_revision,
            decision,
            user_note,
            attempt_id,
            expected_attempt_revision,
            result_judgment,
            result_note,
            specification_question,
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

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def export_workstream(
        workstream_id: str,
        include_closed: bool = True,
        format: Literal["markdown", "legacy"] = "markdown",
    ) -> dict[str, Any]:
        """Return a readable v2 Markdown snapshot, or legacy v1 with embedded JSON.

        Both include a content hash. Closed means done/dropped, not deferred.
        Export returns text only; it never saves files or synchronizes edits.
        """
        return store.export_workstream(workstream_id, include_closed, format)

    @server.tool(annotations=catalog, structured_output=True)
    def get_default_skills(name: str | None = None) -> dict[str, Any]:
        """Read a skill index, or one named workflow: feature-capture for briefs,
        feature-design for design discussions; superdevloop/signoff for delivery."""
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
