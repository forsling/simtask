"""Page through the viewer's Activity read on a copy of a task database.

The source database is only ever opened read-only (mode=ro) and copied with SQLite's
backup API; every read below runs against the copy, which gains its own audit events.

    python examples/activity_check.py COPY --copy-from ~/.local/share/task-mcp/tasks.sqlite3
    python examples/activity_check.py COPY [TASK_ID ...] [--show 20]

With no task IDs it checks an imported task, the busiest task and the busiest group.
For each it walks every page, checks order, duplicates and that each result, review and
sign-off appears exactly once, opens the oldest result as a target, and prints timings
and the first page.
"""

import argparse
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from task_mcp.store import Store
from task_mcp.viewer import dispatch


def copy_database(source, copy):
    with (
        closing(sqlite3.connect(f"file:{Path(source).expanduser()}?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(copy)) as dst,
    ):
        src.backup(dst)


def default_tasks(db):
    imported = db.execute(
        "SELECT e.task_id FROM events e JOIN attempts a ON a.task_id=e.task_id "
        "WHERE e.action='legacy.task_imported' AND NOT EXISTS (SELECT 1 FROM events r "
        "WHERE r.task_id=a.task_id AND r.action='attempt.recorded' "
        "AND json_extract(r.after_json,'$.attempt.id')=a.id) ORDER BY e.task_id LIMIT 1"
    ).fetchone()
    busiest = (
        "SELECT e.task_id FROM events e JOIN tasks t ON t.id=e.task_id WHERE t.object_type=? "
        "GROUP BY e.task_id ORDER BY count(*) DESC, e.task_id LIMIT 1"
    )
    return [
        row[0]
        for row in (
            imported,
            db.execute(busiest, ("task",)).fetchone(),
            db.execute(busiest, ("group",)).fetchone(),
        )
        if row
    ]


def line(entry):
    extra = {
        "result": lambda e: (
            f"{e['attempt_id']} {e.get('state')} {e.get('workstream_name')} "
            f"spec {e['spec_revision']}{' imported' if e.get('imported') else ''}"
        ),
        "review": lambda e: f"{e['verdict']} by {e['reviewer']}",
        "signoff": lambda e: f"{e['decision']} -> {e.get('disposition')}",
    }.get(entry["kind"], lambda e: "")(entry)
    summary = (entry.get("summary") or "")[:80]
    when = entry["timestamp"][:19]
    return f"  {entry['sequence']:>6} {when} {entry['kind']:<18} {extra} | {summary}"


def check(store, db, task_id, show):
    pages, timings, cursor = [], [], None
    while True:
        started = time.perf_counter()
        page = dispatch(store, "activity", {"task_id": task_id, "cursor": cursor})
        timings.append((time.perf_counter() - started) * 1000)
        pages.append(page)
        cursor = page.get("next_cursor")
        if cursor is None:
            break
    entries = [entry for page in pages for entry in page["items"]]
    keys = [(e["sequence"], e["kind"], e.get("attempt_id")) for e in entries]
    sequences = [e["sequence"] for e in entries]
    problems = []
    if len(set(keys)) != len(keys):
        problems.append("duplicate entries")
    if sequences != sorted(sequences, reverse=True):
        problems.append("not newest first")
    if any(len(p["items"]) > 20 for p in pages):
        problems.append("page over 20")
    if any(len(p["items"]) < 20 for p in pages[:-1]):
        problems.append("short page before the last")
    attempts = sorted(
        r[0] for r in db.execute("SELECT id FROM attempts WHERE task_id=?", (task_id,))
    )
    shown = sorted(e["attempt_id"] for e in entries if e["kind"] == "result")
    if shown != attempts:
        problems.append(f"results {shown} != attempts {attempts}")
    for action, kind in (
        ("attempt.reviewed", "review"),
        ("attempt.human_reviewed", "human_review"),
        ("task.signoff", "signoff"),
    ):
        expected = db.execute(
            "SELECT count(*) FROM events WHERE task_id=? AND action=? AND outcome='ok'",
            (task_id, action),
        ).fetchone()[0]
        if sum(e["kind"] == kind for e in entries) != expected:
            problems.append(f"{kind} count differs from {expected}")
    total, hidden = db.execute(
        "SELECT count(*), sum(outcome<>'ok' OR action IN ('tasks.read','tasks.listed',"
        "'attempt.read','attempts.listed','events.listed','activity.read')) "
        "FROM events WHERE task_id=?",
        (task_id,),
    ).fetchone()
    print(
        f"{task_id} ({pages[0]['object_type']}): {total} events ({hidden} reads/errors), "
        f"{len(entries)} entries in {len(pages)} pages {[len(p['items']) for p in pages]}, "
        f"page ms {[round(t, 1) for t in timings]}, newest {pages[0]['newest_sequence']}"
    )
    if shown:
        oldest = next(e for e in reversed(entries) if e["kind"] == "result")
        started = time.perf_counter()
        target = dispatch(store, "activity", {"task_id": task_id, "target": oldest["attempt_id"]})
        took = (time.perf_counter() - started) * 1000
        first = next(e for e in target["items"] if e["kind"] == "result")
        if first["attempt_id"] != oldest["attempt_id"]:
            problems.append("target page does not start with the target")
        print(
            f"  target {oldest['attempt_id']}: page of {len(target['items'])} starting at "
            f"sequence {target['items'][0]['sequence']}, {took:.1f} ms"
        )
    print("  checks:", "; ".join(problems) if problems else "ok")
    for entry in pages[0]["items"][:show]:
        print(line(entry))
    return not problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("copy", type=Path, help="the database copy to read (and audit)")
    parser.add_argument("tasks", nargs="*", help="task or group IDs; default: three samples")
    parser.add_argument("--copy-from", help="first copy this database, opened read-only")
    parser.add_argument("--show", type=int, default=20, help="entries of page 1 to print")
    args = parser.parse_args()
    if args.copy_from:
        copy_database(args.copy_from, args.copy)
    store = Store(args.copy, actor="activity-check")
    with closing(sqlite3.connect(args.copy)) as db:
        tasks = args.tasks or default_tasks(db)
        ok = all([check(store, db, task_id, args.show) for task_id in tasks])
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
