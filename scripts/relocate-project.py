#!/usr/bin/env python3
"""Relocate one registered project without replacing tasks or historical proof.

Workstreams are rebound separately through init. This installation maintenance
command updates the project header because there is no MCP project-edit tool.
"""

import argparse
from pathlib import Path

from simtask.store import Store, TaskError


def relocate(store, project_id, previous_path, path, name):
    previous = str(Path(previous_path).resolve())
    target = str(Path(path).resolve())

    def operation(db, scope):
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise TaskError("unknown_project")
        before = dict(row)
        if before["canonical_path"] != previous:
            raise TaskError("project_path_changed: inspect current registration before relocating")
        attached = db.execute(
            "SELECT project_id FROM project_paths WHERE path=?", (target,)
        ).fetchone()
        if attached and attached[0] != project_id:
            raise TaskError("target_path_registered_to_another_project")
        db.execute(
            "UPDATE projects SET canonical_path=?,name=? WHERE id=?", (target, name, project_id)
        )
        db.execute("INSERT OR IGNORE INTO project_paths VALUES (?,?)", (target, project_id))
        after = dict(db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())
        scope.update(project_id=project_id, before=before, after=after)
        return {"project": after}

    return store._run(
        "project.relocated",
        {"project_id": project_id, "previous_path": previous, "path": target, "name": name},
        operation,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--previous-path", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    result = relocate(
        Store(args.db, "simtask-rename"), args.project, args.previous_path, args.path, args.name
    )
    project = result["project"]
    print(f"{project['id']}: {project['name']} at {project['canonical_path']}")


if __name__ == "__main__":
    main()
