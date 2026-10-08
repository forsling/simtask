"""Persistent, authenticated MCP over loopback HTTP behind Tailscale Serve."""

import argparse
import fcntl
import os
import re
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount

from simtask.server import create_server
from simtask.store import Store, default_database
from simtask.tracing import TraceCollector, TraceConfig, default_trace_directory


def persistent_token(database: Path) -> str:
    """Create one credential per installation and serialize concurrent first starts."""
    path = Path(str(database.expanduser().resolve()) + ".mcp.token")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as handle:
        os.fchmod(handle.fileno(), 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        token = handle.read().strip()
        if token:
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                raise ValueError("Invalid private .mcp.token file; refusing to rotate it")
            return token
        token = secrets.token_urlsafe(32)
        handle.write(token + "\n")
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
        return token


class BearerGate:
    def __init__(self, app, token):
        self.app = app
        self.expected = ("Bearer " + token).encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            credentials = [v for k, v in scope["headers"] if k.lower() == b"authorization"]
            if len(credentials) != 1 or not secrets.compare_digest(credentials[0], self.expected):
                response = PlainTextResponse(
                    "Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(database: Path, public_origin: str, *, tracing=True):
    parsed = urlsplit(public_origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("--public-origin must be an HTTPS origin without a path or credentials")
    # Validate the port too; malformed origins must fail at startup.
    _ = parsed.port
    origin = public_origin.rstrip("/")
    security = TransportSecuritySettings(
        allowed_hosts=[parsed.netloc, "127.0.0.1:*"],
        allowed_origins=[origin],
    )
    apps = {}
    collectors = []
    for name, actor in (("codex", "codex-coordinator"), ("claude", "claude-code")):
        server = create_server(Store(database, actor), tracing=False)
        if tracing:
            collector = TraceCollector(TraceConfig(default_trace_directory(database)))
            server.middleware.insert(0, collector.middleware)
            collectors.append(collector)
        apps[name] = server.streamable_http_app(
            json_response=True, stateless_http=True, transport_security=security
        )

    @asynccontextmanager
    async def lifespan(app):
        # Stateless HTTP starts the MCP lifespan for each request. Trace writers
        # belong to the service instead, and must stay alive between requests.
        try:
            for collector in collectors:
                collector.start()
            async with AsyncExitStack() as stack:
                for child in apps.values():
                    await stack.enter_async_context(child.router.lifespan_context(child))
                yield
        finally:
            for collector in collectors:
                collector.close()

    app = Starlette(
        routes=[Mount("/" + name, app=child) for name, child in apps.items()], lifespan=lifespan
    )
    return BearerGate(app, persistent_token(database))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_database())
    parser.add_argument("--port", type=int, default=8789)
    parser.add_argument("--public-origin", required=True)
    args = parser.parse_args()
    if not 0 < args.port < 65536:
        parser.error("--port must be between 1 and 65535")
    try:
        app = create_app(args.db, args.public_origin)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    uvicorn.run(
        app, host="127.0.0.1", port=args.port, log_level="info", timeout_graceful_shutdown=5
    )


if __name__ == "__main__":
    main()
