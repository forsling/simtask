"""Offline inspection of retained traces; no Store connection or ledger writes."""

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime

from task_mcp.tracing import TRACE_FORMAT_REVISION, json_bytes


def parse_time(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("trace time filters require a timezone, for example 2026-10-03T12:00:00Z")
    return parsed


def payload(record, field):
    captured = record.get(field, {})
    return captured.get("payload") if not captured.get("truncated") else None


def structured(result):
    return result.get("structuredContent", {}) if isinstance(result, dict) else {}


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3) if ordered else None


def analyze(directory, *, since=None, until=None, connection=None, tool=None, entity=None):
    since, until = parse_time(since), parse_time(until)
    if since and until and since > until:
        raise ValueError("since must not be later than until")
    pending = {}
    runtimes = {}
    collection_failures = {}
    invalid = unsupported = records = 0
    for path in sorted(directory.glob("trace-*.jsonl")):
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("record must be an object")
                    if record.get("trace_format_revision") != TRACE_FORMAT_REVISION:
                        unsupported += 1
                        continue
                    conn = record["connection_id"]
                    records += 1
                    collection_failures[conn] = max(
                        collection_failures.get(conn, 0),
                        record.get("collection_failures_before_record", 0),
                    )
                    if record["event"] == "connection_start":
                        runtimes[conn] = record.get("runtime", {})
                    elif record["event"] in {"request_start", "request_finish"}:
                        key = (conn, record["call_id"])
                        call = pending.setdefault(
                            key, {"connection_id": conn, "call_id": record["call_id"]}
                        )
                        call["start" if record["event"] == "request_start" else "finish"] = record
                except (ValueError, KeyError, TypeError):
                    invalid += 1
    calls = []
    for call in pending.values():
        start, finish = call.get("start", {}), call.get("finish", {})
        observed = start or finish
        timestamp = observed.get("timestamp", "")
        try:
            when = parse_time(timestamp)
        except (ValueError, TypeError):
            invalid += 1
            continue
        if when is None:
            invalid += 1
            continue
        refs = sorted(set(start.get("entity_refs", []) + finish.get("entity_refs", [])))
        name = observed.get("tool") or observed.get("method")
        if (
            (since and when < since)
            or (until and when > until)
            or (connection and call["connection_id"] != connection)
            or (tool and name != tool)
            or (entity and entity not in refs)
        ):
            continue
        request = payload(start, "request") or {}
        params = request.get("params") or {}
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        result = payload(finish, "result")
        detail = structured(result)
        calls.append(
            {
                **call,
                "timestamp": timestamp,
                "tool": name,
                "sequence": observed.get("sequence", 0),
                "entity_refs": refs,
                "arguments": arguments,
                "detail": detail,
                "state": "complete" if start and finish else "incomplete",
                "outcome": finish.get("outcome", "incomplete"),
                "duration_ms": finish.get("duration_ms"),
                "request_bytes": start.get("request", {}).get("bytes"),
                "result_bytes": finish.get("result", {}).get("bytes"),
                "structured_content_bytes": finish.get("structured_content_bytes"),
                "content_bytes": finish.get("content_bytes"),
                "truncated": bool(
                    start.get("request", {}).get("truncated")
                    or finish.get("result", {}).get("truncated")
                ),
            }
        )
    calls.sort(key=lambda call: (parse_time(call["timestamp"]), call["call_id"]))
    candidates = []
    prior = {}
    full_reads = {}
    errors = {}
    groups = defaultdict(list)
    writes = {
        "create_task",
        "update_task",
        "accept_task",
        "withdraw_acceptance",
        "signoff_task",
        "add_unresolved",
        "resolve_unresolved",
        "set_disposition",
        "record_result",
    }

    def candidate(kind, first, second, note, task_id=None):
        candidates.append(
            {
                "kind": kind,
                "call_ids": [first["call_id"], second["call_id"]],
                "timestamps": [first["timestamp"], second["timestamp"]],
                "connection_id": second["connection_id"],
                "task_id": task_id,
                "note": note,
            }
        )

    # Candidate relationships follow receive order within each connection even
    # if the wall clock changes. Global display keeps the recorded UTC times.
    for call in sorted(calls, key=lambda c: (c["connection_id"], c["sequence"])):
        conn, name = call["connection_id"], call["tool"]
        groups[name].append(call)
        previous = prior.get(conn)
        arguments, detail = call["arguments"], call["detail"]
        if call["state"] == "complete" and call["outcome"] == "success" and name == "get_tasks":
            ids = arguments.get("ids", [])
            if previous and previous["outcome"] == "success" and previous["state"] == "complete":
                ack = previous["detail"]
                task_id = ack.get("task_id") or ack.get("id")
                if previous["tool"] in writes and task_id in ids and "revision" in ack:
                    candidate(
                        "read_after_ack",
                        previous,
                        call,
                        "Inspect whether the acknowledgement already supplied "
                        "what this read needed.",
                        task_id,
                    )
                if (
                    previous["tool"] == "get_tasks"
                    and arguments.get("specification")
                    and not previous["arguments"].get("specification")
                    and set(ids) & set(previous["arguments"].get("ids", []))
                ):
                    candidate(
                        "card_then_specification",
                        previous,
                        call,
                        "May be legitimate browsing; inspect whether the caller "
                        "already needed the full specification.",
                    )
            for item in detail.get("items", []):
                if (
                    not item.get("specification_complete")
                    or "body" not in item
                    or "acceptance_criteria" not in item
                ):
                    continue
                signature = hashlib.sha256(
                    json_bytes(
                        {key: item.get(key) for key in ("title", "body", "acceptance_criteria")}
                    )
                ).hexdigest()
                key = (conn, item.get("id"), arguments.get("workstream_id"))
                earlier = full_reads.get(key)
                if earlier and earlier[0] == signature:
                    candidate(
                        "unchanged_specification_reread",
                        earlier[1],
                        call,
                        "Requirements match the earlier read; review or human follow-up "
                        "may justify rereading.",
                        item.get("id"),
                    )
                full_reads[key] = (signature, call)
        error_key = (conn, name, tuple(call.get("start", {}).get("entity_refs", [])))
        if name and name not in {"notifications/initialized", "notifications/cancelled"}:
            failed = errors.pop(error_key, None)
            if failed:
                candidate(
                    "retry_after_error",
                    failed,
                    call,
                    "Same operation and explicit references after an error; "
                    "inspect reconciliation and retry cost.",
                )
            if call["outcome"] in {"error", "tool_error"}:
                errors[error_key] = call
            prior[conn] = call
    stats = {}
    for name, group in sorted(groups.items()):
        durations = [c["duration_ms"] for c in group if c["duration_ms"] is not None]
        stats[name] = {
            "calls": len(group),
            "outcomes": dict(Counter(c["outcome"] for c in group)),
            "request_bytes": sum(c["request_bytes"] or 0 for c in group),
            "result_bytes": sum(c["result_bytes"] or 0 for c in group),
            "p50_ms": percentile(durations, 0.5),
            "p95_ms": percentile(durations, 0.95),
        }
    sequence = [
        {
            key: c[key]
            for key in (
                "call_id",
                "connection_id",
                "timestamp",
                "tool",
                "sequence",
                "entity_refs",
                "state",
                "outcome",
                "duration_ms",
                "request_bytes",
                "result_bytes",
                "structured_content_bytes",
                "content_bytes",
                "truncated",
            )
        }
        for c in calls
    ]
    return {
        "boundary": (
            "Retained MCP boundary data only. JSON payload bytes exclude framing and are not "
            "model-context tokens. Durations exclude host delays and trace writes. "
            "Candidates are not misuse verdicts."
        ),
        "records_read": records,
        "invalid_records": invalid,
        "unsupported_records": unsupported,
        "connections": sorted({c["connection_id"] for c in calls}),
        "runtime_by_connection": {
            key: value
            for key, value in runtimes.items()
            if key in {c["connection_id"] for c in calls}
        },
        "reported_collection_failures": {
            key: value
            for key, value in collection_failures.items()
            if value and (connection is None or key == connection)
        },
        "call_count": len(calls),
        "incomplete_calls": sum(c["state"] == "incomplete" for c in calls),
        "truncated_calls": sum(c["truncated"] for c in calls),
        "observed_from": calls[0]["timestamp"] if calls else None,
        "observed_until": calls[-1]["timestamp"] if calls else None,
        "per_tool": stats,
        "candidates": candidates,
        "sequence": sequence,
        "largest_results": sorted(sequence, key=lambda c: c["result_bytes"] or 0, reverse=True)[
            :10
        ],
    }


def render(report):
    lines = [
        "Task MCP usage trace report",
        report["boundary"],
        f"Observed: {report['observed_from']} to {report['observed_until']}",
        f"Connections: {len(report['connections'])}; calls: {report['call_count']}; "
        f"incomplete: {report['incomplete_calls']}; truncated: {report['truncated_calls']}",
        f"Invalid records: {report['invalid_records']}; unsupported: "
        f"{report['unsupported_records']}; reported collection failures: "
        f"{sum(report['reported_collection_failures'].values())}",
        "",
        "Tool / method                         Calls Errors   Result bytes   p50 ms   p95 ms",
    ]
    for name, stats in report["per_tool"].items():
        errors = sum(stats["outcomes"].get(key, 0) for key in ("error", "tool_error"))
        lines.append(
            f"{name:<37} {stats['calls']:>5} {errors:>6} {stats['result_bytes']:>14} "
            f"{str(stats['p50_ms']):>8} {str(stats['p95_ms']):>8}"
        )
    lines.extend(["", "Potentially avoidable patterns (inspect before judging):"])
    for item in report["candidates"]:
        lines.append(f"- {item['kind']}: {' -> '.join(item['call_ids'])}: {item['note']}")
    if not report["candidates"]:
        lines.append("- None identified in retained data.")
    if report.get("detail_page", {}).get("candidate_has_more"):
        lines.append("- More candidates retained; use --offset/--limit or --all.")
    lines.extend(["", "Chronological calls:"])
    for call in report["sequence"]:
        lines.append(
            f"- {call['timestamp']} {call['tool']} {call['outcome']} "
            f"{call['request_bytes']} -> {call['result_bytes']} bytes ({call['call_id']})"
        )
    if report.get("detail_page", {}).get("sequence_has_more"):
        lines.append("- More calls retained; use --offset/--limit or --all.")
    lines.extend(["", "Largest results:"])
    for call in report["largest_results"]:
        lines.append(
            f"- {call['timestamp']} {call['tool']}: {call['result_bytes']} bytes "
            f"({call['call_id']})"
        )
    return "\n".join(lines) + "\n"


def page_report(report, limit=20, offset=0, include_all=False):
    if limit <= 0 or offset < 0:
        raise ValueError("report limit must be positive and offset nonnegative")
    result = {**report}
    result["candidate_counts"] = dict(Counter(item["kind"] for item in report["candidates"]))
    result["detail_page"] = {
        "offset": 0 if include_all else offset,
        "limit": None if include_all else limit,
        "sequence_total": len(report["sequence"]),
        "candidate_total": len(report["candidates"]),
        "sequence_has_more": not include_all and offset + limit < len(report["sequence"]),
        "candidate_has_more": not include_all and offset + limit < len(report["candidates"]),
    }
    if not include_all:
        for key in ("sequence", "candidates"):
            result[key] = report[key][offset : offset + limit]
    return result
