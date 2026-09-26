"""MCP primitives for the local task service."""

import argparse
import os
from functools import wraps
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from task_mcp.reference import default_skills
from task_mcp.store import Store, TaskError, default_database


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    body: str | None = None
    acceptance_criteria: str | None = None


def domain_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except TaskError as exc:
            raise ToolError(str(exc)) from None

    return wrapped


def create_server(store: Store) -> MCPServer:
    server = MCPServer(
        "task-mcp",
        version="1.0.0",
        instructions=(
            "Task MCP v1 is opt-in. Call init with an explicit target checkout and branch "
            "or named workstream for each task-using session. Exact bindings resume without "
            "confirmation; confirm an explicit action to create, attach or rebind. "
            "A session may retain multiple returned project/workstream IDs. "
            "Tasks have current user acceptance, unresolved items, prerequisites and scoped "
            "attempts. get_next_task selects without claiming. A reviewer other than the "
            "implementer records ordinary review; human_review requires an actual user "
            "instruction. signoff_task requires the user's informed verdict. Actor labels and "
            "human assertions are not authenticated. Reads append local audit events. "
            "get_default_skills returns the canonical reference workflows."
        ),
    )
    additive = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
    )
    editing = ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
    )
    catalog = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_projects(limit: int = 50, offset: int = 0) -> dict[str, Any]:
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
        return store.init(
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

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def init_project(
        path: str,
        branch: str | None = None,
        workstream_name: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Explicitly initialize a project and default workstream after user confirmation."""
        return store.init_project(path, branch, workstream_name, confirmed)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def attach_checkout(project: str, path: str, confirmed: bool = False) -> dict[str, Any]:
        """Attach another canonical checkout path to an existing project."""
        return store.attach_checkout(project, path, confirmed)

    @server.tool(annotations=editing, structured_output=True)
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
        return store.init_workstream(project, path, branch, name, scope_expression, confirmed)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_workstreams(
        project: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """List workstreams globally or by project with task counts, not agent liveness."""
        return store.list_workstreams(project, limit, offset)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def workstream_status(workstream_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Inspect one workstream's scoped queue and counts without initializing or rebinding."""
        return store.workstream_status(workstream_id, limit, offset)

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
        return store.rebind_workstream(
            workstream_id, expected_revision, path, branch, name, confirmed
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
        return store.set_scope(workstream_id, expected_revision, expression)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def create_task(
        project: str,
        title: str,
        body: str = "",
        acceptance_criteria: str = "",
        source: Literal["agent", "user"] = "agent",
        user_request: str = "",
        workstream_id: str | None = None,
        scope: Literal["inbox", "workstream"] = "inbox",
        group_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a pending proposal, or record a directly requested accepted task."""
        return store.create_task(
            project,
            title,
            body,
            acceptance_criteria,
            source,
            user_request,
            workstream_id,
            scope,
            group_id,
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_tasks(
        project: str,
        workstream_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Compact project or workstream queue in one project-level order."""
        return store.list_tasks(project, workstream_id, state, limit, offset)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def get_tasks(ids: list[str]) -> dict[str, Any]:
        """Fetch 1–20 full task specifications, gates, attempts and revisions."""
        return store.get_tasks(ids)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def update_task(task_id: str, expected_revision: int, changes: TaskPatch) -> dict[str, Any]:
        """Edit specification fields. Material changes invalidate task acceptance."""
        return store.update_task(task_id, expected_revision, changes.model_dump(exclude_unset=True))

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def accept_task(task_id: str, expected_revision: int, user_note: str) -> dict[str, Any]:
        """Record explicit user acceptance of the current specification revision."""
        return store.accept_task(task_id, expected_revision, user_note)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def set_disposition(
        task_id: str,
        expected_revision: int,
        disposition: Literal["open", "deferred", "dropped"],
        note: str,
    ) -> dict[str, Any]:
        """Defer, resume, or drop a task with a reason; preserve its specification."""
        return store.set_disposition(task_id, expected_revision, disposition, note)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def add_unresolved(
        task_id: str,
        expected_revision: int,
        text: str,
        handling: Literal["active", "observer", "user"] = "active",
    ) -> dict[str, Any]:
        """Add a blocking unresolved item; observers submit a nonblocking gate proposal."""
        return store.add_unresolved(task_id, expected_revision, text, handling)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def resolve_unresolved(
        task_id: str, expected_revision: int, item_id: str, user_note: str
    ) -> dict[str, Any]:
        """Remove a resolved item after a user decision; acceptance remains valid."""
        return store.resolve_unresolved(task_id, expected_revision, item_id, user_note)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def add_prerequisite(
        task_id: str,
        expected_revision: int,
        blocked_by_id: str,
        handling: Literal["active", "observer", "user"] = "active",
    ) -> dict[str, Any]:
        """Link an existing task prerequisite, or submit an observer gate proposal."""
        return store.add_prerequisite(task_id, expected_revision, blocked_by_id, handling)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def propose_prerequisite(
        task_id: str,
        expected_revision: int,
        title: str,
        body: str = "",
        acceptance_criteria: str = "",
        workstream_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically create and link a pending prerequisite in this scope or inbox."""
        return store.propose_prerequisite(
            task_id, expected_revision, title, body, acceptance_criteria, workstream_id
        )

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def accept_gate_proposal(proposal_id: str, expected_revision: int) -> dict[str, Any]:
        """Activate an observer's proposed unresolved or prerequisite gate."""
        return store.accept_gate_proposal(proposal_id, expected_revision)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def dismiss_gate_proposal(
        proposal_id: str, expected_revision: int, note: str
    ) -> dict[str, Any]:
        """Dismiss a pending observer gate with a recorded coordinator decision."""
        return store.dismiss_gate_proposal(proposal_id, expected_revision, note)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def decompose_task(
        task_id: str, expected_revision: int, members: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Convert a task in place to a group and atomically create required pending members."""
        return store.decompose_task(task_id, expected_revision, members)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def reorder_tasks(
        project: str, ordered_ids: list[str], expected_order: list[str]
    ) -> dict[str, Any]:
        """Set project order using the previously read full order as a concurrency check."""
        return store.reorder_tasks(project, ordered_ids, expected_order)

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def get_next_task(workstream_id: str) -> dict[str, Any]:
        """Select first eligible task with full context, without creating an attempt."""
        return store.get_next_task(workstream_id)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def record_result(
        task_id: str,
        workstream_id: str,
        expected_revision: int,
        implementer: str,
        summary: str,
        evidence: str,
    ) -> dict[str, Any]:
        """Implementer atomically records a durable result and creates an attempt for review."""
        return store.record_result(
            task_id, workstream_id, expected_revision, implementer, summary, evidence
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
        return store.record_review(attempt_id, expected_revision, reviewer, verdict, note)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def human_review(attempt_id: str, expected_revision: int, user_note: str) -> dict[str, Any]:
        """Record an explicit user's review or instruction to skip further independent review."""
        return store.human_review(attempt_id, expected_revision, user_note)

    @server.tool(annotations=editing, structured_output=True)
    @domain_errors
    def signoff_task(
        task_id: str,
        expected_revision: int,
        attempt_id: str,
        verdict: Literal["approve", "reject"],
        user_note: str,
        rejection: Literal["rework", "revise"] = "rework",
    ) -> dict[str, Any]:
        """Record informed human verdict; rejection chooses rework or specification revision."""
        return store.signoff_task(
            task_id, expected_revision, verdict, user_note, attempt_id, rejection
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def list_events(
        project: str | None = None,
        task_id: str | None = None,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        limit: int = 50,
        include_details: bool = False,
    ) -> dict[str, Any]:
        """Read local audit history. Pass returned through_sequence for stable pagination."""
        return store.list_events(
            project, task_id, after_sequence, through_sequence, limit, include_details
        )

    @server.tool(annotations=additive, structured_output=True)
    @domain_errors
    def export_workstream(workstream_id: str, include_closed: bool = True) -> dict[str, Any]:
        """Return a deterministic, human-readable, machine-parseable v1 workstream export."""
        return store.export_workstream(workstream_id, include_closed)

    @server.tool(annotations=catalog, structured_output=True)
    def get_default_skills() -> dict[str, Any]:
        """Return exact canonical packaged reference skill files with version and SHA-256."""
        return default_skills()

    return server


def main():
    parser = argparse.ArgumentParser(description="Local Task MCP v1 server (stdio).")
    parser.add_argument("--db", type=Path, default=default_database())
    parser.add_argument("--actor", default=os.environ.get("TASK_MCP_ACTOR", "local-agent"))
    parser.add_argument("--export-workstream", metavar="WORKSTREAM_ID")
    args = parser.parse_args()
    store = Store(args.db, args.actor)
    if args.export_workstream:
        print(store.export_workstream(args.export_workstream)["content"])
    else:
        create_server(store).run(transport="stdio")
