"""Start a realistic disposable browser workspace; never touches the user's database."""

import tempfile
from pathlib import Path

from task_mcp.store import Store
from task_mcp.viewer import launch_viewer


def seed(path):
    store = Store(path, actor="synthetic-demo")
    context = store.init_project(str(path.parent / "Fieldnotes"), branch="main", confirmed=True)
    project, stream = context["project"]["id"], context["workstream"]["id"]
    group = store.create_group(
        stream, "A calmer writing experience", "A shared initiative across the app and website."
    )
    specs = [
        (
            "Give every draft a quiet place to begin",
            "Replace the crowded new-document screen with a focused writing canvas.\n\n"
            "Keep the title, a short prompt and a single clear action. Preserve keyboard shortcuts "
            "and restore unfinished drafts when the app opens again.",
            "• A new draft opens in one click.\n• Keyboard focus starts in the title.\n"
            "• Unfinished writing survives a refresh.",
        ),
        ("Make recent notes easier to find", "Add a useful recent-notes view.", "Search by title."),
        (
            "Choose the right export defaults",
            "Settle the first export experience.",
            "Record a decision.",
        ),
        ("Polish the reading rhythm", "Review spacing and typography.", "Comfortable on mobile."),
    ]
    tasks = []
    for title, body, criteria in specs:
        tasks.append(
            store.create_task(
                project,
                title,
                body,
                criteria,
                source="user",
                user_request="Synthetic demo request",
                approval={"basis": "specific", "note": "Synthetic demo request"},
                workstream_id=stream,
                scope="workstream",
            )
        )
    attempt = store.record_result(
        tasks[0]["id"],
        stream,
        1,
        "demo-builder",
        "A focused canvas with automatic draft recovery.",
        "Synthetic evidence: keyboard flow and draft recovery checked.",
        artifacts=[{"kind": "artifact", "reference": "examples/viewer_demo.py"}],
        verification="Synthetic viewer fixture",
        specification_etag=store.get_tasks([tasks[0]["id"]])["items"][0]["specification_etag"],
    )
    store.record_review(attempt["id"], 1, "demo-reviewer", "pass", "Synthetic independent review.")
    store.add_unresolved(tasks[2]["id"], 1, "Should Markdown or plain text be the default?")
    store.set_disposition(tasks[3]["id"], 1, "deferred", "After the first writing-flow iteration.")
    store.add_group_member(group["id"], 1, tasks[1]["id"], 1)
    store.create_task(
        project,
        "Explore a gentler onboarding",
        "A proposal to investigate.",
        workstream_id=stream,
        scope="workstream",
    )
    other = store.init_project(str(path.parent / "Fieldnotes-Web"), branch="main", confirmed=True)
    store.create_task(
        other["project"]["id"],
        "Explain the new writing flow",
        source="user",
        user_request="Synthetic request",
        approval={"basis": "specific", "note": "Synthetic request"},
        workstream_id=other["workstream"]["id"],
        scope="workstream",
        group_id=group["id"],
        group_expected_revision=2,
    )
    return store


if __name__ == "__main__":
    path = Path(tempfile.mkdtemp(prefix="task-mcp-viewer-demo-")) / "tasks.sqlite3"
    seed(path)
    print(f"Disposable database: {path}")
    print(launch_viewer(path)["url"])
    print(f"Stop: task-mcp ui --db {path} --stop")
