"""Compare direct Store and local stdio latency using disposable databases only."""

import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from task_mcp.store import Store


def snapshot(source: Path | None, target: Path):
    if source is not None:
        # SQLite backup includes committed WAL data; never copy only the main file.
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(target)) as dst:
                src.backup(dst)


def summarize(samples):
    return {
        name: {
            "n": len(values),
            "median_ms": round(statistics.median(values), 3),
            "min_ms": round(min(values), 3),
            "max_ms": round(max(values), 3),
            "samples_ms": [round(value, 3) for value in values],
        }
        for name, values in samples.items()
    }


async def exercise(call, samples, project, workstream):
    timings = {}

    async def timed(name, **arguments):
        start = perf_counter_ns()
        result = await call(name, **arguments)
        timings.setdefault(name, []).append((perf_counter_ns() - start) / 1e6)
        return result

    for index in range(samples):
        await timed("list_tasks", project=project, workstream_id=workstream)
        task = await timed(
            "create_task",
            project=project,
            title=f"Synthetic latency sample {index}",
            workstream_id=workstream,
            scope="workstream",
        )
        task = await timed(
            "update_task",
            task_id=task["id"],
            expected_revision=task["revision"],
            changes={"body": "Synthetic benchmark specification"},
        )
        task = await timed(
            "accept_task",
            task_id=task["id"],
            expected_revision=task["revision"],
            approval={
                "basis": "specific",
                "note": "Synthetic benchmark acceptance, not a real user verdict",
            },
        )
        await timed(
            "record_result",
            task_id=task["id"],
            workstream_id=workstream,
            expected_revision=task["revision"],
            implementer="synthetic-benchmark",
            summary="Synthetic benchmark result",
            evidence="Synthetic benchmark evidence",
        )
    return summarize(timings)


def prepare(database, project, workstream):
    store = Store(database, "synthetic-benchmark")
    if project is None:
        setup = store.init_project(
            str(database.parent / "synthetic-checkout"), "main", confirmed=True
        )
        project, workstream = setup["project"]["id"], setup["workstream"]["id"]
    else:
        status = store.workstream_status(workstream)
        if status["project"]["id"] != project:
            raise ValueError("project and workstream must identify the same copied project")
    return project, workstream


async def run(args):
    with TemporaryDirectory(prefix="task-mcp-benchmark-", dir=args.work_dir) as directory:
        root = Path(directory)
        report = {
            "samples_per_operation": args.samples,
            "temporary_parent": str(root.parent),
            "source": str(args.source.resolve()) if args.source else None,
            "scope": "Local only: excludes host policy approval and client bridge overhead",
        }
        for transport in ("direct", "stdio"):
            database = root / f"{transport}.sqlite3"
            snapshot(args.source, database)
            project, workstream = prepare(database, args.project, args.workstream)
            start = perf_counter_ns()
            if transport == "direct":
                store = Store(database, "synthetic-benchmark")
                startup = (perf_counter_ns() - start) / 1e6

                async def call(name, _store=store, **arguments):
                    return getattr(_store, name)(**arguments)

                operations = await exercise(call, args.samples, project, workstream)
            else:
                source_root = Path(__file__).resolve().parents[1] / "src"
                server = StdioServerParameters(
                    command=sys.executable,
                    args=[
                        "-m",
                        "task_mcp",
                        "--db",
                        str(database),
                        "--actor",
                        "synthetic-benchmark",
                    ],
                    env={"PYTHONPATH": str(source_root)},
                )
                async with Client(server, read_timeout_seconds=30) as client:
                    startup = (perf_counter_ns() - start) / 1e6

                    async def call(name, **arguments):
                        result = await client.call_tool(name, arguments)
                        if result.is_error:
                            raise RuntimeError(f"{name}: {result.content}")
                        return result.structured_content

                    operations = await exercise(call, args.samples, project, workstream)
            with closing(sqlite3.connect(database)) as db:
                integrity = [row[0] for row in db.execute("PRAGMA integrity_check")]
                fk_errors = db.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != ["ok"] or fk_errors:
                raise RuntimeError(f"{transport} disposable database integrity check failed")
            report[transport] = {
                "startup_ms": round(startup, 3),
                "operations": operations,
                "integrity": "ok",
            }
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--source", type=Path, help="Optional SQLite source, opened read-only")
    parser.add_argument("--project", help="Project ID in the copied source")
    parser.add_argument("--workstream", help="Workstream ID in the copied source")
    parser.add_argument("--work-dir", type=Path, help="Existing parent on the dataset to measure")
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000:
        parser.error("--samples must be between 1 and 1000")
    if bool(args.project) != bool(args.workstream) or (args.project and not args.source):
        parser.error("--project and --workstream require each other and --source")
    print(json.dumps(asyncio.run(run(args)), indent=2))


if __name__ == "__main__":
    main()
