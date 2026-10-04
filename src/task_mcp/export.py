"""Human-readable workstream snapshots. Rendering never reads or writes state."""

import html
import re
from collections import Counter

FORMAT = "task-mcp/v5"
VIEWS = {
    "ready": "Ready",
    "inbox": "Inbox",
    "unresolved_items": "Blocked: unresolved questions",
    "prerequisites": "Blocked: prerequisites",
    "review": "Awaiting review",
    "signoff": "Awaiting sign-off",
    "done": "Done",
    "deferred": "Deferred",
    "dropped": "Dropped",
}


def _inline(value):
    """Keep stored names on one line without allowing Markdown/HTML structure."""
    text = html.escape(" ".join(str(value).split()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)


def _section(lines, title, text, level=4):
    lines.extend([f"{'#' * level} {title}", ""])
    # Quoting contains stored headings and code fences within their field.
    # Preserve source prose/code exactly; Markdown viewers must disable raw HTML
    # for untrusted content. Escaping HTML here would corrupt fenced code samples.
    lines.extend(
        [("> " + line) if line else ">" for line in text.splitlines()]
        if text.strip()
        else ["Not provided."]
    )
    lines.append("")


def render_markdown(project, workstream, tasks, groups, scoped_count, include_closed):
    """Render a consistent, already-selected snapshot in project task order."""
    lines = [
        "# Task MCP workstream export",
        "",
        f"Format: {FORMAT}",
        "",
        "Snapshot only. Editing this document does not update Task MCP.",
        "",
        f"- Project: {_inline(project['name'])} (`{project['id']}`)",
        f"- Project path: {_inline(project['canonical_path'])}",
        f"- Workstream: {_inline(workstream['name'])} (`{workstream['id']}`)",
        f"- Checkout: {_inline(workstream['checkout_path'] or '(unbound)')}",
        f"- Branch: {_inline(workstream['branch'] or '(named workstream; no branch)')}",
        f"- Scope revision: {workstream['revision']}",
        f"- Closed tasks: {'included' if include_closed else 'omitted (done and dropped)'}",
        f"- Exported tasks: {len(tasks)} of {scoped_count} in local scope",
        "",
        "## Overview",
        "",
        "Workflow views match this workstream's queue. Counts cover exported tasks only.",
        "Deferred tasks remain visible when closed tasks are omitted.",
        "",
    ]
    counts = Counter(task["view"] for task in tasks)
    if tasks:
        lines.extend(f"- {label}: {counts[view]}" for view, label in VIEWS.items() if counts[view])
        lines.extend(["", "| Workflow | Task | ID |", "| --- | --- | --- |"])
        lines.extend(
            f"| {VIEWS[task['view']]} | {_inline(task['title'])} | `{task['id']}` |"
            for task in tasks
        )
    else:
        lines.append("No tasks match this export." if scoped_count else "No tasks in local scope.")
    lines.append("")

    if groups:
        lines.extend(
            [
                "## Referenced groups",
                "",
                "Progress covers all repositories, including members outside this local scope",
                "or hidden by the closed-task filter. An empty local queue does not mean the",
                "whole group is complete. Only local scoped task details appear below.",
                "",
            ]
        )
        for group in groups:
            progress = group["progress"]
            lines.extend(
                [
                    f"### {_inline(group['title'])}",
                    "",
                    f"- ID: `{group['id']}`; revision: {group['revision']}",
                    f"- Reference: {group['reference']}",
                    f"- Global progress: {progress['done']}/{progress['total']} done; "
                    + ("complete" if group["complete"] else "incomplete"),
                    f"- Members in this project: {group['project_member_count']}",
                    f"- Members in local scope: {group['scoped_member_count']}",
                    f"- Members in this export: {group['exported_member_count']}",
                    "",
                ]
            )
            if group.get("summary") is not None:
                _section(
                    lines,
                    "Descriptive summary (stale)"
                    if group["summary_stale"]
                    else "Descriptive summary",
                    group["summary"],
                )
            _section(lines, "Group context", group["body"])
            _section(lines, "Group acceptance criteria", group["acceptance_criteria"])

    lines.extend(["## Tasks", ""])
    if not tasks:
        lines.extend(["No task details to export.", ""])
    for task in tasks:
        lines.extend(
            [
                f"### {_inline(task['title'])}",
                "",
                f"- ID: `{task['id']}`",
                f"- Workflow: {VIEWS[task['view']]} ({task['view']})",
                f"- Stored disposition: {task['status']}",
                f"- Revision: {task['revision']}; specification: {task['spec_revision']}",
                f"- Owning queue: {task['queue_workstream_id'] or 'inbox'}",
                f"- Request origin: {task['source']}",
                f"- Updated: {task['updated_at']}",
            ]
        )
        if parent := task.get("parent_group"):
            lines.append(f"- Group: {_inline(parent['title'])} (`{parent['id']}`)")
        if task["selected_attempt_id"]:
            lines.append(f"- Selected signed-off attempt: `{task['selected_attempt_id']}`")
        lines.append("")
        if task.get("summary") is not None:
            _section(
                lines,
                "Descriptive summary (stale)" if task["summary_stale"] else "Descriptive summary",
                task["summary"],
            )
        if rejection := task.get("latest_rejection"):
            lines.extend(
                [
                    "#### Latest rejection",
                    "",
                    f"- Source: {rejection['source']}; verdict: {rejection['verdict']}",
                    f"- Attempt: `{rejection['attempt_id']}`; "
                    f"workstream: `{rejection['workstream_id']}`; "
                    f"specification: {rejection['spec_revision']}",
                    f"- Recorded: {rejection['timestamp']}; decision: {rejection['decision_ref']}",
                    "",
                ]
            )
            _section(lines, "Rejection reasons", rejection["reasons"] or "", level=5)
        if decisions := task.get("signoff_decisions"):
            lines.extend(["#### Sign-off history", ""])
            for decision in decisions:
                lines.extend(
                    [
                        f"- Verdict: {decision['decision']}; "
                        f"disposition: {decision['disposition']}; "
                        f"attempt: `{decision['attempt_id']}`; recorded: {decision['timestamp']}",
                        "",
                    ]
                )
                _section(
                    lines,
                    "Reasons",
                    decision.get("reasons", decision.get("user_note")) or "",
                    level=5,
                )
                for key in ("purpose_judgment", "purpose_source", "result_judgment"):
                    if key in decision:
                        lines.extend([f"- Historical {key}: {_inline(decision[key])}", ""])
                if decision.get("result_note"):
                    _section(lines, "Historical result note", decision["result_note"], level=5)
        _section(lines, "Specification", task["body"])
        _section(lines, "Acceptance criteria", task["acceptance_criteria"])
        if task["user_request"]:
            _section(lines, "Original request", task["user_request"])

        if task["unresolved_items"] or task["prerequisites"]:
            lines.extend(["#### Questions and prerequisites", ""])
            if task["status"] in {"done", "dropped", "deferred"}:
                lines.extend(["Recorded context; this task is not currently executable.", ""])
            for item in task["unresolved_items"]:
                _section(lines, f"Unresolved question `{item['id']}`", item["text"], level=5)
            for dependency in task["prerequisites"]:
                project_ref = (
                    f"{_inline(dependency['project_name'])} / {dependency['project_id']}"
                    if dependency["project_id"]
                    else "global group"
                )
                lines.append(
                    f"- {'Satisfied' if dependency['satisfied'] else 'Not satisfied'}: "
                    f"{_inline(dependency['title'])} (`{dependency['id']}`; "
                    f"{dependency['object_type']}; {project_ref}; {_inline(dependency['state'])}; "
                    f"{dependency['milestone']} required)"
                )
            if task["prerequisites"]:
                lines.append("")
        if task["gate_proposals"]:
            lines.extend(["#### Proposed gates (nonblocking until accepted)", ""])
            for proposal in task["gate_proposals"]:
                _section(
                    lines,
                    f"`{proposal['id']}`: {_inline(proposal['gate_type'])} "
                    + (
                        f"({proposal['milestone']} required) "
                        if proposal["gate_type"] == "prerequisite"
                        else ""
                    )
                    + f"by {_inline(proposal['proposer'])}",
                    proposal["detail"],
                    level=5,
                )

        lines.extend(["#### Implementation and review history", ""])
        if not task["attempts"]:
            lines.extend(["No implementation results recorded.", ""])
        for attempt in task["attempts"]:
            context = []
            if attempt["spec_revision"] != task["spec_revision"]:
                context.append("superseded specification")
            if attempt["workstream_id"] != workstream["id"]:
                context.append("other workstream")
            if attempt["id"] == task["selected_attempt_id"]:
                context.append("selected signed-off result")
            lines.extend(
                [
                    f"##### Attempt `{attempt['id']}`",
                    "",
                    f"- State: {attempt['state']}; revision: {attempt['revision']}",
                    f"- Specification: {attempt['spec_revision']}; "
                    f"workstream: `{attempt['workstream_id']}`",
                    f"- Context: {', '.join(context) or 'current specification and workstream'}",
                    f"- Implementer: {_inline(attempt['implementer'])}",
                    f"- Updated: {attempt['updated_at']}",
                    "",
                ]
            )
            _section(lines, "Result", attempt["summary"], level=6)
            _section(lines, "Evidence", attempt["evidence"], level=6)
            if artifacts := attempt.get("artifacts"):
                _section(
                    lines,
                    "Artifact and commit references",
                    "\n".join(f"{item['kind']}: {item['reference']}" for item in artifacts),
                    level=6,
                )
            if verification := attempt.get("verification"):
                _section(lines, "Verification", verification, level=6)
            if specification_etag := attempt.get("specification_etag"):
                _section(lines, "Checked specification token", specification_etag, level=6)
            if attempt["reviewer"]:
                lines.extend([f"Reviewer: {_inline(attempt['reviewer'])}", ""])
            if attempt["review_note"]:
                _section(lines, "Review note", attempt["review_note"], level=6)
            if attempt["human_review_note"]:
                _section(lines, "Human review note", attempt["human_review_note"], level=6)
            for concern in attempt.get("concerns", []):
                _section(
                    lines,
                    f"{concern['kind'].title()} concern — {concern['source']} "
                    f"{_inline(concern['author'])} (nonblocking)",
                    concern["text"],
                    level=6,
                )
    return "\n".join(lines)
