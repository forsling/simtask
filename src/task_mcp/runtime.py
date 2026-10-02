"""Identity captured once when the server runtime is imported, never per request."""

import hashlib
import os
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# Bump when the public Task MCP tool/result contract changes incompatibly.
# This is the application schema, independent of the negotiated MCP wire version.
PROTOCOL_SCHEMA_REVISION = 2
PROCESS_STARTED_AT = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def source_identifier(root: Path) -> str:
    """Fingerprint shipped Python and resource bytes, including uncommitted edits."""
    digest = hashlib.sha256()
    paths = set(root.rglob("*.py"))
    for directory in ("reference_skills", "viewer_assets"):
        paths.update(path for path in (root / directory).rglob("*") if path.is_file())
    for path in sorted(paths):
        name = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _identity() -> dict:
    root = Path(__file__).resolve().parent
    try:
        package_version = version("task-mcp")
    except PackageNotFoundError:
        package_version = "unknown (package metadata unavailable)"
    return {
        "package_version": package_version,
        "source_identifier": source_identifier(root),
        "protocol_schema_revision": PROTOCOL_SCHEMA_REVISION,
        "process_started_at": PROCESS_STARTED_AT,
        "process_id": os.getpid(),
        "python_executable": sys.executable,
        "package_path": str(root),
    }


RUNTIME_IDENTITY = _identity()
