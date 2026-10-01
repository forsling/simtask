import asyncio
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from task_mcp.runtime import PROTOCOL_SCHEMA_REVISION, source_identifier
from task_mcp.store import DATABASE_SCHEMA_REVISION, Store


def test_source_identifier_tracks_shipped_bytes_without_git_or_bytecode(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    source = package / "server.py"
    source.write_text("original")
    original = source_identifier(package)
    source.write_text("modified")
    assert source_identifier(package) != original
    source.write_text("original")
    (package / "__pycache__").mkdir()
    (package / "__pycache__/server.pyc").write_bytes(b"bytecode")
    assert source_identifier(package) == original
    (package / "reference_skills/init").mkdir(parents=True)
    (package / "reference_skills/init/SKILL.md").write_text("resource")
    assert source_identifier(package) != original


def test_database_revision_adopts_legacy_schema_and_rejects_future_schema(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    store = Store(database)
    setup = store.init_project(str(tmp_path), branch="main", confirmed=True)
    project_id = setup["project"]["id"]
    with sqlite3.connect(database) as db:
        before = db.execute("SELECT * FROM events").fetchall()
        db.execute("PRAGMA user_version=0")
    store = Store(database)
    assert store.database_schema_revision() == DATABASE_SCHEMA_REVISION
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT id FROM projects").fetchone()[0] == project_id
        assert db.execute("SELECT * FROM events").fetchall() == before
        db.execute(f"PRAGMA user_version={DATABASE_SCHEMA_REVISION + 1}")
    with pytest.raises(RuntimeError, match="newer than this server supports"):
        Store(database)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_REVISION + 1
        assert db.execute("SELECT * FROM events").fetchall() == before


def test_live_stdio_identity_is_read_only_frozen_and_changes_on_restart(tmp_path):
    package = tmp_path / "src/task_mcp"
    shutil.copytree(Path(__file__).resolve().parents[1] / "src/task_mcp", package)
    database = tmp_path / "tasks.sqlite3"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "task_mcp", "--db", str(database)],
        env={"PYTHONPATH": str(package.parent)},
    )

    async def exercise():
        before_start = datetime.now(UTC)
        async with Client(parameters, read_timeout_seconds=30) as client:

            async def call(name, **arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.content
                return result.structured_content

            with sqlite3.connect(database) as db:
                before = db.execute("SELECT * FROM events").fetchall()
            identity = await call("runtime_info")
            assert identity["package_version"] == version("task-mcp")
            assert identity["source_identifier"] == source_identifier(package)
            assert identity["protocol_schema_revision"] == PROTOCOL_SCHEMA_REVISION
            assert identity["database_schema_revision"] == DATABASE_SCHEMA_REVISION
            assert identity["process_id"] > 0
            assert identity["package_path"] == str(package)
            assert Path(identity["python_executable"]).resolve() == Path(sys.executable).resolve()
            started = datetime.fromisoformat(identity["process_started_at"])
            assert before_start <= started <= datetime.now(UTC)
            assert await call("runtime_info") == identity
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT * FROM events").fetchall() == before
            discovered = await call("init", path=str(tmp_path), branch="main")
            assert discovered["state"] == "unregistered_checkout"
            assert discovered["runtime"] == identity
            setup = await call(
                "init", path=str(tmp_path), branch="main", action="create_project", confirmed=True
            )
            assert setup["runtime"] == identity
            assert (await call("init", path=str(tmp_path), branch="main"))["runtime"] == identity
            # An editable installation can change on disk while this process
            # keeps its imported code. The diagnostic must retain its old identity.
            with (package / "server.py").open("a") as source:
                source.write("\n# Simulated editable-install update\n")
            assert source_identifier(package) != identity["source_identifier"]
            assert await call("runtime_info") == identity
        async with Client(parameters, read_timeout_seconds=30) as restarted:
            result = await restarted.call_tool("runtime_info", {})
            assert not result.is_error
            current = result.structured_content
            assert current["source_identifier"] == source_identifier(package)
            assert current["source_identifier"] != identity["source_identifier"]
            assert current["process_started_at"] > identity["process_started_at"]
            assert current["process_id"] != identity["process_id"]

    asyncio.run(exercise())
