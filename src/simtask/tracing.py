"""Bounded, private observations of the MCP boundary, outside the task ledger."""

import hashlib
import json
import logging
import os
import queue
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mcp_types import CLIENT_INFO_META_KEY

from simtask.runtime import RUNTIME_IDENTITY

TRACE_FORMAT_REVISION = 1
logger = logging.getLogger(__name__)


def utc_now(timestamp_ns=None):
    timestamp_ns = time.time_ns() if timestamp_ns is None else timestamp_ns
    return (
        datetime.fromtimestamp(timestamp_ns / 1e9, UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def json_bytes(value):
    """Compact, non-ASCII-escaped JSON; counts exclude JSON-RPC framing."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def default_trace_directory(database):
    database = Path(database)
    return database.with_name(database.name + ".traces")


@dataclass(frozen=True)
class TraceConfig:
    directory: Path
    max_age_days: float = 30
    max_bytes: int = 256 * 1024 * 1024
    payload_bytes: int = 1024 * 1024
    segment_bytes: int = 4 * 1024 * 1024
    queue_bytes: int = 8 * 1024 * 1024

    def __post_init__(self):
        if (
            self.max_age_days <= 0
            or min(self.max_bytes, self.payload_bytes, self.segment_bytes, self.queue_bytes) <= 0
        ):
            raise ValueError("trace limits must be positive")


@dataclass(frozen=True)
class CapturedPayload:
    """An immutable, already serialized capture ready to embed in a record."""

    encoded: bytes


def encoded_capture(encoded, limit):
    result = {
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "truncated": len(encoded) > limit,
    }
    if result["truncated"]:
        result["json_prefix"] = encoded[:limit].decode("utf-8", errors="ignore")
        envelope = json_bytes(result)
    else:
        envelope = json_bytes(result)[:-1] + b',"payload":' + encoded + b"}"
    return CapturedPayload(envelope)


def capture(value, limit):
    return json.loads(encoded_capture(json_bytes(value), limit).encoded)


def encode_record(record):
    # Captured payloads have already been serialized and frozen. Embedding
    # their JSON avoids decoding and re-encoding full specifications/evidence.
    captured = {key: value for key, value in record.items() if isinstance(value, CapturedPayload)}
    ordinary = {key: value for key, value in record.items() if key not in captured}
    encoded = json_bytes(ordinary)[:-1]
    for key, value in captured.items():
        encoded += b"," + json_bytes(key) + b":" + value.encoded
    return encoded + b"}\n"


def entity_references(value):
    """Bounded explicit references; never parse prose or infer a current project."""
    found = set()
    remaining = 200
    keys = {
        "id",
        "task_id",
        "project_id",
        "project",
        "workstream_id",
        "attempt_id",
        "group_id",
        "note_id",
        "ids",
        "attempt_ids",
    }

    def visit(item, depth=0):
        nonlocal remaining
        if remaining <= 0 or depth > 6:
            return
        remaining -= 1
        if isinstance(item, dict):
            for key, child in item.items():
                if key in keys:
                    values = child if isinstance(child, list) else [child]
                    for entry in values[:20]:
                        if isinstance(entry, str) and len(entry) <= 500:
                            found.add(entry)
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item[:20]:
                visit(child, depth + 1)

    visit(value)
    return sorted(found)[:100]


class TraceCollector:
    """One stdio connection, one daemon writer; full queues never gate a tool."""

    def __init__(self, config):
        self.config = config
        self.connection_id = uuid.uuid4().hex
        self._sequence = 0
        self._queue = queue.Queue()
        self._pending_bytes = 0
        self._guard = threading.Lock()
        self._thread = None
        self._closing = threading.Event()
        self._segment = 0
        self._path = None
        self._failures = 0
        self._last_warning = 0

    def _metadata(self):
        return {
            "runtime": RUNTIME_IDENTITY,
            "limits": {
                "max_age_days": self.config.max_age_days,
                "max_bytes": self.config.max_bytes,
                "payload_bytes": self.config.payload_bytes,
                "segment_bytes": self.config.segment_bytes,
                "queue_bytes": self.config.queue_bytes,
            },
        }

    def start(self):
        self._thread = threading.Thread(target=self._write_loop, daemon=True)
        try:
            self._thread.start()
        except RuntimeError as exc:
            self._thread = None
            self._closing.set()
            self._failed()
            logger.warning("simtask trace writer could not start: %s", exc)
            return
        self.emit({"event": "connection_start", **self._metadata()})

    def close(self):
        self.emit({"event": "connection_end"})
        self._closing.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _failed(self):
        with self._guard:
            self._failures += 1

    def emit(self, record):
        try:
            recorded_ns = time.time_ns()
            record = {
                "trace_format_revision": TRACE_FORMAT_REVISION,
                "connection_id": self.connection_id,
                "timestamp": utc_now(recorded_ns),
                **record,
            }
            encoded = encode_record(record)
            with self._guard:
                if (
                    self._closing.is_set()
                    or self._pending_bytes + len(encoded) > self.config.queue_bytes
                ):
                    self._failures += 1
                    return
                self._pending_bytes += len(encoded)
            self._queue.put_nowait((encoded, recorded_ns))
        except Exception:
            self._failed()

    def _write_loop(self):
        while not self._closing.is_set() or not self._queue.empty():
            try:
                encoded, recorded_ns = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                with self._guard:
                    failures = self._failures
                if failures:
                    encoded_to_write = (
                        encoded[:-2]
                        + b","
                        + json_bytes({"collection_failures_before_record": failures})[1:]
                        + b"\n"
                    )
                else:
                    encoded_to_write = encoded
                self._append(encoded_to_write, recorded_ns)
            except Exception as exc:
                self._failed()
                if time.monotonic() - self._last_warning >= 60:
                    logger.warning("simtask trace collection failed: %s", exc)
                    self._last_warning = time.monotonic()
            finally:
                with self._guard:
                    self._pending_bytes -= len(encoded)
                self._queue.task_done()

    @staticmethod
    def _segment_age_ns(path, info):
        parts = path.stem.split("-")
        if len(parts) == 4 and len(parts[1]) == 32 and parts[2].isdigit():
            return int(parts[2])
        # Older format-1 segments have no birth time in their name. Their first
        # record normally contains the immutable connection/observation time.
        try:
            with path.open("rb") as stream:
                first = json.loads(stream.readline(64 * 1024))
            timestamp = datetime.fromisoformat(first["timestamp"].replace("Z", "+00:00"))
            if timestamp.tzinfo is not None:
                return int(timestamp.timestamp() * 1e9)
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        return info.st_mtime_ns

    def _append(self, encoded, recorded_ns=None):
        # A shared lock protects the aggregate budget across server processes.
        # It runs on the daemon writer, never on the tool's execution path.
        import fcntl

        directory = self.config.directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise PermissionError("trace directory must be private to its owner")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        lock_fd = os.open(directory / ".retention.lock", flags, 0o600)
        try:
            if os.fstat(lock_fd).st_uid != os.getuid():
                raise PermissionError("trace lock has a different owner")
            deadline = time.monotonic() + 0.25
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("trace retention lock busy") from None
                    time.sleep(0.005)
            now_ns = time.time_ns()
            recorded_ns = now_ns if recorded_ns is None else recorded_ns
            oldest = now_ns - self.config.max_age_days * 86400 * 1e9
            if recorded_ns < oldest:
                raise ValueError("trace observation expired before storage")
            files = []
            for path in directory.glob("trace-*.jsonl"):
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    continue
                age_ns = self._segment_age_ns(path, info)
                if age_ns < oldest:
                    path.unlink()
                else:
                    files.append((age_ns, path.name, path, info.st_size))
            total = sum(entry[3] for entry in files)
            header = (
                json_bytes(
                    {
                        "trace_format_revision": TRACE_FORMAT_REVISION,
                        "connection_id": self.connection_id,
                        "timestamp": utc_now(recorded_ns),
                        "event": "segment_start",
                        **self._metadata(),
                    }
                )
                + b"\n"
            )
            if len(encoded) + len(header) > self.config.max_bytes:
                raise ValueError("trace record exceeds aggregate storage budget")
            new_segment = (
                self._path is None
                or not self._path.exists()
                or self._path.stat().st_size + len(encoded) > self.config.segment_bytes
            )
            for _, _, path, size in sorted(files):
                required = len(encoded) + (len(header) if new_segment else 0)
                if total + required <= self.config.max_bytes:
                    break
                path.unlink()
                total -= size
                if path == self._path:
                    new_segment = True
            if new_segment:
                self._segment += 1
                self._path = directory / (
                    f"trace-{self.connection_id}-{recorded_ns:020d}-{self._segment:06d}.jsonl"
                )
                encoded = header + encoded
            fd = os.open(
                self._path,
                os.O_CREAT | os.O_APPEND | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                if os.fstat(fd).st_uid != os.getuid():
                    raise PermissionError("trace file has a different owner")
                os.fchmod(fd, 0o600)
                view = memoryview(encoded)
                while view:
                    written = os.write(fd, view)
                    if not written:
                        raise OSError("trace write made no progress")
                    view = view[written:]
            finally:
                os.close(fd)
        finally:
            os.close(lock_fd)

    async def middleware(self, ctx, call_next):
        self._sequence += 1
        call_id = f"{self.connection_id}:{self._sequence}"
        params = ctx.params if isinstance(ctx.params, dict) else {}
        tool = params.get("name") if ctx.method == "tools/call" else None
        base = {
            "call_id": call_id,
            "sequence": self._sequence,
            "request_id": ctx.request_id,
            "method": ctx.method,
            "tool": tool if isinstance(tool, str) else None,
            "protocol_version": ctx.protocol_version,
        }
        try:
            request = {"method": ctx.method, "params": ctx.params}
            client_info = params.get("clientInfo") if ctx.method == "initialize" else None
            if client_info is None and isinstance(params.get("_meta"), dict):
                client_info = params["_meta"].get(CLIENT_INFO_META_KEY)
            if client_info is None and ctx.session.client_params is not None:
                client_info = ctx.session.client_params.client_info.model_dump(
                    mode="json", by_alias=True
                )
            if isinstance(client_info, dict):
                client_info = {
                    key: str(client_info[key])[:240]
                    for key in ("name", "version")
                    if key in client_info
                }
            self.emit(
                {
                    **base,
                    "event": "request_start",
                    "client_info": client_info,
                    "entity_refs": entity_references(ctx.params),
                    "request": encoded_capture(json_bytes(request), self.config.payload_bytes),
                }
            )
        except Exception:
            self._failed()
        started = time.perf_counter_ns()
        try:
            result = await call_next(ctx)
        except BaseException as exc:
            elapsed = (time.perf_counter_ns() - started) / 1e6
            try:
                import anyio

                cancelled = isinstance(exc, anyio.get_cancelled_exc_class())
                self.emit(
                    {
                        **base,
                        "event": "request_finish",
                        "duration_ms": elapsed,
                        "outcome": "cancelled" if cancelled else "error",
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc)[:4096],
                            "message_truncated": len(str(exc)) > 4096,
                            "code": getattr(exc, "code", None),
                        },
                    }
                )
            except Exception:
                self._failed()
            raise
        elapsed = (time.perf_counter_ns() - started) / 1e6
        try:
            record = {
                **base,
                "event": "request_finish",
                "duration_ms": elapsed,
                "outcome": "notification" if ctx.request_id is None else "success",
            }
            if ctx.request_id is not None:
                if hasattr(result, "model_dump"):
                    shaped = result.model_dump(mode="json", by_alias=True, exclude_none=True)
                else:
                    shaped = result
                if isinstance(shaped, dict):
                    if shaped.get("isError"):
                        record["outcome"] = "tool_error"
                    fields = {key: json_bytes(value) for key, value in shaped.items()}
                    if "structuredContent" in fields:
                        record["structured_content_bytes"] = len(fields["structuredContent"])
                    if "content" in fields:
                        record["content_bytes"] = len(fields["content"])
                    record["entity_refs"] = entity_references(shaped)
                    encoded_result = (
                        b"{"
                        + b",".join(json_bytes(key) + b":" + value for key, value in fields.items())
                        + b"}"
                    )
                else:
                    encoded_result = json_bytes(shaped)
                record["result"] = encoded_capture(encoded_result, self.config.payload_bytes)
            self.emit(record)
        except Exception:
            self._failed()
        return result
