"""A disposable database for checking task details' Spec and Activity tabs.

Copies the live database with SQLite's backup API (the source is opened read-only,
mode=ro) and adds seeded tasks to the copy only, in the task-mcp project: one awaiting
sign-off with results in two workstreams and a superseded specification, one whose
current result was sent back and sits below 60 newer entries, and one completed. It
prints the IDs to open, with the busiest real group and an imported task of the copy.

    python examples/spec_activity_seed.py COPY --copy-from ~/.local/share/task-mcp/tasks.sqlite3
    TASK_MCP_DB=COPY ./run.sh          # open the printed link; stop with
    TASK_MCP_DB=COPY ./run.sh --stop   # (never plain ./run.sh --stop: that is the live viewer)

    python examples/spec_activity_seed.py COPY --add-question TASK TEXT

The second form adds one question to a task in an existing copy, to check that Activity
offers newer entries between loads. It refuses the default (live) database.
"""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from task_mcp.store import Store, default_database


def copy_database(source, copy):
    with (
        closing(sqlite3.connect(f"file:{Path(source).expanduser()}?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(copy)) as dst,
    ):
        src.backup(dst)


def full(store, task_id):
    return store.get_tasks([task_id])["items"][0]


def result(store, task_id, ws, summary, artifacts=None):
    current = full(store, task_id)
    return store.record_result(
        task_id,
        ws,
        current["revision"],
        "seed implementer",
        summary,
        f"Evidence for: {summary}",
        artifacts=artifacts
        or [
            {"kind": "commit", "reference": "d9464f2"},
            {"kind": "artifact", "reference": "examples/activity_check.py"},
        ],
        verification=f"Verified: {summary}",
        specification_etag=current["specification_etag"],
        concerns=[{"kind": "design", "text": f"A seeded concern on {summary}"}],
    )


def churn(store, task_id, count, label):
    """count question add + resolve pairs: 2*count meaningful entries."""
    for index in range(count):
        current = full(store, task_id)
        added = store.add_unresolved(task_id, current["revision"], f"{label} question {index + 1}?")
        item = added["unresolved_items"][-1]["id"] if "unresolved_items" in added else None
        current = full(store, task_id)
        item = item or current["unresolved_items"][-1]["id"]
        store.resolve_unresolved(task_id, current["revision"], item, f"{label} answer {index + 1}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("copy")
    parser.add_argument("--copy-from")
    parser.add_argument("--add-question", nargs=2, metavar=("TASK", "TEXT"))
    args = parser.parse_args()
    copy = Path(args.copy).expanduser().absolute()
    if args.add_question:
        if not copy.exists() or copy.resolve() == default_database().resolve():
            raise SystemExit("--add-question works on an existing disposable copy only")
        store = Store(copy, actor="seed")
        task_id, text = args.add_question
        store.add_unresolved(task_id, full(store, task_id)["revision"], text)
        return
    if not args.copy_from:
        parser.error("--copy-from is required to build a copy")
    if copy.exists():
        raise SystemExit(f"{copy} exists; choose a new path")
    copy_database(args.copy_from, copy)
    store = Store(copy, actor="seed")
    project = next(
        p for p in store.list_projects()["items"] if p["canonical_path"].endswith("/task-mcp")
    )
    streams = [
        w
        for w in store.list_workstreams(project["id"])["items"]
        if not w.get("archive", {}).get("archived")
    ]
    main_ws = next(w for w in streams if w["branch"] == "main")
    alt_ws = next((w for w in streams if w["id"] != main_ws["id"]), None)
    if alt_ws is None:
        # A second workstream in the copy only (same checkout, another branch name).
        alt_ws = store.init(
            project["canonical_path"], "seed-alt-branch", action="new_workstream", confirmed=True
        )["workstream"]
    ids = {
        "project": project["id"],
        "main": main_ws["id"],
        "alt": alt_ws["id"],
        "alt_name": alt_ws["branch"] or alt_ws["name"],
    }
    spec = dict(
        body="Seeded specification for the tab check.",
        acceptance_criteria="Seeded criteria; one; two",
    )

    # Awaiting sign-off: a superseded result sent back in main, then a passed result
    # in another workstream for the current specification, with churn in between.
    t = store.create_task(
        project["id"],
        "Seed: awaiting sign-off across two workstreams",
        workstream_id=main_ws["id"],
        user_request="Seeded request",
        **spec,
    )
    store.add_to_workstream(t["id"], alt_ws["id"], full(store, t["id"])["revision"])
    old = result(store, t["id"], main_ws["id"], "First round on spec 1")
    store.record_review(
        old["id"], old["revision"], "seed reviewer", "rework", "Missing the gap fill."
    )
    current = full(store, t["id"])
    store.update_task(
        t["id"],
        current["revision"],
        {"body": spec["body"] + " Revised."},
        specification_etag=current["specification_etag"],
    )
    churn(store, t["id"], 12, "Signoff")
    new = result(store, t["id"], alt_ws["id"], "Second round on spec 2")
    store.record_review(
        new["id"], new["revision"], "seed reviewer", "pass", "Checked in a browser."
    )
    ids["signoff"] = t["id"]

    # In progress: the current result was sent back and is far below the first page.
    t = store.create_task(
        project["id"],
        "Seed: result in rework outside the first page",
        workstream_id=main_ws["id"],
        **spec,
    )
    old = result(store, t["id"], main_ws["id"], "Rework candidate")
    store.record_review(
        old["id"], old["revision"], "seed reviewer", "rework", "Needs another pass."
    )
    churn(store, t["id"], 30, "Rework")
    ids["rework"] = t["id"]

    # Completed: the accepted result.
    t = store.create_task(
        project["id"],
        "Seed: completed with an accepted result",
        workstream_id=main_ws["id"],
        **spec,
    )
    done = result(store, t["id"], main_ws["id"], "Accepted work")
    reviewed = store.record_review(done["id"], done["revision"], "seed reviewer", "pass", "Good.")
    current = full(store, t["id"])
    store.signoff_task(
        t["id"],
        current["revision"],
        "approve",
        "Looks right.",
        attempt_id=done["id"],
        expected_attempt_revision=reviewed["revision"],
    )
    ids["done"] = t["id"]

    # Real objects of the copy: the task with the most results (ties: most events), the
    # group with the most events, and a task with an imported result. Each with the
    # location path of its project, for the address bar.
    with closing(sqlite3.connect(copy)) as db:
        ids["busy"] = db.execute(
            "SELECT a.task_id FROM attempts a WHERE a.task_id NOT LIKE 'seed-%' GROUP BY "
            "a.task_id ORDER BY count(*) DESC, (SELECT count(*) FROM events e "
            "WHERE e.task_id=a.task_id) DESC LIMIT 1"
        ).fetchone()[0]
        ids["group"] = db.execute(
            "SELECT e.task_id FROM events e JOIN tasks t ON t.id=e.task_id "
            "WHERE t.object_type='group' GROUP BY e.task_id ORDER BY count(*) DESC LIMIT 1"
        ).fetchone()[0]
        ids["imported"] = db.execute(
            "SELECT e.task_id FROM events e JOIN attempts a ON a.task_id=e.task_id "
            "WHERE e.action='legacy.task_imported' AND NOT EXISTS (SELECT 1 FROM events r "
            "WHERE r.task_id=a.task_id AND r.action='attempt.recorded' "
            "AND json_extract(r.after_json,'$.attempt.id')=a.id) ORDER BY e.task_id LIMIT 1"
        ).fetchone()[0]
        hex8 = lambda value: value.split("_", 1)[1][:8]  # noqa: E731
        paths = {}
        for key in ("signoff", "rework", "done", "busy", "imported"):
            task_id = ids[key]
            project_id = db.execute(
                "SELECT project_id FROM tasks WHERE id=?", (task_id,)
            ).fetchone()[0]
            short = hex8(task_id) if task_id.startswith("tsk_") else "id/" + task_id
            paths[key] = f"/p/{hex8(project_id)}/t/{short}"
        paths["group"] = "/g/" + hex8(ids["group"])
        ids["paths"] = paths
    print(json.dumps(ids, indent=2))


if __name__ == "__main__":
    main()
