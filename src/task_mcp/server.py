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


class ArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["artifact", "commit"]
    reference: str


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
            "The user directs the work: explicit user instructions override all workflow "
            "guidance.\n"
            "Call init first for the checkout and branch, and read the notes it returns.\n"
            "Workflows are skills read with get_default_skills: superdevloop, task-signoff, "
            "task-capture, task-design, proposal-review and init.\n"
            'For "go through my ideas", read task-capture.\n'
            "Basic loop: pick up a task (get_next_action), implement it, record_result, "
            "independent review (record_review), then the user's sign-off (signoff_task).\n"
            "Pass the last revision each write returned; no confirming read is needed.\n"
            "Write task specifications using ASD-STE100 sentence and structure principles. "
            "Use short sentences, explicit subjects, active voice, and consistent technical "
            "terms. Put conditions before the behavior they control. State one requirement or "
            "instruction per sentence. Separate context, requirements, procedures, boundaries, "
            "and acceptance criteria. Preserve every exception and unresolved question. Retain "
            "exact technical identifiers and UI labels.\n"
            "This applies when you create or deliberately rewrite a task or group "
            "specification. Write the body as plain labelled Context, Requirements, "
            "Procedures and Boundaries sections, in that order, and omit a section that has "
            "no content. Put acceptance criteria in the acceptance_criteria field."
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

    def tool(annotations: ToolAnnotations):
        """Register a tool described by its docstring, unwrapped into one paragraph."""

        def register(function):
            description = " ".join((function.__doc__ or "").split())
            return server.tool(
                annotations=annotations, structured_output=True, description=description
            )(function)

        return register

    def runtime_identity() -> dict[str, Any]:
        """Startup-frozen runtime identity and the current database schema revision.

        init returns it as its runtime block; no separate tool or audit event is needed.
        """
        return {
            **RUNTIME_IDENTITY,
            "database_schema_revision": store.database_schema_revision(),
        }

    @tool(
        ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    @domain_errors
    def open_task_viewer() -> dict[str, Any]:
        """Start or reuse the local browser viewer and editor and return its private link. It
        outlives this session; stop it in the viewer or with `task-mcp ui --stop`.
        """
        from task_mcp.viewer import launch_viewer

        return launch_viewer(store.path)

    @tool(additive)
    @domain_errors
    def list_projects(limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """List initialized projects with their IDs, names and checkout paths. Listing selects no
        project; call init for the checkout you work in.
        """
        return store.list_projects(limit, offset)

    @tool(editing)
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
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """Call first with an absolute checkout path and branch (workstream_name if detached);
        ready returns cards, notes and runtime, else repeat with a returned choice and
        confirmed=true. workstream_id checks the binding. A new workstream's scope_expression:
        none or a base workstream, then +/-task/group.
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
            include_archived,
        )
        return {**result, "runtime": runtime_identity()}

    @tool(editing)
    @domain_errors
    def set_note(
        kind: Literal["project", "workstream"],
        target_id: str,
        expected_revision: int,
        text: str,
    ) -> dict[str, Any]:
        """Replace a personal, uncommitted project or workstream note (at most 2,000 characters;
        empty clears). Update the workstream note when the live state it describes changes or
        the user directs, rewriting rather than appending. expected_revision is the note's, or
        0.
        """
        return store.set_note(kind, target_id, expected_revision, text)

    @tool(editing)
    @domain_errors
    def archive_workstream(
        workstream_id: str, expected_revision: int, reason: str, archived: bool = True
    ) -> dict[str, Any]:
        """Archive a stale workstream with a reason when the user directs (archived=false
        restores it). Archived workstreams are hidden unless include_archived=true; tasks,
        memberships and history are unchanged. expected_revision is archive.revision, or 0.
        """
        return store.archive_workstream(workstream_id, expected_revision, reason, archived)

    @tool(additive)
    @domain_errors
    def list_workstreams(
        project: str | None = None,
        limit: int = 20,
        offset: int = 0,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """List workstreams, globally or for one project, with task counts. Archived workstreams
        are only counted (archived_hidden) unless include_archived=true.
        """
        return store.list_workstreams(project, limit, offset, include_archived)

    @tool(additive)
    @domain_errors
    def workstream_status(
        workstream_id: str,
        limit: int = 20,
        offset: int = 0,
        include_scope: bool = False,
        include_inactive: bool = False,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """Page one workstream's active cards with counts and its tasks with concerns, awaiting
        sign-off first. Closed tasks are only counted unless include_inactive=true;
        include_scope pages explicit scope; archived workstreams list nothing unless
        include_archived=true.
        """
        return store.workstream_status(
            workstream_id, limit, offset, include_scope, include_inactive, include_archived
        )

    @tool(additive)
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
        public_id: str | None = None,
        kind: Literal["task", "group"] = "task",
    ) -> dict[str, Any]:
        """Create a task or kind=group. workstream_id adds membership; omission leaves it
        in the inbox. group_id requires group_expected_revision. Pass a short public_id:
        a permanent, globally unique ID of at most 40 characters; a conflict needs another
        name. source/user_request record origin.
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
            public_id,
        )

    @tool(additive)
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
        """List active cards in workstream order, or the project baseline. Closed tasks are only
        counted unless include_inactive or a closed state is asked for; state filters by card
        state (ready includes rework); include adds detail groups. state=group lists groups;
        group_id a group's members.
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

    @tool(additive)
    @domain_errors
    def get_tasks(
        ids: list[str],
        specification: bool = False,
        workstream_id: str | None = None,
        attempt_ids: list[str] | None = None,
        include: list[CardInclude] | None = None,
    ) -> dict[str, Any]:
        """Read 1-20 cards (include as list_tasks). specification=true adds full requirements,
        questions, concerns and the specification_etag; attempt_ids (up to 20) add exactly
        those complete attempts in the same call. A parent_group gives only id, title and
        summary: add its ID for the group's body.
        """
        return store.read_tasks(ids, specification, workstream_id, attempt_ids, include)

    @tool(additive)
    @domain_errors
    def list_task_attempts(
        task_id: str,
        workstream_id: str | None = None,
        states: list[str] | None = None,
        current_spec_only: bool = True,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Page a task's attempt summaries, filtered by workstream and state.
        current_spec_only=false adds attempts against older specifications.
        """
        return store.list_task_attempts(
            task_id, workstream_id, states, current_spec_only, limit, cursor
        )

    @tool(additive)
    @domain_errors
    def get_attempt(attempt_id: str) -> dict[str, Any]:
        """Read one complete attempt (proof, review and concerns) with its original task,
        workstream and specification revision.
        """
        return store.get_attempt(attempt_id)

    @tool(editing)
    @domain_errors
    def update_task(
        task_id: str,
        expected_revision: int,
        changes: TaskPatch | None = None,
        specification_etag: str | None = None,
        group_id: str | None = None,
        group_expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Edit a task's title, summary, body or acceptance criteria, keeping memberships and
        proof. Replacing body or criteria needs the current specification_etag; requirement
        changes advance spec_revision. group_id with the group's revision attaches the task to
        a group.
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

    @tool(editing)
    @domain_errors
    def add_to_workstream(
        task_id: str, workstream_id: str, expected_revision: int
    ) -> dict[str, Any]:
        """Add a task to a workstream, keeping its other memberships; a group ID adds the group
        and its live members. A task in any workstream is adopted, but open questions and
        prerequisites still gate its execution.
        """
        return store.compact_call("add_to_workstream", task_id, workstream_id, expected_revision)

    @tool(editing)
    @domain_errors
    def remove_from_workstream(
        task_id: str, workstream_id: str, expected_revision: int
    ) -> dict[str, Any]:
        """Remove a task from this workstream only, keeping other memberships and all proof. A
        group ID removes the group; a member ID excludes just that member. A task left with no
        workstream is in the inbox.
        """
        return store.compact_call(
            "remove_from_workstream", task_id, workstream_id, expected_revision
        )

    @tool(editing)
    @domain_errors
    def set_disposition(
        task_id: str,
        expected_revision: int,
        disposition: Literal["open", "deferred", "dropped"],
        note: str,
        authorization: str | None = None,
    ) -> dict[str, Any]:
        """Defer, drop or reopen a task with a note, keeping memberships, context and proof.
        Reopening a dropped task needs the user's actual instruction as authorization.
        """
        return store.compact_call(
            "set_disposition", task_id, expected_revision, disposition, note, authorization
        )

    @tool(additive)
    @domain_errors
    def add_unresolved(
        task_id: str,
        expected_revision: int,
        text: str,
        handling: Literal["active", "user"] = "active",
    ) -> dict[str, Any]:
        """Add an open question that blocks the task until resolved. Its answer belongs in the
        rewritten specification.
        """
        return store.compact_call("add_unresolved", task_id, expected_revision, text, handling)

    @tool(editing)
    @domain_errors
    def resolve_unresolved(
        task_id: str, expected_revision: int, item_id: str, user_note: str
    ) -> dict[str, Any]:
        """Resolve a settled question, recording the user's decision in user_note. Fold the
        answer into the specification with update_task first, unless it changes nothing.
        """
        return store.compact_call(
            "resolve_unresolved", task_id, expected_revision, item_id, user_note
        )

    @tool(additive)
    @domain_errors
    def add_prerequisite(
        task_id: str,
        expected_revision: int,
        blocked_by_id: str,
        handling: Literal["active", "user"] = "active",
        milestone: Literal["review", "signoff"] = "review",
    ) -> dict[str, Any]:
        """Make a task or group (any project) block this task. milestone=review clears when it,
        or every group member, passes review or is done; use signoff exceptionally, when
        proceeding before the user's verdict would very likely waste work. Deferred or dropped
        blockers never clear.
        """
        return store.compact_call(
            "add_prerequisite", task_id, expected_revision, blocked_by_id, handling, milestone
        )

    @tool(editing)
    @domain_errors
    def remove_prerequisite(
        task_id: str, expected_revision: int, blocked_by_id: str, note: str
    ) -> dict[str, Any]:
        """Remove a blocker link, with a note giving the actual reason, and recalculate the gate.
        An absent link is a no-op. Memberships, specification and proof are unchanged.
        """
        return store.compact_call(
            "remove_prerequisite", task_id, expected_revision, blocked_by_id, note
        )

    @tool(editing)
    @domain_errors
    def decompose_task(
        task_id: str, expected_revision: int, members: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Turn an open task without attempts, questions or a parent into a group and
        create members in its workstreams with its prerequisites atomically. Members
        take title/body/acceptance_criteria and a short unique public_id (at most 40
        characters); any conflict rolls back all members.
        """
        return store.compact_call("decompose_task", task_id, expected_revision, members)

    @tool(editing)
    @domain_errors
    def reorder_tasks(
        workstream_id: str, task_ids: list[str], expected_order_revision: int
    ) -> dict[str, Any]:
        """Reorder a workstream by listing a task-ID prefix; unlisted members follow in their
        existing order. Needs the last workstream_order_revision; changes no membership or
        proof. Order is preference: express a real dependency with add_prerequisite.
        """
        return store.compact_call("reorder_tasks", workstream_id, task_ids, expected_order_revision)

    @tool(additive)
    @domain_errors
    def get_next_action(workstream_id: str, include_archived: bool = False) -> dict[str, Any]:
        """Pick the next implement or review action in workstream order, with the full
        specification, etag, latest rejection and, for review or rework, the applicable
        attempt. Only members with clear gates qualify; null returns waiting counts. Marks
        the task picked for 4h (information only, no lock).
        """
        return store.get_next_action(workstream_id, include_archived)

    @tool(additive)
    @domain_errors
    def record_result(
        task_id: str,
        workstream_id: str,
        expected_revision: int,
        implementer: str,
        summary: str,
        evidence: str,
        artifacts: list[ArtifactInput],
        verification: str,
        specification_etag: str,
        concerns: list[ConcernInput] | None = None,
    ) -> dict[str, Any]:
        """Record finished work with the last revision, specification_etag, actual verification
        and artifacts [{kind: artifact|commit, reference}]; it grants no authority. Optional
        concerns (kind value or design) are worth-doing or approach doubts not fixable without
        changing the task; they never affect gates.
        """
        return store.compact_call(
            "record_result",
            task_id,
            workstream_id,
            expected_revision,
            implementer,
            summary,
            evidence,
            [a.model_dump() for a in artifacts],
            verification,
            specification_etag,
            [c.model_dump() for c in concerns] if concerns is not None else None,
        )

    @tool(editing)
    @domain_errors
    def record_review(
        attempt_id: str,
        expected_revision: int,
        reviewer: str,
        verdict: Literal["pass", "rework"],
        note: str,
        concerns: list[ConcernInput] | None = None,
    ) -> dict[str, Any]:
        """Record an independent reviewer's pass or rework on an attempt. A pass awaits the
        user's sign-off; it never completes a task. Concerns are as in record_result and are
        kept when omitted.
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

    @tool(editing)
    @domain_errors
    def signoff_task(
        task_id: str,
        expected_revision: int,
        attempt_id: str,
        expected_attempt_revision: int,
        decision: Literal["approve", "rework", "revise", "drop"],
        reasons: str | None = None,
    ) -> dict[str, Any]:
        """Record the user's verdict on a reviewed attempt: approve completes it, rework returns
        it to implementation, revise turns the reasons into a question, drop closes it
        unapproved. Rework and revise need reasons. Approving an unreviewed result needs the
        user's explicit say-so, stated in reasons.
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

    @tool(additive)
    @domain_errors
    def list_events(
        project: str | None = None,
        task_id: str | None = None,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        limit: int = 20,
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Page the local audit history for a project or task. Pass the returned through_sequence
        to keep pages stable.
        """
        return store.list_events(
            project, task_id, after_sequence, through_sequence, limit, include_details
        )

    @tool(catalog)
    def get_default_skills(name: str | None = None) -> dict[str, Any]:
        """Read the workflow skill index (names, descriptions, versions and hashes), or one
        complete skill by name.
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
