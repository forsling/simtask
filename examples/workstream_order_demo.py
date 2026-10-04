"""Disposable A/B preview with four shared tasks and independent list orders."""

import argparse
import json
import tempfile
from pathlib import Path

from task_mcp.store import Store
from task_mcp.viewer import launch_viewer


def seed(path):
    """Create synthetic state only; never overwrite an existing fixture."""
    if path.exists():
        raise ValueError("Use a new disposable database path")
    store = Store(path, actor="synthetic-order-demo")
    ctx = store.init(
        str(path.parent / "Shared-task-order"), branch="A", action="create_project", confirmed=True
    )
    project, a = ctx["project"]["id"], ctx["workstream"]["id"]
    b = store.init_workstream(
        project, str(path.parent / "Shared-task-order"), branch="B", confirmed=True
    )["workstream"]["id"]
    tasks = []
    for title in (
        "Alpha — shared task",
        "Beta — shared task",
        "Gamma — shared task",
        "Delta — shared task",
    ):
        task = store.create_task(
            project,
            title,
            "Synthetic preview: this canonical task appears in both A and B.\n\n"
            "Drag tasks in a workstream list to reorder it. Switch A/B in the sidebar "
            "to see independent list order. More actions offers Add to workstream and Remove "
            "from workstream; those affect only the selected workstream.",
            "A reorder changes only the chosen list. Named removal leaves the other membership.",
            workstream_id=a,
            source="user",
            user_request="Synthetic preview request",
        )
        store.add_to_workstream(task["id"], b, task["revision"])
        tasks.append(task["id"])
    board = store.list_tasks(project, a)
    store.reorder_tasks(a, [tasks[2], tasks[0]], board["workstream_order_revision"])
    metadata = {
        "database": str(path),
        "project_id": project,
        "workstreams": {"A": a, "B": b},
        "task_ids": tasks,
        "orders": {
            name: [t["id"] for t in store.list_tasks(project, ws)["items"]]
            for name, ws in (("A", a), ("B", b))
        },
        "landing": "A opens with Gamma first; drag rows to reorder A. "
        "B uses Alpha/Beta/Gamma/Delta.",
    }
    path.with_suffix(".preview.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path, help="A new disposable path; default creates a temporary directory"
    )
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    path = args.db or Path(tempfile.mkdtemp(prefix="task-mcp-order-demo-")) / "tasks.sqlite3"
    print(json.dumps(seed(path), indent=2))
    if not args.seed_only:
        print(launch_viewer(path)["url"])
        print(f"Stop: python -m task_mcp ui --db {path} --stop")
