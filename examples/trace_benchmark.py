"""Compare enabled/disabled tracing over real stdio on disposable synthetic data."""

import argparse
import asyncio
import json
import statistics
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from task_mcp.store import Store


async def benchmark(directory, samples):
    root = Path(__file__).resolve().parents[1]
    database = directory / "tasks.sqlite3"
    store = Store(database, "synthetic-trace-benchmark")
    ctx = store.init(
        str(directory / "synthetic-checkout"),
        branch="main",
        confirmed=True,
        action="create_project",
    )
    project, workstream = ctx["project"]["id"], ctx["workstream"]["id"]
    task = store.create_task(
        project,
        "Synthetic full specification",
        body="å scope\n" * 12000,
        acceptance_criteria="Synthetic criteria\n" * 1000,
        workstream_id=workstream,
    )
    attempt = store.record_result(
        task["id"],
        workstream,
        task["revision"],
        "synthetic-benchmark",
        "Synthetic result",
        "Synthetic proof\n" * 10000,
        [{"kind": "artifact", "reference": "synthetic fixture"}],
        "Synthetic fixture verification",
        task["specification_etag"],
    )
    for number in range(20):
        store.create_task(
            project,
            f"Synthetic queue task {number}",
            summary="Small queue card",
            workstream_id=workstream,
        )
    methods = {
        "runtime_info": ("runtime_info", {}),
        "list_tasks": ("list_tasks", {"project": project, "workstream_id": workstream}),
        "card": ("get_tasks", {"ids": [task["id"]]}),
        "specification_and_proof": (
            "get_tasks",
            {"ids": [task["id"]], "specification": True, "attempt_ids": [attempt["id"]]},
        ),
    }
    measurements = {
        mode: {name: [] for name in ["catalog", *methods]} for mode in ("enabled", "disabled")
    }
    for round_number in range(2):
        async with AsyncExitStack() as stack:
            clients = {}
            setup_order = ("disabled", "enabled") if round_number == 0 else ("enabled", "disabled")
            for mode in setup_order:
                args = [
                    "-m",
                    "task_mcp",
                    "--db",
                    str(database),
                    "--trace-dir",
                    str(directory / "traces"),
                ]
                if mode == "disabled":
                    args.append("--no-trace")
                params = StdioServerParameters(
                    command=sys.executable,
                    args=args,
                    env={"PYTHONPATH": str(root / "src"), "TASK_MCP_DB": str(database)},
                )
                client = await stack.enter_async_context(Client(params, read_timeout_seconds=30))
                clients[mode] = client
                await client.list_tools()
                for tool_name, arguments in methods.values():
                    assert not (await client.call_tool(tool_name, arguments)).is_error
            for name in measurements["enabled"]:
                for number in range(samples):
                    order = (
                        ("disabled", "enabled")
                        if (number + round_number) % 2 == 0
                        else ("enabled", "disabled")
                    )
                    for mode in order:
                        client = clients[mode]
                        started = perf_counter_ns()
                        if name == "catalog":
                            await client.list_tools()
                        else:
                            tool_name, arguments = methods[name]
                            assert not (await client.call_tool(tool_name, arguments)).is_error
                        measurements[mode][name].append((perf_counter_ns() - started) / 1e6)
    report = {
        "boundary": (
            "Real stdio round-trip; disposable synthetic data; interleaved modes with "
            "alternating order and two fresh connection pairs. Shared local load and "
            "background trace writes can affect either mode. Host approval delays are not measured."
        ),
        "samples_per_mode_per_operation": samples * 2,
        "operations": {},
    }
    for name in measurements["enabled"]:
        operation = {}
        for mode in ("enabled", "disabled"):
            values = measurements[mode][name]
            ordered = sorted(values)
            operation[mode] = {
                "median_ms": round(statistics.median(values), 3),
                "p95_ms": round(ordered[int(0.95 * (len(ordered) - 1))], 3),
                "max_ms": round(max(values), 3),
            }
        operation["added_median_ms"] = round(
            operation["enabled"]["median_ms"] - operation["disabled"]["median_ms"], 3
        )
        report["operations"][name] = operation
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    if args.samples <= 0:
        parser.error("samples must be positive")
    with TemporaryDirectory(prefix="task-mcp-trace-benchmark-", dir=args.work_dir) as name:
        report = asyncio.run(benchmark(Path(name), args.samples))
    serialized = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized)
    print(serialized, end="")


if __name__ == "__main__":
    main()
