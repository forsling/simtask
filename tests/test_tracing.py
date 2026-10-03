import asyncio
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from task_mcp.trace_report import analyze, page_report, render
from task_mcp.tracing import TraceCollector, TraceConfig, capture, json_bytes


def records(directory):
    return [
        json.loads(line)
        for p in sorted(directory.glob("trace-*.jsonl"))
        for line in p.read_text().splitlines()
    ]


def context(name="get_tasks", args=None, request_id=1):
    return SimpleNamespace(
        method="tools/call",
        params={"name": name, "arguments": args or {}},
        request_id=request_id,
        protocol_version="2025-11-25",
        session=SimpleNamespace(client_params=None),
    )


async def observe(collector, name, args, result=None, error=None, request_id=1):
    async def handler(ctx):
        if error is not None:
            raise error
        return result

    return await collector.middleware(context(name, args, request_id), handler)


def test_capture_snapshots_utf8_and_explicitly_caps_payload():
    original = {"body": "å🙂" * 20}
    measured = capture(original, 1000)
    original["body"] = "changed"
    assert measured["payload"]["body"] == "å🙂" * 20
    assert measured["bytes"] == len(json_bytes(measured["payload"]))
    truncated = capture(measured["payload"], 25)
    assert truncated["truncated"] and "payload" not in truncated
    assert truncated["bytes"] == measured["bytes"]
    assert truncated["sha256"] == measured["sha256"]
    assert len(truncated["json_prefix"].encode()) <= 25


def test_middleware_preserves_results_errors_cancellation_and_notifications(tmp_path):
    collector = TraceCollector(TraceConfig(tmp_path / "traces"))
    collector.start()
    result = {"content": [{"type": "text", "text": "å"}], "structuredContent": {"id": "tsk_a"}}
    error = ValueError("original error")

    async def exercise():
        assert await observe(collector, "get_tasks", {"ids": ["tsk_a"]}, result) is result
        with pytest.raises(ValueError) as caught:
            await observe(collector, "get_tasks", {}, error=error)
        assert caught.value is error
        with pytest.raises(asyncio.CancelledError):
            await observe(collector, "get_tasks", {}, error=asyncio.CancelledError())
        await observe(collector, "get_tasks", {}, {"isError": True})
        ctx = context(request_id=None)
        ctx.method = "notifications/initialized"
        ctx.params = None

        async def no_result(ctx):
            return None

        await collector.middleware(ctx, no_result)
        ctx = context()
        ctx.params = []
        with pytest.raises(ValueError):

            async def malformed(ctx):
                raise error

            await collector.middleware(ctx, malformed)

    asyncio.run(exercise())
    collector.close()
    observed = records(collector.config.directory)
    finishes = [r for r in observed if r["event"] == "request_finish"]
    assert [r["outcome"] for r in finishes] == [
        "success",
        "error",
        "cancelled",
        "tool_error",
        "notification",
        "error",
    ]
    assert finishes[0]["result"]["payload"] == result
    assert finishes[0]["structured_content_bytes"] == len(json_bytes(result["structuredContent"]))
    assert finishes[0]["content_bytes"] == len(json_bytes(result["content"]))
    assert "result" not in finishes[4]
    assert len({r["call_id"] for r in finishes}) == 6
    assert all(r["request_id"] == 1 for r in finishes if r["outcome"] != "notification")
    assert collector.config.directory.stat().st_mode & 0o077 == 0
    assert all(p.stat().st_mode & 0o077 == 0 for p in collector.config.directory.glob("*.jsonl"))


def test_failed_storage_and_full_queue_never_change_handler_result(tmp_path, monkeypatch):
    collector = TraceCollector(TraceConfig(tmp_path / "traces"))

    def failed_write(encoded):
        raise OSError("disk full")

    monkeypatch.setattr(collector, "_append", failed_write)
    collector.start()
    result = {"structuredContent": {"id": "tsk_a", "revision": 3}}
    assert asyncio.run(observe(collector, "update_task", {}, result)) is result
    collector.close()
    assert collector._failures >= 3
    collector = TraceCollector(TraceConfig(tmp_path / "overflow", queue_bytes=1))
    collector.start()
    assert asyncio.run(observe(collector, "update_task", {}, result)) is result
    assert collector._failures >= 3
    collector.config = replace(collector.config, queue_bytes=10000)
    collector.emit({"event": "recovered"})
    collector.close()
    assert records(collector.config.directory)[0]["collection_failures_before_record"] >= 3


def test_writer_start_failure_does_not_gate_task(tmp_path, monkeypatch):
    import threading

    def fail_start(thread):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    collector = TraceCollector(TraceConfig(tmp_path / "traces"))
    collector.start()
    result = {"structuredContent": {"revision": 1}}
    assert asyncio.run(observe(collector, "update_task", {}, result)) is result
    collector.close()
    assert not collector.config.directory.exists()


def test_concurrent_writers_enforce_shared_budget_and_retention(tmp_path):
    directory = tmp_path / "traces"
    directory.mkdir(mode=0o700)
    old = directory / "trace-old.jsonl"
    old.write_text("old data\n")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    collectors = [
        TraceCollector(TraceConfig(directory, max_bytes=3000, segment_bytes=500)) for _ in range(4)
    ]

    def write(collector):
        for number in range(8):
            collector._append(
                json_bytes({"connection": collector.connection_id, "number": number}) + b"\n"
            )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, collectors))
    assert not old.exists()
    files = list(directory.glob("trace-*.jsonl"))
    assert sum(p.stat().st_size for p in files) <= 3000
    assert all(json.loads(line) for p in files for line in p.read_text().splitlines())
    assert (
        len({json.loads(line)["connection"] for p in files for line in p.read_text().splitlines()})
        > 1
    )


def test_report_patterns_filters_incomplete_and_bounded_details(tmp_path):
    collector = TraceCollector(TraceConfig(tmp_path / "traces"))
    collector.start()
    task = {
        "id": "tsk_a",
        "title": "A",
        "body": "Complete spec",
        "acceptance_criteria": "Works",
        "specification_complete": True,
        "updated_at": "old",
    }

    def wire(data):
        return {"structuredContent": data}

    async def exercise():
        await observe(
            collector, "create_task", {"project": "prj_a"}, wire({"id": "tsk_a", "revision": 1})
        )
        await observe(
            collector, "get_tasks", {"ids": ["tsk_a"]}, wire({"items": [{"id": "tsk_a"}]})
        )
        await observe(
            collector,
            "get_tasks",
            {"ids": ["tsk_a"], "specification": True},
            wire({"items": [task]}),
        )
        await observe(
            collector,
            "get_tasks",
            {"ids": ["tsk_a"], "specification": True},
            wire({"items": [{**task, "updated_at": "new"}]}),
        )
        with pytest.raises(ValueError):
            await observe(
                collector,
                "update_task",
                {"task_id": "tsk_a"},
                error=ValueError("revision conflict"),
            )
        await observe(
            collector, "update_task", {"task_id": "tsk_a"}, wire({"id": "tsk_a", "revision": 2})
        )

    asyncio.run(exercise())
    collector.emit(
        {
            "event": "request_start",
            "call_id": "incomplete",
            "request_id": 7,
            "method": "tools/call",
            "tool": "get_tasks",
        }
    )
    collector.close()
    report = analyze(collector.config.directory)
    assert report["call_count"] == 7 and report["incomplete_calls"] == 1
    assert {c["kind"] for c in report["candidates"]} == {
        "read_after_ack",
        "card_then_specification",
        "unchanged_specification_reread",
        "retry_after_error",
    }
    assert all(c["connection_id"] == collector.connection_id for c in report["candidates"])
    assert analyze(collector.config.directory, entity="tsk_a")["call_count"] == 6
    assert analyze(collector.config.directory, tool="update_task")["call_count"] == 2
    assert analyze(collector.config.directory, connection="different")["call_count"] == 0
    assert analyze(collector.config.directory, since="2100-01-01T00:00:00Z")["call_count"] == 0
    with pytest.raises(ValueError):
        analyze(collector.config.directory, since="2026-10-03")
    paged = page_report(report, limit=2)
    assert len(paged["sequence"]) == 2 and paged["detail_page"]["sequence_has_more"]
    assert len(page_report(report, include_all=True)["sequence"]) == 7
    output = render(paged)
    assert "Candidates are not misuse verdicts" in output and "Chronological calls" in output
    assert "Complete spec" not in output
    bad = collector.config.directory / "trace-partial.jsonl"
    bad.write_text('{"trace_format_revision":999}\n{"unfinished":')
    assert analyze(collector.config.directory)["invalid_records"] == 1
    assert analyze(collector.config.directory)["unsupported_records"] == 1


def test_read_only_cli_report_does_not_create_database(tmp_path):
    database = tmp_path / "nonexistent.sqlite3"
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "task_mcp", "trace-report", "--db", str(database), "--json"],
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["call_count"] == 0
    assert not database.exists() and not database.with_name(database.name + ".traces").exists()


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_real_stdio_default_collection_errors_and_disable(tmp_path, mode):
    root = Path(__file__).resolve().parents[1]
    database, directory = tmp_path / "tasks.sqlite3", tmp_path / "traces"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "task_mcp", "--db", str(database), "--trace-dir", str(directory)],
        env={"PYTHONPATH": str(root / "src")},
    )

    async def exercise():
        from mcp import types

        async with Client(
            params,
            read_timeout_seconds=15,
            mode=mode,
            client_info=types.Implementation(name="trace-test", version="1"),
        ) as client:
            assert len((await client.list_tools()).tools) == 41
            assert not (await client.call_tool("runtime_info", {})).is_error
            assert not (
                await client.call_tool("get_default_skills", {"name": "feature-design"})
            ).is_error
            assert (
                await client.call_tool("get_tasks", {"ids": [], "specification": "invalid"})
            ).is_error
            assert (await client.call_tool("missing_tool", {})).is_error
            from mcp import types
            from mcp.shared.exceptions import MCPError

            with pytest.raises(MCPError):
                await client.session.send_request(
                    types.Request(method="unknown/method", params={}), types.EmptyResult
                )

    asyncio.run(exercise())
    report = analyze(directory)
    assert {
        "server/discover" if mode == "auto" else "initialize",
        "tools/list",
        "runtime_info",
        "get_default_skills",
        "get_tasks",
        "missing_tool",
        "unknown/method",
    } <= set(report["per_tool"])
    assert report["per_tool"]["get_tasks"]["outcomes"] == {"tool_error": 1}
    assert report["per_tool"]["unknown/method"]["outcomes"] == {"error": 1}
    assert report["incomplete_calls"] == 0 and not report["reported_collection_failures"]
    starts = [r for r in records(directory) if r["event"] == "request_start"]
    assert any(
        r.get("client_info", {}).get("name") == "trace-test"
        for r in starts
        if r.get("client_info") is not None
    )
    disabled = tmp_path / "disabled"
    params.args.extend(["--no-trace", "--trace-dir", str(disabled)])

    async def disabled_exercise():
        async with Client(params, read_timeout_seconds=15) as client:
            await client.call_tool("runtime_info", {})

    asyncio.run(disabled_exercise())
    assert not disabled.exists()


def test_real_stdio_concurrent_connections_have_distinct_identity(tmp_path):
    root = Path(__file__).resolve().parents[1]
    directory = tmp_path / "traces"

    async def consumer(number):
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "task_mcp",
                "--db",
                str(tmp_path / f"{number}.sqlite3"),
                "--trace-dir",
                str(directory),
                "--actor",
                "same-actor",
            ],
            env={"PYTHONPATH": str(root / "src")},
        )
        async with Client(params, read_timeout_seconds=15) as client:
            for _ in range(5):
                assert not (await client.call_tool("runtime_info", {})).is_error

    async def exercise():
        await asyncio.gather(consumer(1), consumer(2))

    asyncio.run(exercise())
    report = analyze(directory)
    assert len(report["connections"]) == 2
    assert report["per_tool"]["runtime_info"]["calls"] == 10
    assert report["incomplete_calls"] == 0 and not report["reported_collection_failures"]
