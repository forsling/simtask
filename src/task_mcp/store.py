"""Local SQLite task state and audit history."""

import base64
import hashlib
import json
import os
import shlex
import sqlite3
from collections.abc import Callable
from contextlib import closing
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from task_mcp.export import FORMAT, render_markdown

DATABASE_SCHEMA_REVISION = 9
COMPACT_CALL = ContextVar("compact_task_mcp_call", default=False)
# Concerns have explicit provenance in their own column, never in arbitrary proof text.
CONCERN_COUNT_SQL = "json_array_length(concerns_json)"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, canonical_path TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL, order_revision INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS project_paths (
        path TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id))""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id),
        title TEXT NOT NULL, body TEXT NOT NULL, acceptance_criteria TEXT NOT NULL,
        status TEXT NOT NULL, object_type TEXT NOT NULL, spec_revision INTEGER NOT NULL,
        accepted_spec_revision INTEGER, acceptance_note TEXT NOT NULL DEFAULT '',
        unresolved_json TEXT NOT NULL, parent_group_id TEXT REFERENCES tasks(id),
        order_key INTEGER NOT NULL, selected_attempt_id TEXT,
        revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'unknown', user_request TEXT NOT NULL DEFAULT '',
        acceptance_basis TEXT NOT NULL DEFAULT 'unknown',
        summary TEXT, summary_spec_revision INTEGER)""",
    "CREATE INDEX IF NOT EXISTS task_board ON tasks(project_id, order_key, id)",
    """CREATE TABLE IF NOT EXISTS workstreams (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        name TEXT NOT NULL, branch TEXT, checkout_path TEXT,
        revision INTEGER NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(project_id, name), UNIQUE(project_id, branch))""",
    """CREATE TABLE IF NOT EXISTS scope_members (
        workstream_id TEXT NOT NULL REFERENCES workstreams(id),
        task_id TEXT NOT NULL REFERENCES tasks(id),
        PRIMARY KEY(workstream_id, task_id))""",
    """CREATE TABLE IF NOT EXISTS scope_groups (
        workstream_id TEXT NOT NULL REFERENCES workstreams(id),
        group_id TEXT NOT NULL REFERENCES tasks(id),
        PRIMARY KEY(workstream_id, group_id))""",
    """CREATE TABLE IF NOT EXISTS scope_exclusions (
        workstream_id TEXT NOT NULL REFERENCES workstreams(id),
        task_id TEXT NOT NULL REFERENCES tasks(id),
        PRIMARY KEY(workstream_id, task_id))""",
    """CREATE TABLE IF NOT EXISTS prerequisites (
        task_id TEXT NOT NULL REFERENCES tasks(id),
        blocked_by_id TEXT NOT NULL REFERENCES tasks(id),
        milestone TEXT NOT NULL DEFAULT 'review' CHECK(milestone IN ('review','signoff')),
        PRIMARY KEY(task_id, blocked_by_id))""",
    """CREATE TABLE IF NOT EXISTS gate_proposals (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        gate_type TEXT NOT NULL, detail TEXT NOT NULL, proposer TEXT NOT NULL,
        created_at TEXT NOT NULL,
        milestone TEXT NOT NULL DEFAULT 'review' CHECK(milestone IN ('review','signoff')))""",
    """CREATE TABLE IF NOT EXISTS attempts (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        workstream_id TEXT NOT NULL REFERENCES workstreams(id),
        implementer TEXT NOT NULL, summary TEXT NOT NULL, evidence TEXT NOT NULL,
        spec_revision INTEGER NOT NULL, state TEXT NOT NULL, reviewer TEXT,
        review_note TEXT, human_review_note TEXT, revision INTEGER NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        concerns_json TEXT NOT NULL DEFAULT '[]'
        CHECK(json_valid(concerns_json) AND json_type(concerns_json)='array'))""",
    "CREATE INDEX IF NOT EXISTS attempt_task ON attempts(task_id, created_at, id)",
    """CREATE TABLE IF NOT EXISTS events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        actor TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL,
        project_id TEXT, task_id TEXT, request_json TEXT NOT NULL,
        before_json TEXT, after_json TEXT, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS task_history ON events(task_id, sequence)",
    "CREATE INDEX IF NOT EXISTS project_history ON events(project_id, sequence)",
    """CREATE INDEX IF NOT EXISTS rejection_history ON events(task_id, sequence)
        WHERE outcome='ok' AND ((action='attempt.reviewed'
        AND json_extract(request_json,'$.verdict')='rework') OR (action='task.signoff'
        AND json_extract(request_json,'$.decision') IN ('rework','revise')))""",
    """CREATE TABLE IF NOT EXISTS legacy_membership_migration (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id), source_schema INTEGER NOT NULL,
        task_json TEXT NOT NULL, scopes_json TEXT NOT NULL,
        unresolved_id TEXT)""",
)


class TaskError(ValueError):
    """An actionable domain error safe to show to the caller."""


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def default_database() -> Path:
    if configured := os.environ.get("TASK_MCP_DB"):
        return Path(configured).expanduser().absolute()
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "task-mcp" / "tasks.sqlite3"


def _id(prefix):
    return prefix + uuid4().hex


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    """Explicit projects, workstream scopes, nonexclusive memberships and attempts."""

    def __init__(self, path: Path, actor: str = "local-agent"):
        self.path = path.expanduser().absolute()
        self.actor = actor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migration_backup_path: Path | None = None
        with closing(self._connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > DATABASE_SCHEMA_REVISION:
                raise RuntimeError("task database schema is newer than this server supports")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("BEGIN IMMEDIATE")
            try:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version > DATABASE_SCHEMA_REVISION:
                    raise RuntimeError("task database schema is newer than this server supports")
                existing = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
                ).fetchone()
                if existing and version < DATABASE_SCHEMA_REVISION:
                    # The writer lock prevents a commit between this online snapshot
                    # and the migration. A separate read connection includes WAL data.
                    self.migration_backup_path = self._backup_for_migration(version)
                self._upgrade_schema(db)
                db.execute(f"PRAGMA user_version={DATABASE_SCHEMA_REVISION}")
                db.commit()
            except Exception:
                db.rollback()
                raise
            db.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _upgrade_schema(db):
        source_version = db.execute("PRAGMA user_version").fetchone()[0]
        for statement in SCHEMA:
            if not statement.startswith("CREATE TRIGGER"):
                db.execute(statement)
        project_columns = {row["name"] for row in db.execute("PRAGMA table_info(projects)")}
        if "order_revision" not in project_columns:
            db.execute("ALTER TABLE projects ADD COLUMN order_revision INTEGER NOT NULL DEFAULT 0")
        for table in ("prerequisites", "gate_proposals"):
            columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            if "milestone" not in columns:
                db.execute(
                    f"ALTER TABLE {table} ADD COLUMN milestone TEXT NOT NULL DEFAULT 'review' "
                    "CHECK(milestone IN ('review','signoff'))"
                )
        columns = {row["name"]: row for row in db.execute("PRAGMA table_info(tasks)")}
        for name, definition in (
            ("source", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("user_request", "TEXT NOT NULL DEFAULT ''"),
            ("acceptance_basis", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("summary", "TEXT"),
            ("summary_spec_revision", "INTEGER"),
        ):
            if name not in columns:
                db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
        attempt_columns = {row["name"] for row in db.execute("PRAGMA table_info(attempts)")}
        if "concerns_json" not in attempt_columns:
            # Historical evidence is arbitrary text, including JSON that resembles
            # metadata. Adding an empty column preserves every byte and invents none.
            db.execute(
                "ALTER TABLE attempts ADD COLUMN concerns_json TEXT NOT NULL DEFAULT '[]' "
                "CHECK(json_valid(concerns_json) AND json_type(concerns_json)='array')"
            )
        if columns["project_id"]["notnull"]:
            db.execute(
                SCHEMA[2].replace("CREATE TABLE IF NOT EXISTS tasks (", "CREATE TABLE tasks_new (")
            )
            names = [row["name"] for row in db.execute("PRAGMA table_info(tasks)")]
            columns_sql = ",".join(names)
            db.execute(f"INSERT INTO tasks_new ({columns_sql}) SELECT {columns_sql} FROM tasks")
            db.execute("DROP TRIGGER IF EXISTS queue_member_valid_insert")
            db.execute("DROP TRIGGER IF EXISTS queue_member_valid_update")
            db.execute("DROP TABLE tasks")
            db.execute("ALTER TABLE tasks_new RENAME TO tasks")
            db.execute(SCHEMA[3])
        if source_version < 9:
            Store._migrate_membership(db, source_version)
        for statement in SCHEMA:
            if statement.startswith("CREATE TRIGGER"):
                db.execute(statement)
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("task database has invalid foreign keys")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("task database failed integrity check")

    @staticmethod
    def _migrate_membership(db, source_version):
        """Restore original scopes; keep pending intent as an ordinary open question."""
        has_queue = bool(
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='queue_members' AND type='table'"
            ).fetchone()
        )
        has_archive = bool(
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='legacy_queue_migration' AND type='table'"
            ).fetchone()
        )
        rows = db.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        for row in rows:
            task = dict(row)
            archive = (
                db.execute(
                    "SELECT * FROM legacy_queue_migration WHERE task_id=?", (task["id"],)
                ).fetchone()
                if has_archive
                else None
            )
            archived = json.loads(archive["scopes_json"]) if archive else {}
            # Retained scope rows are authoritative. The rejected migration archive
            # repairs any missing rows without selecting or transferring ownership.
            for entry in archived.get("members", []):
                db.execute(
                    "INSERT OR IGNORE INTO scope_members VALUES (?,?)",
                    (entry["workstream_id"], task["id"]),
                )
            for entry in archived.get("groups", []):
                db.execute(
                    "INSERT OR IGNORE INTO scope_groups VALUES (?,?)",
                    (entry["workstream_id"], entry["group_id"]),
                )
            for entry in archived.get("exclusions", []):
                db.execute(
                    "INSERT OR IGNORE INTO scope_exclusions VALUES (?,?)",
                    (entry["workstream_id"], entry["task_id"]),
                )
        # Preserve explicit additions made in the isolated rejected candidate too.
        if has_queue:
            conflict = db.execute(
                "SELECT q.task_id,q.workstream_id FROM queue_members q "
                "JOIN scope_exclusions e ON e.task_id=q.task_id "
                "AND e.workstream_id=q.workstream_id LIMIT 1"
            ).fetchone()
            if conflict:
                raise RuntimeError(
                    "membership_migration_conflict: candidate membership contradicts a "
                    f"retained exclusion for {conflict['task_id']} in "
                    f"{conflict['workstream_id']}; resolve the intended scope on a copy"
                )
            db.execute(
                "INSERT OR IGNORE INTO scope_members "
                "SELECT workstream_id,task_id FROM queue_members"
            )
        for row in rows:
            task = dict(row)
            memberships = Store._membership_ids(db, task)
            facts = {
                name: [
                    dict(r)
                    for r in db.execute(
                        f"SELECT * FROM {table} WHERE {column}=? ORDER BY workstream_id",
                        (task["id"],),
                    )
                ]
                for name, table, column in (
                    ("members", "scope_members", "task_id"),
                    ("groups", "scope_groups", "group_id"),
                    ("exclusions", "scope_exclusions", "task_id"),
                )
            }
            facts["effective_workstream_ids"] = memberships
            unresolved_id = None
            # Legacy pending scope was context, not permission to execute. Keep
            # every membership and require an explicit normal gate resolution.
            archive = (
                db.execute(
                    "SELECT reason FROM legacy_queue_migration WHERE task_id=?", (task["id"],)
                ).fetchone()
                if has_archive
                else None
            )
            pending_intent = (
                task["accepted_spec_revision"] != task["spec_revision"]
                if source_version < 6
                else bool(archive and archive["reason"] == "legacy_unapproved_to_inbox")
            )
            if (
                pending_intent
                and memberships
                and task["object_type"] == "task"
                and task["status"] not in {"done", "dropped"}
            ):
                unresolved_id = "unr_membership_migration_" + task["id"]
                questions = json.loads(task["unresolved_json"])
                questions.append(
                    {
                        "id": unresolved_id,
                        "text": "Confirm legacy pending intent before implementation. Memberships "
                        "are preserved; resolve this question after the user decides whether "
                        "to proceed, defer or remove the task from a named workstream.",
                    }
                )
                db.execute(
                    "UPDATE tasks SET unresolved_json=?,revision=revision+1,"
                    "updated_at=? WHERE id=?",
                    (_json(questions), timestamp(), task["id"]),
                )
            db.execute(
                "INSERT INTO legacy_membership_migration VALUES (?,?,?,?,?)",
                (task["id"], source_version, _json(task), _json(facts), unresolved_id),
            )
        if has_queue:
            db.execute("DROP TRIGGER IF EXISTS queue_member_valid_insert")
            db.execute("DROP TRIGGER IF EXISTS queue_member_valid_update")
            db.execute("DROP TABLE queue_members")

    def _backup_for_migration(self, version: int) -> Path:
        """Create and verify a fresh SQLite online backup before changing schema."""
        backup = self.path.with_name(
            f"{self.path.name}.pre-schema-{DATABASE_SCHEMA_REVISION}.{uuid4().hex}.sqlite3"
        )
        # Never replace an earlier backup and keep private task contents private.
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with (
                closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source,
                closing(sqlite3.connect(backup)) as destination,
            ):
                source.backup(destination)
                if (
                    destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or destination.execute("PRAGMA foreign_key_check").fetchone()
                    or destination.execute("PRAGMA user_version").fetchone()[0] != version
                ):
                    raise RuntimeError("pre-migration backup failed verification")
            return backup
        except Exception:
            backup.unlink(missing_ok=True)
            raise

    def database_schema_revision(self) -> int:
        """Read the actual persisted revision without an audit or any database write."""
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            return db.execute("PRAGMA user_version").fetchone()[0]

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _event(self, db, action, request, scope, outcome, error=None):
        cursor = db.execute(
            """INSERT INTO events
            (timestamp, actor, action, outcome, project_id, task_id, request_json,
             before_json, after_json, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp(),
                self.actor,
                action,
                outcome,
                scope.get("project_id"),
                scope.get("task_id"),
                json.dumps(request, ensure_ascii=False),
                json.dumps(scope["before"], ensure_ascii=False) if "before" in scope else None,
                json.dumps(scope["after"], ensure_ascii=False) if "after" in scope else None,
                error,
            ),
        )

        return cursor.lastrowid

    def _run(self, action: str, request: dict, operation: Callable) -> Any:
        scope: dict = {}
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                result = operation(db, scope)
                if COMPACT_CALL.get():
                    result = self._mutation_ack(db, action, result, request, scope)
                decision_ref = self._event(db, action, request, scope, "ok")
                db.commit()
                if action == "task.signoff":
                    result = {**result, "decision_ref": decision_ref}
                return result
            except TaskError as exc:
                db.rollback()
                db.execute("BEGIN IMMEDIATE")
                self._event(db, action, request, scope, "error", str(exc))
                db.commit()
                raise
            except Exception:
                db.rollback()
                raise

    def compact_call(self, method, *args, **kwargs):
        """Project writes inside their transaction; internal clients keep full detail."""
        token = COMPACT_CALL.set(True)
        try:
            return getattr(self, method)(*args, **kwargs)
        finally:
            COMPACT_CALL.reset(token)

    def _mutation_ack(self, db, action, result, request, scope):
        if action in {
            "project.initialized",
            "checkout.attached",
            "workstream.initialized",
            "workstream.rebound",
            "scope.changed",
        }:
            if action == "scope.changed":
                return {k: result[k] for k in ("id", "project_id", "revision")} | {
                    "workstream_id": result["id"],
                    "workstream_revision": result["revision"],
                    "changed": result["changed"],
                    **{k + "_count": len(result[k]) for k in ("members", "groups", "exclusions")},
                }
            if action == "workstream.initialized":
                return {
                    "workstream": result["workstream"],
                    "changed": True,
                    **{k + "_count": len(result[k]) for k in ("members", "groups", "exclusions")},
                }
            return result | {"changed": result.get("changed", True)}
        if action.startswith("attempt.") and action in {
            "attempt.recorded",
            "attempt.reviewed",
            "attempt.human_reviewed",
        }:
            if action == "attempt.recorded":
                return result | {
                    "attempt_id": result["id"],
                    "attempt_revision": result["revision"],
                    "workstream_id": request["workstream_id"],
                    "changed": True,
                }
            task = self._task(db, result["task_id"], {})
            return {
                k: result[k]
                for k in ("id", "revision", "task_id", "workstream_id", "spec_revision", "state")
            } | {
                "attempt_id": result["id"],
                "attempt_revision": result["revision"],
                "task_revision": task["revision"],
                "task_spec_revision": task["spec_revision"],
                "changed": True,
                "workstream_ids": Store._membership_ids(db, task),
                "adopted": bool(Store._membership_ids(db, task)),
                "status": task["status"],
                "concern_count": len(result.get("concerns", [])),
                "gate_diagnostics": [
                    r
                    for r in self._gate_reasons(db, task, result["workstream_id"])
                    if r != "closed_or_group"
                ],
            }
        if action == "group.member_added":
            return {
                "group": self._task_ack(db, result["group"]),
                "member": self._task_ack(db, result["member"]),
                "changed": True,
            }
        if action == "group.created":
            return self._task_ack(db, result) | {
                "changed": True,
                "complete": result["complete"],
                "workstream_id": request["workstream_id"],
                "workstream_revision": self._workstream(db, request["workstream_id"])["revision"],
                "specification_etag": result["specification_etag"],
            }
        if action == "task.decomposed":
            return self._task_ack(db, result) | {
                "changed": True,
                "members": [{"id": identity, "revision": 1} for identity in result["members"]],
                "workstreams": [
                    {"id": r["id"], "revision": r["revision"]}
                    for r in db.execute(
                        "SELECT w.id,w.revision FROM workstreams w JOIN scope_groups s "
                        "ON s.workstream_id=w.id WHERE s.group_id=? ORDER BY w.id",
                        (result["id"],),
                    )
                ],
                "project_order_revision": self._order_revision(db, scope["project_id"]),
            }
        if action == "gate.prerequisite_proposed":
            ack = {
                "task": self._task_ack(db, result["task"]),
                "proposal": self._task_ack(db, result["proposal"]),
                "changed": True,
                "project_order_revision": self._order_revision(db, scope["project_id"]),
            }
            if request["workstream_id"]:
                ack.update(
                    workstream_id=request["workstream_id"],
                    workstream_revision=self._workstream(db, request["workstream_id"])["revision"],
                )
            return ack
        if action.startswith("gate."):
            task = self._task(db, scope["task_id"], {})
            ack = self._task_ack(db, task) | {
                "task_id": task["id"],
                "task_revision": task["revision"],
                "changed": result.get("changed", True),
            }
            if request.get("handling") == "observer":
                ack.update(proposal_id=result["id"], gate_type=result["gate_type"])
            if action == "gate.unresolved_added" and request.get("handling") != "observer":
                ack["unresolved_id"] = task["unresolved_items"][-1]["id"]
            if action == "gate.unresolved_resolved":
                ack["resolved_id"] = request["item_id"]
            if request.get("blocked_by_id"):
                ack["blocked_by_id"] = request["blocked_by_id"]
            if request.get("proposal_id"):
                ack["proposal_id"] = request["proposal_id"]
                if action == "gate.proposal_accepted" and len(task["unresolved_items"]) > len(
                    scope["before"]["unresolved_items"]
                ):
                    ack["unresolved_id"] = task["unresolved_items"][-1]["id"]
            return ack
        if action.startswith("task.") and action in {
            "task.created",
            "task.updated",
            "task.workstream_added",
            "task.workstream_removed",
            "task.disposition_changed",
            "task.signoff",
            "task.reordered",
        }:
            return result | {"changed": result.get("changed", True)}
        return result

    @staticmethod
    def _revision(task: dict, expected_revision: int):
        if type(expected_revision) is not int or task["revision"] != expected_revision:
            raise TaskError(
                f"revision_conflict: expected {expected_revision}, current {task['revision']}; "
                "re-read the full specification with get_tasks(specification=true), "
                "reconcile your edit, "
                "and retry with the current revision and specification_etag for body/criteria"
            )

    @staticmethod
    def _page(limit, offset):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TaskError("invalid_limit: use 1 through 100")
        if type(offset) is not int or offset < 0:
            raise TaskError("invalid_offset: use a nonnegative integer")

    @staticmethod
    def _paged(rows, limit, offset):
        return {"items": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}

    def list_projects(self, limit=50, offset=0):
        def operation(db, scope):
            self._page(limit, offset)
            rows = db.execute(
                """SELECT p.*, count(t.id) AS task_count FROM projects p LEFT JOIN tasks t
                ON p.id=t.project_id AND t.object_type='task'
                GROUP BY p.id ORDER BY p.name, p.id LIMIT ? OFFSET ?""",
                (limit + 1, offset),
            ).fetchall()
            return self._paged([dict(row) for row in rows], limit, offset)

        return self._run("projects.listed", dict(limit=limit, offset=offset), operation)

    def list_events(
        self,
        project=None,
        task_id=None,
        after_sequence=0,
        through_sequence=None,
        limit=20,
        include_details=False,
    ):
        request = dict(
            project=project,
            task_id=task_id,
            after_sequence=after_sequence,
            through_sequence=through_sequence,
            limit=limit,
            include_details=include_details,
        )

        def operation(db, scope):
            self._page(limit, after_sequence)
            ceiling = db.execute("SELECT coalesce(max(sequence), 0) FROM events").fetchone()[0]
            if through_sequence is None:
                through = ceiling
            elif type(through_sequence) is int and through_sequence >= 0:
                through = min(through_sequence, ceiling)
            else:
                raise TaskError("invalid_sequence: through_sequence must be nonnegative")
            where, values = ["sequence>?", "sequence<=?"], [after_sequence, through]
            if project is not None:
                project_id = self._project(db, project, scope)["id"]
                where.append("project_id=?")
                values.append(project_id)
            if task_id is not None:
                self._task(db, task_id, scope)
                where.append(
                    "(task_id=? OR EXISTS (SELECT 1 FROM "
                    "json_each(events.request_json, '$.ids') WHERE value=?))"
                )
                values.extend([task_id, task_id])
            columns = (
                "*"
                if include_details
                else "sequence, timestamp, actor, action, outcome, project_id, task_id, error"
            )
            rows = [
                dict(row)
                for row in db.execute(
                    f"SELECT {columns} FROM events WHERE {' AND '.join(where)} "
                    "ORDER BY sequence LIMIT ?",
                    (*values, limit + 1),
                ).fetchall()
            ]
            if include_details:
                for row in rows:
                    for field in ("request", "before", "after"):
                        raw = row.pop(field + "_json")
                        row[field] = json.loads(raw) if raw is not None else None
            return {
                "items": rows[:limit],
                "through_sequence": through,
                "next_after_sequence": rows[limit - 1]["sequence"] if len(rows) > limit else None,
            }

        return self._run("events.listed", request, operation)

    def _project(self, db, project, scope):
        if not isinstance(project, str) or not project.strip():
            raise TaskError("project_required: provide a project ID, path, or unique name")
        value = project.strip()
        if Path(value).is_absolute():
            rows = db.execute(
                "SELECT p.* FROM projects p JOIN project_paths a ON a.project_id=p.id "
                "WHERE a.path=?",
                (str(Path(value).resolve()),),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM projects WHERE id=? OR name=?", (value, value)
            ).fetchall()
        if len(rows) > 1:
            raise TaskError("ambiguous_project: use its stable project ID or path")
        if not rows:
            raise TaskError("project_not_initialized: use init_project before task operations")
        result = dict(rows[0])
        scope["project_id"] = result["id"]
        return result

    @staticmethod
    def _workstream(db, workstream_id, project_id=None):
        row = db.execute("SELECT * FROM workstreams WHERE id=?", (workstream_id,)).fetchone()
        if row is None or (project_id and row["project_id"] != project_id):
            raise TaskError("unknown_workstream: initialize or select a workstream")
        return dict(row)

    def init_project(self, path, branch=None, workstream_name=None, confirmed=False):
        request = locals().copy()
        request.pop("self")

        def operation(db, scope):
            if not confirmed:
                raise TaskError(
                    "confirmation_required: show the canonical path and workstream to the user"
                )
            canonical = self._canonical_path(path)
            if not branch and not workstream_name:
                raise TaskError(
                    "workstream_name_required: detached HEAD and non-Git use need a name"
                )
            if db.execute("SELECT 1 FROM project_paths WHERE path=?", (canonical,)).fetchone():
                raise TaskError("project_initialized: use preflight or attach_checkout")
            now = timestamp()
            project = dict(
                id=_id("prj_"),
                name=Path(canonical).name,
                canonical_path=canonical,
                created_at=now,
            )
            db.execute(
                "INSERT INTO projects (id,name,canonical_path,created_at) "
                "VALUES (:id,:name,:canonical_path,:created_at)",
                project,
            )
            db.execute("INSERT INTO project_paths VALUES (?, ?)", (canonical, project["id"]))
            ws = self._insert_workstream(
                db, project["id"], workstream_name or branch, branch, canonical
            )
            scope.update(project_id=project["id"], after={"project": project, "workstream": ws})
            return {"project": project, "workstream": ws}

        return self._run("project.initialized", request, operation)

    @staticmethod
    def _canonical_path(path):
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise TaskError("absolute_path_required: provide an absolute checkout path")
        return str(Path(path).resolve())

    @staticmethod
    def _insert_workstream(db, project_id, name, branch, checkout_path):
        if not isinstance(name, str) or not name.strip():
            raise TaskError("workstream_name_required")
        ws = dict(
            id=_id("wst_"),
            project_id=project_id,
            name=name.strip(),
            branch=branch,
            checkout_path=checkout_path,
            revision=1,
            created_at=timestamp(),
        )
        try:
            db.execute(
                """INSERT INTO workstreams
                (id,project_id,name,branch,checkout_path,revision,created_at)
                VALUES (:id,:project_id,:name,:branch,:checkout_path,:revision,:created_at)""",
                ws,
            )
        except sqlite3.IntegrityError as exc:
            raise TaskError("workstream_exists: choose a distinct name and branch") from exc
        return ws

    def attach_checkout(self, project, path, confirmed=False):
        request = dict(project=project, path=path, confirmed=confirmed)

        def operation(db, scope):
            if not confirmed:
                raise TaskError("confirmation_required: show the attachment to the user")
            selected = self._project(db, project, scope)
            canonical = self._canonical_path(path)
            row = db.execute(
                "SELECT project_id FROM project_paths WHERE path=?", (canonical,)
            ).fetchone()
            if row and row["project_id"] != selected["id"]:
                raise TaskError("checkout_bound_elsewhere")
            db.execute(
                "INSERT OR IGNORE INTO project_paths VALUES (?, ?)", (canonical, selected["id"])
            )
            scope["after"] = {"path": canonical, "project_id": selected["id"]}
            return scope["after"] | {"changed": row is None}

        return self._run("checkout.attached", request, operation)

    def list_workstreams(self, project=None, limit=50, offset=0):
        def operation(db, scope):
            self._page(limit, offset)
            selected = self._project(db, project, scope) if project is not None else None
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT w.*, p.name AS project_name, p.canonical_path AS project_path "
                    "FROM workstreams w JOIN projects p ON p.id=w.project_id "
                    + ("WHERE w.project_id=? " if selected else "")
                    + "ORDER BY p.name,p.id,w.created_at,w.id LIMIT ? OFFSET ?",
                    ((selected["id"],) if selected else ()) + (limit + 1, offset),
                )
            ]
            for row in rows:
                all_ids = self._scope_ids(db, row["id"])
                row["scope"] = all_ids[:3]
                groups = self._scope_group_ids(db, row["id"])
                row["groups"] = groups[:3]
                row["scope_total"] = len(all_ids)
                row["scope_has_more"] = len(all_ids) > 3
                row["groups_has_more"] = len(groups) > 3
                row["group_total"] = len(groups)
                row["status"] = self._status_summary(db, row["id"], all_ids)
            return self._paged(rows, limit, offset)

        return self._run(
            "workstreams.listed", {"project": project, "limit": limit, "offset": offset}, operation
        )

    def workstream_status(self, workstream_id, limit=20, offset=0, include_scope=False):
        request = dict(
            workstream_id=workstream_id, limit=limit, offset=offset, include_scope=include_scope
        )

        def operation(db, scope):
            self._page(limit, offset)
            ws = self._workstream(db, workstream_id)
            project = db.execute(
                "SELECT * FROM projects WHERE id=?", (ws["project_id"],)
            ).fetchone()
            scope["project_id"] = ws["project_id"]
            queue = self._scoped_queue(db, workstream_id)
            # A separate page keeps concerns visible even beyond the ordinary queue page.
            concerned = [item for item in queue if item["concern_count"]]
            concerned.sort(
                key=lambda item: (item["view"] != "signoff", item["order_key"], item["id"])
            )
            concern_page = self._paged(concerned[offset : offset + limit + 1], limit, offset)
            concern_page["items"] = [
                {
                    key: item[key]
                    for key in (
                        "id",
                        "title",
                        "view",
                        "workstream_id",
                        "spec_revision",
                        "concern_count",
                        "concern_attempt_total",
                        "concern_attempt_references",
                        "concern_attempts_has_more",
                    )
                }
                for item in concern_page["items"]
            ]
            scope_page = {}
            if include_scope:
                for table, column, name in (
                    ("scope_members", "task_id", "members"),
                    ("scope_groups", "group_id", "groups"),
                    ("scope_exclusions", "task_id", "exclusions"),
                ):
                    rows = (
                        []
                        if name == "exclusions"
                        else [
                            r[0]
                            for r in db.execute(
                                f"SELECT {column} FROM {table} WHERE workstream_id=? "
                                f"ORDER BY {column}",
                                (workstream_id,),
                            )
                        ]
                    )
                    scope_page[name] = {
                        "ids": rows[offset : offset + limit],
                        "total": len(rows),
                        "next_offset": offset + limit if len(rows) > offset + limit else None,
                    }
            return {
                "project": dict(project),
                "workstream": ws,
                "status": self._status_summary(db, workstream_id),
                "project_order_revision": self._order_revision(db, ws["project_id"]),
                "total": len(queue),
                "concern_tasks": {"total": len(concerned), **concern_page},
                **({"scope": scope_page} if include_scope else {}),
                **self._paged(queue[offset : offset + limit + 1], limit, offset),
            }

        return self._run("workstream.status_read", request, operation)

    def _scoped_queue(self, db, workstream_id):
        ids = set(self._scope_ids(db, workstream_id))
        queue = []
        for row in db.execute(
            "SELECT id FROM tasks WHERE project_id=(SELECT project_id FROM workstreams WHERE id=?) "
            "ORDER BY order_key,id",
            (workstream_id,),
        ):
            if row["id"] not in ids:
                continue
            task = self._task(db, row["id"], {})
            queue.append(self._card(db, task, workstream_id))
        return queue

    @staticmethod
    def _status_view(task, reasons):
        if task["object_type"] == "group":
            return "group"
        if task["status"] in {"done", "dropped", "deferred"}:
            return task["status"]
        if "task_out_of_scope" in reasons:
            return "out_of_scope"
        if "inbox" in reasons:
            return "inbox"
        if "signoff" in reasons:
            return "signoff"
        if "review" in reasons:
            return "review"
        for reason in ("unresolved_items", "prerequisites"):
            if reason in reasons:
                return reason
        return "ready"

    def _status_summary(self, db, workstream_id, ids=None):
        ids = ids if ids is not None else self._scope_ids(db, workstream_id)
        scoped_ids = set(ids)
        counts = {
            key: 0
            for key in (
                "ready",
                "unresolved_items",
                "prerequisites",
                "review",
                "signoff",
                "done",
                "deferred",
                "dropped",
            )
        }
        overlapping = {
            key: 0
            for key in (
                "unresolved_items",
                "prerequisites",
                "review",
                "signoff",
            )
        }
        concerned_tasks = concern_count = 0
        for item in self._scoped_queue(db, workstream_id):
            concerned_tasks += bool(item["concern_count"])
            concern_count += item["concern_count"]
            counts[item["view"]] += 1
            for reason in item["gate_diagnostics"]:
                if reason in overlapping:
                    overlapping[reason] += 1
        ws = self._workstream(db, workstream_id)
        referenced_groups = []
        for row in db.execute(
            "SELECT g.id,g.title FROM tasks g JOIN scope_groups s ON s.group_id=g.id "
            "WHERE s.workstream_id=? ORDER BY g.title,g.id",
            (workstream_id,),
        ):
            group = self._details(db, self._task(db, row["id"], {}))
            referenced_groups.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "global_progress": {
                        k: group["progress"][k] for k in ("total", "done", "remaining")
                    },
                    "complete": group["complete"],
                    "project_member_count": group["progress"]["by_project"].get(
                        ws["project_id"], {"total": 0}
                    )["total"],
                    "scoped_member_count": sum(
                        member_id in scoped_ids for member_id in group["members"]
                    ),
                }
            )
        return {
            "recorded_state": "registered",
            "agent_liveness": "not_tracked",
            "scoped_count": len(ids),
            "counts": counts,
            "concern_task_count": concerned_tasks,
            "concern_count": concern_count,
            "overlapping_gate_diagnostics": overlapping,
            "referenced_groups": referenced_groups[:3],
            "referenced_group_total": len(referenced_groups),
            "referenced_groups_has_more": len(referenced_groups) > 3,
        }

    def init(
        self,
        path,
        branch=None,
        workstream_name=None,
        action=None,
        project=None,
        workstream_id=None,
        scope_expression="none",
        expected_revision=None,
        confirmed=False,
    ):
        request = dict(
            path=path,
            branch=branch,
            workstream_name=workstream_name,
            action=action,
            project=project,
            workstream_id=workstream_id,
            scope_expression=scope_expression,
            expected_revision=expected_revision,
            confirmed=confirmed,
        )

        def operation(db, scope):
            canonical = self._canonical_path(path)
            if not branch and not workstream_name:
                raise TaskError("workstream_name_required: detached or non-Git use needs a name")
            if branch is not None and (not isinstance(branch, str) or not branch.strip()):
                raise TaskError("invalid_branch")
            if workstream_name is not None and (
                not isinstance(workstream_name, str) or not workstream_name.strip()
            ):
                raise TaskError("invalid_workstream_name")
            attached = db.execute(
                "SELECT p.* FROM projects p JOIN project_paths a ON a.project_id=p.id "
                "WHERE a.path=?",
                (canonical,),
            ).fetchone()
            selected = dict(attached) if attached else None
            if project is not None:
                chosen = self._project(db, project, scope)
                if selected and selected["id"] != chosen["id"]:
                    return {
                        "state": "mismatch",
                        "message": "Checkout is attached to another project",
                        "path": canonical,
                        "project": selected,
                    }
            else:
                chosen = selected
            if action == "rebind_workstream" and workstream_id and chosen is None:
                target = self._workstream(db, workstream_id)
                chosen = dict(
                    db.execute(
                        "SELECT * FROM projects WHERE id=?", (target["project_id"],)
                    ).fetchone()
                )
            candidate = None
            if chosen:
                if branch:
                    candidate = db.execute(
                        "SELECT * FROM workstreams WHERE project_id=? AND branch=?",
                        (chosen["id"], branch),
                    ).fetchone()
                else:
                    candidate = db.execute(
                        "SELECT * FROM workstreams WHERE project_id=? AND branch IS NULL "
                        "AND name=?",
                        (chosen["id"], workstream_name),
                    ).fetchone()
            if candidate and candidate["checkout_path"] == canonical and selected:
                ws = dict(candidate)
                if workstream_id and workstream_id != ws["id"]:
                    return {
                        "state": "mismatch",
                        "message": "Target has another workstream binding",
                        "path": canonical,
                        "project": selected,
                        "workstream": ws,
                    }
                return self._ready_init(db, selected, ws) | {"changed": False}
            if candidate and not (
                action == "rebind_workstream" and confirmed and workstream_id == candidate["id"]
            ):
                return {
                    "state": "mismatch",
                    "message": "Branch is bound to another checkout; "
                    "choose explicit rebind_workstream",
                    "path": canonical,
                    "project": chosen,
                    "workstream": dict(candidate),
                }
            if not action or not confirmed:
                if selected:
                    rows = db.execute(
                        "SELECT * FROM workstreams WHERE project_id=? "
                        "ORDER BY created_at,id LIMIT 11",
                        (selected["id"],),
                    ).fetchall()
                    return {
                        "state": "new_branch",
                        "message": "Choose an initial scope or an explicit workstream rebind",
                        "path": canonical,
                        "project": selected,
                        "candidates": [dict(row) for row in rows[:10]],
                        "more_candidates": len(rows) > 10,
                        "candidate_total": db.execute(
                            "SELECT count(*) FROM workstreams WHERE project_id=?", (selected["id"],)
                        ).fetchone()[0],
                        "choices": ["new_workstream", "rebind_workstream"],
                    }
                projects = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM projects ORDER BY name,id LIMIT 11"
                    ).fetchall()
                ]
                workstreams = [
                    dict(row)
                    for row in db.execute(
                        "SELECT id,project_id,name,branch,checkout_path,revision "
                        "FROM workstreams ORDER BY created_at,id LIMIT 11"
                    ).fetchall()
                ]
                return {
                    "state": "unregistered_checkout",
                    "message": "Choose how this checkout relates to existing projects",
                    "path": canonical,
                    "choices": ["create_project", "attach_workstream", "rebind_workstream"],
                    "project_candidates": projects[:10],
                    "more_projects": len(projects) > 10,
                    "workstream_candidates": workstreams[:10],
                    "more_workstreams": len(workstreams) > 10,
                }
            if action not in {
                "create_project",
                "new_workstream",
                "attach_workstream",
                "rebind_workstream",
            }:
                raise TaskError("invalid_init_action")
            if action == "create_project":
                if selected or project or workstream_id:
                    raise TaskError("checkout_already_registered: choose its existing project")
                now = timestamp()
                selected = dict(
                    id=_id("prj_"),
                    name=Path(canonical).name,
                    canonical_path=canonical,
                    created_at=now,
                )
                db.execute(
                    "INSERT INTO projects (id,name,canonical_path,created_at) "
                    "VALUES (:id,:name,:canonical_path,:created_at)",
                    selected,
                )
                db.execute("INSERT INTO project_paths VALUES (?,?)", (canonical, selected["id"]))
                ws = self._insert_workstream(
                    db, selected["id"], workstream_name or branch, branch, canonical
                )
            elif action in {"new_workstream", "attach_workstream"}:
                if not chosen:
                    raise TaskError("project_required: select an existing project")
                if action == "new_workstream" and not selected:
                    raise TaskError("checkout_not_attached: choose attach_workstream")
                if action == "attach_workstream" and selected:
                    raise TaskError("checkout_already_attached: choose new_workstream")
                tokens = shlex.split(scope_expression) if isinstance(scope_expression, str) else []
                if tokens and tokens[0] != "none" and not tokens[0].startswith(("+", "-")):
                    base = db.execute(
                        "SELECT revision FROM workstreams WHERE project_id=? AND (id=? OR name=?)",
                        (chosen["id"], tokens[0], tokens[0]),
                    ).fetchone()
                    if base and (
                        type(expected_revision) is not int or base["revision"] != expected_revision
                    ):
                        raise TaskError("revision_conflict: re-read the scope source workstream")
                members, groups, exclusions = self._scope_expression(
                    db, chosen["id"], scope_expression
                )
                if not selected:
                    db.execute("INSERT INTO project_paths VALUES (?,?)", (canonical, chosen["id"]))
                selected = chosen
                ws = self._insert_workstream(
                    db, selected["id"], workstream_name or branch, branch, canonical
                )
                self._set_scope(
                    db,
                    ws["id"],
                    members,
                    groups,
                    exclusions,
                )
                ws = self._workstream(db, ws["id"])
            else:
                if not workstream_id:
                    raise TaskError("workstream_id_required: select a durable binding")
                ws = self._workstream(db, workstream_id)
                if chosen and ws["project_id"] != chosen["id"]:
                    raise TaskError("workstream_project_mismatch")
                if type(expected_revision) is not int or ws["revision"] != expected_revision:
                    raise TaskError("revision_conflict: re-read the workstream")
                selected = dict(
                    db.execute("SELECT * FROM projects WHERE id=?", (ws["project_id"],)).fetchone()
                )
                if not attached:
                    db.execute(
                        "INSERT INTO project_paths VALUES (?,?)", (canonical, selected["id"])
                    )
                try:
                    db.execute(
                        "UPDATE workstreams SET checkout_path=?,branch=?,name=?,"
                        "revision=revision+1 "
                        "WHERE id=?",
                        (canonical, branch, workstream_name or branch, workstream_id),
                    )
                except sqlite3.IntegrityError as exc:
                    raise TaskError("workstream_exists: target binding conflicts") from exc
                ws = self._workstream(db, workstream_id)
            scope.update(project_id=selected["id"], after={"project": selected, "workstream": ws})
            return self._ready_init(db, selected, ws) | {"changed": True}

        return self._run("session.initialized", request, operation)

    def _ready_init(self, db, project, ws):
        return {
            "state": "ready",
            "message": "Workstream ready",
            "project": project,
            "workstream": ws,
            "scope_revision": ws["revision"],
            "project_order_revision": self._order_revision(db, project["id"]),
            "queue": self._scoped_queue(db, ws["id"])[:10],
            "queue_total": len(self._scope_ids(db, ws["id"])),
            "queue_next_offset": 10 if len(self._scope_ids(db, ws["id"])) > 10 else None,
            "groups": self._scope_group_ids(db, ws["id"])[:10],
            "groups_total": len(self._scope_group_ids(db, ws["id"])),
            "status": self._status_summary(db, ws["id"]),
        }

    def init_workstream(
        self, project, path, branch=None, name=None, scope_expression="none", confirmed=False
    ):
        request = dict(
            project=project,
            path=path,
            branch=branch,
            name=name,
            scope_expression=scope_expression,
            confirmed=confirmed,
        )

        def operation(db, scope):
            if not confirmed:
                raise TaskError("confirmation_required: show branch/name and scope to the user")
            selected = self._project(db, project, scope)
            canonical = self._canonical_path(path)
            if not db.execute(
                "SELECT 1 FROM project_paths WHERE path=? AND project_id=?",
                (canonical, selected["id"]),
            ).fetchone():
                raise TaskError("checkout_not_attached: use attach_checkout")
            if not branch and not name:
                raise TaskError("workstream_name_required")
            members, groups, exclusions = self._scope_expression(
                db, selected["id"], scope_expression
            )
            ws = self._insert_workstream(db, selected["id"], name or branch, branch, canonical)
            self._set_scope(
                db,
                ws["id"],
                members,
                groups,
                exclusions,
            )
            ws = self._workstream(db, ws["id"])
            scope["after"] = {
                "workstream": ws,
                "members": sorted(members),
                "groups": sorted(groups),
                "exclusions": sorted(exclusions),
            }
            return scope["after"]

        return self._run("workstream.initialized", request, operation)

    def rebind_workstream(
        self, workstream_id, expected_revision, path, branch=None, name=None, confirmed=False
    ):
        request = dict(
            workstream_id=workstream_id,
            expected_revision=expected_revision,
            path=path,
            branch=branch,
            name=name,
            confirmed=confirmed,
        )

        def operation(db, scope):
            if not confirmed:
                raise TaskError("confirmation_required: show the new binding to the user")
            before = self._workstream(db, workstream_id)
            if before["revision"] != expected_revision:
                raise TaskError("revision_conflict: re-read the workstream")
            canonical = self._canonical_path(path)
            if not db.execute(
                "SELECT 1 FROM project_paths WHERE path=? AND project_id=?",
                (canonical, before["project_id"]),
            ).fetchone():
                raise TaskError("checkout_not_attached: use attach_checkout")
            if not branch and not name:
                raise TaskError("workstream_name_required")
            changed = any(
                before[k] != value
                for k, value in (
                    ("checkout_path", canonical),
                    ("branch", branch),
                    ("name", name or branch),
                )
            )
            if not changed:
                scope.update(project_id=before["project_id"], before=before, after=before)
                return before | {"changed": False}
            try:
                db.execute(
                    """UPDATE workstreams SET checkout_path=?, branch=?, name=?, revision=revision+1
                    WHERE id=?""",
                    (canonical, branch, name or branch, workstream_id),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskError("workstream_exists") from exc
            after = self._workstream(db, workstream_id)
            scope.update(project_id=before["project_id"], before=before, after=after)
            return after | {"changed": True}

        return self._run("workstream.rebound", request, operation)

    def preflight(self, project, path, branch=None, workstream_id=None):
        request = dict(project=project, path=path, branch=branch, workstream_id=workstream_id)

        def operation(db, scope):
            selected = self._project(db, project, scope)
            canonical = self._canonical_path(path)
            if not db.execute(
                "SELECT 1 FROM project_paths WHERE path=? AND project_id=?",
                (canonical, selected["id"]),
            ).fetchone():
                raise TaskError("checkout_not_attached: choose attach_checkout or init_project")
            if workstream_id:
                ws = self._workstream(db, workstream_id, selected["id"])
                if ws["checkout_path"] != canonical or ws["branch"] != branch:
                    raise TaskError("workstream_mismatch: rebind or initialize a new workstream")
            elif branch:
                rows = db.execute(
                    "SELECT * FROM workstreams WHERE project_id=? AND branch=?",
                    (selected["id"], branch),
                ).fetchall()
                if len(rows) != 1 or rows[0]["checkout_path"] != canonical:
                    raise TaskError(
                        "workstream_not_initialized: choose scope before autonomous work"
                    )
                ws = dict(rows[0])
            else:
                raise TaskError("workstream_name_required: select a named workstream")
            return {
                "project": selected,
                "workstream": ws,
                "scope": self._scope_ids(db, ws["id"])[:10],
                "scope_total": len(self._scope_ids(db, ws["id"])),
                "groups": self._scope_group_ids(db, ws["id"])[:10],
                "groups_total": len(self._scope_group_ids(db, ws["id"])),
            }

        return self._run("workstream.preflight", request, operation)

    @staticmethod
    def _scope_ids(db, workstream_id):
        rows = db.execute(
            """SELECT t.id FROM tasks t JOIN scope_members m ON m.task_id=t.id
            JOIN workstreams w ON w.id=m.workstream_id
            WHERE m.workstream_id=? AND t.project_id=w.project_id AND t.object_type='task'
            UNION SELECT t.id FROM tasks t JOIN scope_groups s ON s.group_id=t.parent_group_id
            JOIN workstreams w ON w.id=s.workstream_id
            WHERE s.workstream_id=? AND t.project_id=w.project_id AND t.object_type='task'
            AND NOT EXISTS (
                SELECT 1 FROM scope_exclusions e WHERE e.workstream_id=s.workstream_id
                AND e.task_id=s.group_id)""",
            (workstream_id, workstream_id),
        ).fetchall()
        exclusions = {
            row["task_id"]
            for row in db.execute(
                "SELECT task_id FROM scope_exclusions WHERE workstream_id=?", (workstream_id,)
            )
        }
        return sorted(row["id"] for row in rows if row["id"] not in exclusions)

    @staticmethod
    def _scope_group_ids(db, workstream_id):
        return [
            row["group_id"]
            for row in db.execute(
                "SELECT group_id FROM scope_groups WHERE workstream_id=? ORDER BY group_id",
                (workstream_id,),
            )
        ]

    @staticmethod
    def _set_scope(db, workstream_id, members, groups, exclusions):
        db.execute("DELETE FROM scope_members WHERE workstream_id=?", (workstream_id,))
        db.execute("DELETE FROM scope_groups WHERE workstream_id=?", (workstream_id,))
        db.execute("DELETE FROM scope_exclusions WHERE workstream_id=?", (workstream_id,))
        db.executemany(
            "INSERT INTO scope_members VALUES (?, ?)",
            [(workstream_id, member) for member in sorted(members)],
        )
        db.executemany(
            "INSERT INTO scope_groups VALUES (?, ?)",
            [(workstream_id, group) for group in sorted(groups)],
        )
        db.executemany(
            "INSERT INTO scope_exclusions VALUES (?, ?)",
            [(workstream_id, item) for item in sorted(exclusions)],
        )

    @staticmethod
    def _touch_workstream(db, workstream_id):
        db.execute("UPDATE workstreams SET revision=revision+1 WHERE id=?", (workstream_id,))

    @staticmethod
    def _resolve_reference(db, project_id, term):
        row = db.execute(
            "SELECT id, object_type FROM tasks WHERE id=? AND "
            "(project_id=? OR object_type='group')",
            (term, project_id),
        ).fetchone()
        if row:
            return row["id"], row["object_type"]
        rows = db.execute(
            "SELECT id, object_type FROM tasks WHERE title=? AND "
            "(project_id=? OR (object_type='group' AND EXISTS "
            "(SELECT 1 FROM tasks child WHERE child.parent_group_id=tasks.id "
            "AND child.project_id=?)) OR (object_type='group' AND EXISTS "
            "(SELECT 1 FROM scope_groups s JOIN workstreams w ON w.id=s.workstream_id "
            "WHERE s.group_id=tasks.id AND w.project_id=?)))",
            (term, project_id, project_id, project_id),
        ).fetchall()
        if len(rows) != 1:
            raise TaskError(
                "ambiguous_reference: use a stable task/group ID"
                if rows
                else f"unknown_reference: {term}"
            )
        return rows[0]["id"], rows[0]["object_type"]

    def _scope_expression(self, db, project_id, expression):
        if not isinstance(expression, str):
            raise TaskError("invalid_scope_expression")
        tokens = shlex.split(expression)
        if not tokens or tokens == ["none"]:
            return set(), set(), set()
        members, groups, exclusions = set(), set(), set()
        first = tokens[0]
        if first == "none":
            tokens = tokens[1:]
        elif not first.startswith(("+", "-")):
            ws_rows = db.execute(
                "SELECT id FROM workstreams WHERE project_id=? AND (id=? OR name=?)",
                (project_id, first, first),
            ).fetchall()
            if len(ws_rows) != 1:
                raise TaskError("unknown_scope_base: use none or a workstream ID/name")
            ws_id = ws_rows[0]["id"]
            members = {
                row["task_id"]
                for row in db.execute(
                    "SELECT task_id FROM scope_members WHERE workstream_id=?", (ws_id,)
                )
            }
            groups = {
                r["group_id"]
                for r in db.execute(
                    "SELECT group_id FROM scope_groups WHERE workstream_id=?", (ws_id,)
                )
            }
            exclusions = {
                r["task_id"]
                for r in db.execute(
                    "SELECT task_id FROM scope_exclusions WHERE workstream_id=?", (ws_id,)
                )
            }
            tokens = tokens[1:]
        for token in tokens:
            if len(token) < 2 or token[0] not in "+-":
                raise TaskError("invalid_scope_expression: use +reference or -reference")
            identity, kind = self._resolve_reference(db, project_id, token[1:])
            target = groups if kind == "group" else members
            if token[0] == "+":
                target.add(identity)
                exclusions.discard(identity)
            else:
                target.discard(identity)
                exclusions.add(identity)
        return members, groups, exclusions

    def set_scope(self, workstream_id, expected_revision, expression):
        request = dict(
            workstream_id=workstream_id, expected_revision=expected_revision, expression=expression
        )

        def operation(db, scope):
            before = self._workstream(db, workstream_id)
            if before["revision"] != expected_revision:
                raise TaskError("revision_conflict: re-read the workstream")
            previous = {
                "members": [
                    row["task_id"]
                    for row in db.execute(
                        "SELECT task_id FROM scope_members WHERE workstream_id=? ORDER BY task_id",
                        (workstream_id,),
                    )
                ],
                "groups": [
                    row["group_id"]
                    for row in db.execute(
                        "SELECT group_id FROM scope_groups WHERE workstream_id=? ORDER BY group_id",
                        (workstream_id,),
                    )
                ],
                "exclusions": [
                    row["task_id"]
                    for row in db.execute(
                        "SELECT task_id FROM scope_exclusions WHERE workstream_id=? "
                        "ORDER BY task_id",
                        (workstream_id,),
                    )
                ],
            }
            members, groups, exclusions = self._scope_expression(
                db, before["project_id"], expression
            )
            changed = previous != {
                "members": sorted(members),
                "groups": sorted(groups),
                "exclusions": sorted(exclusions),
            }
            if changed:
                self._set_scope(db, workstream_id, members, groups, exclusions)
                self._touch_workstream(db, workstream_id)
            scope.update(
                project_id=before["project_id"],
                before={"workstream": before, **previous},
                after={
                    "members": sorted(members),
                    "groups": sorted(groups),
                    "exclusions": sorted(exclusions),
                },
            )
            return {**self._workstream(db, workstream_id), **scope["after"], "changed": changed}

        return self._run("scope.changed", request, operation)

    def add_to_workstream(self, task_id, workstream_id, expected_revision):
        """Add effective membership here while preserving all other workstreams."""
        return self._membership_change(task_id, workstream_id, expected_revision, True)

    def remove_from_workstream(self, task_id, workstream_id, expected_revision):
        """Remove only the named workstream, including inherited group membership."""
        return self._membership_change(task_id, workstream_id, expected_revision, False)

    def _membership_change(self, task_id, workstream_id, expected_revision, adding):
        request = dict(
            task_id=task_id, workstream_id=workstream_id, expected_revision=expected_revision
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            self._workstream(db, workstream_id, before["project_id"])
            present = task_id in self._scope_ids(db, workstream_id)
            changed = present != adding
            if changed:
                if adding:
                    db.execute(
                        "INSERT OR IGNORE INTO scope_members VALUES (?,?)", (workstream_id, task_id)
                    )
                    db.execute(
                        "DELETE FROM scope_exclusions WHERE workstream_id=? AND task_id=?",
                        (workstream_id, task_id),
                    )
                else:
                    db.execute(
                        "DELETE FROM scope_members WHERE workstream_id=? AND task_id=?",
                        (workstream_id, task_id),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO scope_exclusions VALUES (?,?)",
                        (workstream_id, task_id),
                    )
                self._touch_workstream(db, workstream_id)
                db.execute(
                    "UPDATE tasks SET revision=revision+1,updated_at=? WHERE id=?",
                    (timestamp(), task_id),
                )
            after = self._task(db, task_id, {})
            scope.update(before=before, after=after)
            return self._task_ack(db, after) | {
                "changed": changed,
                "workstream_id": workstream_id,
                "workstream_revision": self._workstream(db, workstream_id)["revision"],
                "workstream_revisions": {
                    workstream_id: self._workstream(db, workstream_id)["revision"]
                },
            }

        return self._run(
            "task.workstream_added" if adding else "task.workstream_removed", request, operation
        )

    @staticmethod
    def _membership_ids(db, task):
        if task["object_type"] != "task":
            return []
        return [
            row["id"]
            for row in db.execute(
                "SELECT w.id FROM workstreams w WHERE w.project_id=? "
                "AND NOT EXISTS (SELECT 1 FROM scope_exclusions e "
                "WHERE e.workstream_id=w.id AND e.task_id=?) "
                "AND (EXISTS (SELECT 1 FROM scope_members m "
                "WHERE m.workstream_id=w.id AND m.task_id=?) "
                "OR EXISTS (SELECT 1 FROM scope_groups g "
                "WHERE g.workstream_id=w.id AND g.group_id=? AND NOT EXISTS "
                "(SELECT 1 FROM scope_exclusions e WHERE e.workstream_id=w.id "
                "AND e.task_id=g.group_id))) ORDER BY w.id",
                (task["project_id"], task["id"], task["id"], task["parent_group_id"]),
            )
        ]

    @staticmethod
    def _task(db, task_id, scope):
        scope["task_id"] = task_id
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskError(f"unknown_task: {task_id}")
        task = dict(row)
        task["unresolved_items"] = json.loads(task.pop("unresolved_json"))
        # Legacy authority columns are frozen private historical storage.
        for key in ("accepted_spec_revision", "acceptance_note", "acceptance_basis"):
            task.pop(key, None)
        task["workstream_ids"] = Store._membership_ids(db, task)
        task["adopted"] = bool(task["workstream_ids"])
        task["latest_rejection"] = Store._latest_rejection(db, task_id)
        task["summary_stale"] = Store._summary_stale(task)
        scope["project_id"] = task["project_id"]
        return task

    @staticmethod
    def _latest_rejection(db, task_id):
        """Project the newest factual rejection; its proof keeps its original scope."""
        row = db.execute(
            "SELECT e.sequence,e.timestamp,e.action,e.request_json,a.id AS attempt_id,"
            "a.workstream_id,a.spec_revision FROM events e "
            "JOIN attempts a ON a.id=json_extract(e.request_json,'$.attempt_id') "
            "AND a.task_id=e.task_id WHERE e.task_id=? AND e.outcome='ok' AND "
            "((e.action='attempt.reviewed' AND json_extract(e.request_json,'$.verdict')='rework') "
            "OR (e.action='task.signoff' AND "
            "json_extract(e.request_json,'$.decision') IN ('rework','revise'))) "
            "ORDER BY e.sequence DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        request = json.loads(row["request_json"])
        review = row["action"] == "attempt.reviewed"
        return {
            "source": "review" if review else "signoff",
            "verdict": request["verdict" if review else "decision"],
            "reasons": request["note"]
            if review
            else request.get("reasons", request.get("user_note")),
            "attempt_id": row["attempt_id"],
            "workstream_id": row["workstream_id"],
            "spec_revision": row["spec_revision"],
            "timestamp": row["timestamp"],
            "decision_ref": row["sequence"],
        }

    @staticmethod
    def _validate_summary(summary):
        if summary is not None and (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > 240
            or len(summary.splitlines()) != 1
            or any(c in summary for c in "\r\n\x85\u2028\u2029")
        ):
            raise TaskError("invalid_summary: use null or 1–240 Unicode characters on one line")

    @staticmethod
    def _summary_stale(task):
        return (
            task["summary_spec_revision"] != task["spec_revision"]
            if task.get("summary") is not None
            else None
        )

    @staticmethod
    def _attempt_summary(row):
        attempt = dict(row)
        return {
            "attempt_id": attempt["id"],
            "attempt_revision": attempt["revision"],
            **{k: attempt[k] for k in ("task_id", "workstream_id", "spec_revision", "state")},
            "implementer": attempt["implementer"][:120],
            "implementer_truncated": len(attempt["implementer"]) > 120,
            "summary": " ".join(attempt["summary"].split())[:240],
            "concern_count": attempt["concern_count"],
        }

    @staticmethod
    def _current_attempt_rows(db, task, workstream_id=None):
        return db.execute(
            "SELECT id,task_id,workstream_id,spec_revision,state,revision,implementer,summary, "
            + CONCERN_COUNT_SQL
            + " AS concern_count "
            "FROM attempts WHERE task_id=? AND spec_revision=? "
            + ("AND workstream_id=? " if workstream_id else "")
            + "ORDER BY CASE WHEN state IN ('review','passed','human_review') THEN 0 ELSE 1 END, "
            "created_at DESC,id ASC",
            (task["id"], task["spec_revision"]) + ((workstream_id,) if workstream_id else ()),
        ).fetchall()

    @staticmethod
    def _card(db, task, workstream_id=None):
        card = {
            k: task[k]
            for k in (
                "id",
                "project_id",
                "title",
                "summary",
                "summary_spec_revision",
                "object_type",
                "status",
                "revision",
                "spec_revision",
                "order_key",
                "selected_attempt_id",
                "parent_group_id",
            )
        }
        card.update(
            summary_stale=Store._summary_stale(task),
            specification_complete=False,
            workstream_ids=Store._membership_ids(db, task),
            adopted=bool(Store._membership_ids(db, task)),
            unresolved_count=len(task["unresolved_items"]),
            latest_rejection=(
                {key: value for key, value in task["latest_rejection"].items() if key != "reasons"}
                if task["latest_rejection"]
                else None
            ),
            pending_proposal_count=db.execute(
                "SELECT count(*) FROM gate_proposals WHERE task_id=?", (task["id"],)
            ).fetchone()[0],
        )
        prerequisites = Store._prerequisite_references(db, task["id"])
        card.update(
            prerequisite_count=len(prerequisites),
            prerequisites=prerequisites[:3],
            prerequisites_has_more=len(prerequisites) > 3,
        )
        reasons = Store._gate_reasons(db, task, workstream_id)
        if not workstream_id:
            reasons = [r for r in reasons if r not in {"review", "signoff"}]
        if workstream_id and task["object_type"] == "task":
            card["in_scope"] = task["id"] in Store._scope_ids(db, workstream_id)
            if not card["in_scope"] and task["status"] not in {"done", "dropped", "deferred"}:
                reasons.append("task_out_of_scope")
        card["gate_diagnostics"] = [r for r in reasons if r != "closed_or_group"]
        if task["object_type"] == "group":
            detail = Store._details(db, task, history=False)
            card.update(
                project_id=None,
                origin_project_id=task["project_id"],
                progress={k: detail["progress"][k] for k in ("total", "done", "remaining")},
                complete=detail["complete"],
                project_count=len(detail["progress"]["by_project"]),
            )
        else:
            rows = Store._current_attempt_rows(db, task, workstream_id)
            concerned = [row for row in rows if row["concern_count"]]
            card.update(
                concern_count=sum(row["concern_count"] for row in concerned),
                concern_attempt_total=len(concerned),
                concern_attempt_references=[
                    {
                        key: row[key]
                        for key in (
                            "id",
                            "workstream_id",
                            "spec_revision",
                            "state",
                            "revision",
                            "concern_count",
                        )
                    }
                    for row in concerned[:3]
                ],
                concern_attempts_has_more=len(concerned) > 3,
            )
            counts = {
                state: sum(r["state"] == state for r in rows)
                for state in ("review", "passed", "human_review", "rework")
            }
            if workstream_id:
                view = Store._status_view(task, reasons)
                preferred = (
                    {"review"}
                    if view == "review"
                    else {"passed", "human_review"}
                    if view == "signoff"
                    else {"rework"}
                    if view == "ready"
                    else set()
                )
                relevant = next(
                    (r for r in rows if r["state"] in preferred), rows[0] if rows else None
                )
                card.update(
                    workstream_id=workstream_id,
                    view=view,
                    attempt_counts=counts,
                    attempt_reference=Store._attempt_summary(relevant) if relevant else None,
                    alternative_attempt_count=max(0, len(rows) - 1),
                )
            else:
                card["aggregate_attempt_counts"] = counts
        return card

    @staticmethod
    def _specification(db, task, workstream_id=None):
        detail = Store._details(db, task, history=False)
        detail.update(specification_complete=True, summary_stale=Store._summary_stale(task))
        reasons = Store._gate_reasons(db, task, workstream_id)
        if not workstream_id:
            reasons = [r for r in reasons if r not in {"review", "signoff"}]
        if workstream_id and task["object_type"] == "task":
            detail["in_scope"] = task["id"] in Store._scope_ids(db, workstream_id)
            if not detail["in_scope"] and task["status"] not in {"done", "dropped", "deferred"}:
                reasons.append("task_out_of_scope")
        detail["gate_diagnostics"] = [r for r in reasons if r != "closed_or_group"]
        rows = Store._current_attempt_rows(db, task, workstream_id)
        detail.update(
            attempt_summaries=[Store._attempt_summary(row) for row in rows[:3]],
            attempt_total=len(rows),
            actionable_total=sum(r["state"] in {"review", "passed", "human_review"} for r in rows),
            attempts_has_more=len(rows) > 3,
            has_more=len(rows) > 3,
            attempt_scope=workstream_id or "cross_workstream",
            concern_count=sum(row["concern_count"] for row in rows),
            concern_attempt_total=sum(bool(row["concern_count"]) for row in rows),
            concerns=Store._concern_window(db, rows[:3]),
            concerns_has_more=any(row["concern_count"] for row in rows[3:]),
        )
        if task["object_type"] == "group":
            detail.update(
                member_total=len(detail["members"]), members_has_more=len(detail["members"]) > 3
            )
            detail["members"] = detail["members"][:3]
            detail["member_details"] = detail["member_details"][:3]
            detail["progress"].pop("by_project")
        return detail

    @staticmethod
    def _concern_window(db, rows):
        """Explicit full reads show the same bounded applicable attempt window."""
        concerns = []
        for reference in rows:
            if not reference["concern_count"]:
                continue
            row = db.execute("SELECT * FROM attempts WHERE id=?", (reference["id"],)).fetchone()
            concerns.extend(
                {
                    **concern,
                    "attempt_id": row["id"],
                    "workstream_id": row["workstream_id"],
                    "spec_revision": row["spec_revision"],
                }
                for concern in Store._attempt_details(row)["concerns"]
            )
        return concerns

    def read_tasks(self, ids, specification=False, workstream_id=None, attempt_ids=None):
        """Bounded MCP projection; get_tasks retains full internal/viewer detail."""
        request = dict(
            ids=ids,
            specification=specification,
            workstream_id=workstream_id,
            attempt_ids=attempt_ids,
        )

        def operation(db, scope):
            if not isinstance(ids, list) or not 1 <= len(ids) <= 20:
                raise TaskError("invalid_ids: request 1–20 IDs")
            if workstream_id:
                self._workstream(db, workstream_id)
            if attempt_ids is not None and (
                not specification
                or not isinstance(attempt_ids, list)
                or not 1 <= len(attempt_ids) <= 20
                or len(set(attempt_ids)) != len(attempt_ids)
            ):
                raise TaskError(
                    "invalid_attempt_ids: specification=true and 1–20 distinct IDs required"
                )
            tasks = [self._task(db, identity, {}) for identity in ids]
            projection = self._specification if specification else self._card
            items = [projection(db, task, workstream_id) for task in tasks]
            if len(ids) == 1:
                scope.update(task_id=ids[0], project_id=tasks[0]["project_id"])
            if attempt_ids:
                by_id = {item["id"]: item for item in items}
                for item in items:
                    item["attempts"] = []
                for identity in attempt_ids:
                    row = db.execute("SELECT * FROM attempts WHERE id=?", (identity,)).fetchone()
                    if row is None or row["task_id"] not in by_id:
                        raise TaskError(
                            "invalid_attempt_owner: requested proof must belong to requested tasks"
                        )
                    item = by_id[row["task_id"]]
                    item["attempts"].append(self._attempt_details(row))
                    # Chosen proof already includes its concerns, once, with its provenance.
                    item["concerns"] = [c for c in item["concerns"] if c["attempt_id"] != identity]
            return {"items": items}

        return self._run("tasks.read", request, operation)

    @staticmethod
    def _cursor_page(rows, limit, cursor, context):
        """Keyset paging in immutable creation order, bound to the requested filters."""
        Store._page(limit, 0)
        last = None
        if cursor is not None:
            try:
                payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if payload["context"] != context:
                    raise ValueError()
                last = tuple(payload["last"])
                if len(last) != 2 or not all(isinstance(x, str) for x in last):
                    raise ValueError()
            except (ValueError, TypeError, KeyError, AttributeError):
                raise TaskError(
                    "invalid_cursor: use the returned cursor with unchanged filters"
                ) from None
        selected = [r for r in rows if last is None or (r["created_at"], r["id"]) > last]
        page = selected[:limit]
        next_cursor = None
        if len(selected) > limit:
            next_cursor = base64.urlsafe_b64encode(
                _json(
                    {
                        "context": context,
                        "last": [page[-1]["created_at"], page[-1]["id"]],
                    }
                ).encode()
            ).decode()
        return page, {
            "total": len(rows),
            "has_more": next_cursor is not None,
            "next_cursor": next_cursor,
        }

    def list_task_attempts(
        self,
        task_id,
        workstream_id=None,
        states=None,
        current_spec_only=True,
        limit=20,
        cursor=None,
    ):
        request = dict(
            task_id=task_id,
            workstream_id=workstream_id,
            states=states,
            current_spec_only=current_spec_only,
            limit=limit,
            cursor=cursor,
        )

        def operation(db, scope):
            task = self._task(db, task_id, scope)
            if workstream_id:
                self._workstream(db, workstream_id)
            if states is not None and (
                not isinstance(states, list)
                or not states
                or any(s not in {"review", "passed", "human_review", "rework"} for s in states)
            ):
                raise TaskError("invalid_states")
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT id,task_id,workstream_id,implementer,summary,"
                    "spec_revision,state,revision,created_at, "
                    + CONCERN_COUNT_SQL
                    + " AS concern_count "
                    "FROM attempts WHERE task_id=? ORDER BY created_at,id",
                    (task_id,),
                )
                if (not workstream_id or r["workstream_id"] == workstream_id)
                and (not current_spec_only or r["spec_revision"] == task["spec_revision"])
                and (states is None or r["state"] in states)
            ]
            context = [
                task_id,
                workstream_id,
                sorted(set(states)) if states else None,
                task["spec_revision"] if current_spec_only else None,
            ]
            page, info = self._cursor_page(rows, limit, cursor, context)
            return {"task_id": task_id, "items": [self._attempt_summary(r) for r in page], **info}

        return self._run("attempts.listed", request, operation)

    def get_attempt(self, attempt_id):
        def operation(db, scope):
            row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None:
                raise TaskError("unknown_attempt")
            self._task(db, row["task_id"], scope)
            return self._attempt_details(row)

        return self._run("attempt.read", {"attempt_id": attempt_id}, operation)

    def list_group_members(self, group_id, limit=20, cursor=None):
        def operation(db, scope):
            group = self._task(db, group_id, scope)
            if group["object_type"] != "group":
                raise TaskError("invalid_group")
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT id,created_at FROM tasks WHERE parent_group_id=? "
                    "ORDER BY created_at,id",
                    (group_id,),
                )
            ]
            page, info = self._cursor_page(rows, limit, cursor, [group_id])
            return {
                "group_id": group_id,
                "group_revision": group["revision"],
                "items": [self._card(db, self._task(db, r["id"], {})) for r in page],
                **info,
            }

        return self._run(
            "group.members_listed", dict(group_id=group_id, limit=limit, cursor=cursor), operation
        )

    @staticmethod
    def _specification_etag(task):
        """Whole-field replacement guard, independent of non-spec task revisions."""
        content = [task[key] for key in ("id", "spec_revision", "body", "acceptance_criteria")]
        return "sha256:" + hashlib.sha256(_json(content).encode("utf-8")).hexdigest()

    @staticmethod
    def _group_complete(db, group_id):
        return (
            bool(
                db.execute(
                    "SELECT 1 FROM tasks WHERE parent_group_id=? LIMIT 1", (group_id,)
                ).fetchone()
            )
            and not db.execute(
                "SELECT 1 FROM tasks WHERE parent_group_id=? AND status!='done' LIMIT 1",
                (group_id,),
            ).fetchone()
        )

    @staticmethod
    def _require_mutable(db, task, concrete=True):
        if task["status"] == "done" or (
            task["object_type"] == "group" and Store._group_complete(db, task["id"])
        ):
            raise TaskError("completed_task_immutable: create a new task for changed requirements")
        if concrete and task["object_type"] != "task":
            raise TaskError("group_not_executable: groups hold context and completion only")

    @staticmethod
    def _save_task(db, task):
        values = {
            **task,
            "unresolved_json": _json(task["unresolved_items"]),
        }
        db.execute(
            """UPDATE tasks SET title=:title, body=:body,
            acceptance_criteria=:acceptance_criteria, spec_revision=:spec_revision,
            unresolved_json=:unresolved_json, object_type=:object_type,
            parent_group_id=:parent_group_id, order_key=:order_key, status=:status,
            selected_attempt_id=:selected_attempt_id,
            source=:source, user_request=:user_request,
            summary=:summary, summary_spec_revision=:summary_spec_revision,
            revision=:revision, updated_at=:updated_at WHERE id=:id""",
            values,
        )

    @staticmethod
    def _details(db, task, history=True):
        task = dict(task)
        task["workstream_ids"] = Store._membership_ids(db, task)
        task["adopted"] = bool(task["workstream_ids"])
        task["summary_stale"] = Store._summary_stale(task)
        if task["parent_group_id"]:
            group = db.execute(
                "SELECT id,title,body,acceptance_criteria,revision,spec_revision,"
                "summary,summary_spec_revision "
                "FROM tasks WHERE id=?",
                (task["parent_group_id"],),
            ).fetchone()
            task["parent_group"] = dict(group)
            task["parent_group"]["summary_stale"] = Store._summary_stale(task["parent_group"])
        task["blocked_by"] = [
            r["blocked_by_id"]
            for r in db.execute(
                "SELECT blocked_by_id FROM prerequisites WHERE task_id=? ORDER BY blocked_by_id",
                (task["id"],),
            )
        ]
        task["prerequisites"] = Store._prerequisite_references(db, task["id"])
        if history:
            task["attempts"] = [
                Store._attempt_details(r)
                for r in db.execute(
                    "SELECT * FROM attempts WHERE task_id=? ORDER BY created_at,id", (task["id"],)
                )
            ]
        task["gate_proposals"] = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM gate_proposals WHERE task_id=? ORDER BY created_at,id", (task["id"],)
            )
        ]
        if task["object_type"] == "group":
            task["origin_project_id"] = task["project_id"]
            task["project_id"] = None
            members = [
                dict(r)
                for r in db.execute(
                    "SELECT id,project_id,title,status,revision FROM tasks "
                    "WHERE parent_group_id=? ORDER BY project_id,order_key,id",
                    (task["id"],),
                )
            ]
            task["members"] = [member["id"] for member in members]
            task["member_details"] = members
            task["progress"] = {
                "total": len(members),
                "done": sum(member["status"] == "done" for member in members),
                "remaining": sum(member["status"] != "done" for member in members),
                "by_project": {
                    project_id: {
                        "total": sum(member["project_id"] == project_id for member in members),
                        "done": sum(
                            member["project_id"] == project_id and member["status"] == "done"
                            for member in members
                        ),
                    }
                    for project_id in sorted({member["project_id"] for member in members})
                },
            }
            task["complete"] = bool(members) and task["progress"]["remaining"] == 0
        if history:
            task["signoff_decisions"] = Store._signoff_decisions(db, task["id"])
        task["specification_etag"] = Store._specification_etag(task)
        return task

    @staticmethod
    def _signoff_decisions(db, task_id):
        decisions = []
        for row in db.execute(
            "SELECT sequence,timestamp,after_json FROM events WHERE task_id=? "
            "AND action='task.signoff' AND outcome='ok' ORDER BY sequence",
            (task_id,),
        ):
            after = json.loads(row["after_json"] or "null") or {}
            if judgment := after.get("signoff_decision"):
                decisions.append(
                    {
                        "decision_ref": row["sequence"],
                        "timestamp": row["timestamp"],
                        **judgment,
                    }
                )
        return decisions

    @staticmethod
    def _attempt_proof(evidence):
        try:
            proof = json.loads(evidence)
        except (ValueError, TypeError):
            return None
        if not isinstance(proof, dict):
            return None
        if (
            proof.get("format") == "durable-result-v1"
            and {"evidence", "artifacts", "verification", "specification_etag"} <= proof.keys()
        ):
            return proof
        return None

    @staticmethod
    def _attempt_details(row):
        """Read explicit metadata and decode only the established durable proof format."""
        attempt = dict(row)
        attempt["concerns"] = json.loads(attempt.pop("concerns_json", "[]"))
        proof = Store._attempt_proof(attempt["evidence"])
        if not proof:
            return attempt
        attempt.update(
            {
                key: proof[key]
                for key in ("evidence", "artifacts", "verification", "specification_etag")
            }
        )
        return attempt

    def get_tasks(self, ids):
        def operation(db, scope):
            if not isinstance(ids, list) or not 1 <= len(ids) <= 20:
                raise TaskError("invalid_ids: request 1–20 IDs")
            tasks = [self._details(db, self._task(db, task_id, {})) for task_id in ids]
            if len({t["project_id"] for t in tasks}) == 1:
                scope["project_id"] = tasks[0]["project_id"]
            if len(ids) == 1:
                scope["task_id"] = ids[0]
            return {"items": tasks}

        return self._run("tasks.read", {"ids": ids}, operation)

    def list_groups(self, project=None, limit=20, offset=0):
        request = dict(project=project, limit=limit, offset=offset)

        def operation(db, scope):
            self._page(limit, offset)
            selected = self._project(db, project, scope) if project is not None else None
            rows = db.execute(
                "SELECT g.id FROM tasks g WHERE g.object_type='group' "
                + (
                    "AND (g.project_id=? OR EXISTS (SELECT 1 FROM tasks child "
                    "WHERE child.parent_group_id=g.id AND child.project_id=?) "
                    "OR EXISTS (SELECT 1 FROM scope_groups s JOIN workstreams w "
                    "ON w.id=s.workstream_id WHERE s.group_id=g.id AND w.project_id=?)) "
                    if selected
                    else ""
                )
                + "ORDER BY g.created_at,g.id LIMIT ? OFFSET ?",
                ((selected["id"],) * 3 if selected else ()) + (limit + 1, offset),
            ).fetchall()
            groups = [self._card(db, self._task(db, row["id"], {})) for row in rows]
            return self._paged(groups, limit, offset)

        return self._run("groups.listed", request, operation)

    def create_group(self, workstream_id, title, body="", acceptance_criteria="", summary=None):
        request = dict(
            workstream_id=workstream_id,
            title=title,
            body=body,
            acceptance_criteria=acceptance_criteria,
            summary=summary,
        )

        def operation(db, scope):
            ws = self._workstream(db, workstream_id)
            scope["project_id"] = ws["project_id"]
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(body, str)
                or not isinstance(acceptance_criteria, str)
            ):
                raise TaskError("invalid_specification")
            now = timestamp()
            self._validate_summary(summary)
            group_id = _id("tsk_")
            db.execute(
                """INSERT INTO tasks
                (id,project_id,title,body,acceptance_criteria,status,object_type,
                spec_revision,accepted_spec_revision,acceptance_note,unresolved_json,
                parent_group_id,order_key,selected_attempt_id,revision,created_at,updated_at)
                VALUES (?,NULL,?,?,?,'open','group',1,NULL,'','[]',NULL,0,NULL,1,?,?)""",
                (group_id, title.strip(), body, acceptance_criteria, now, now),
            )
            db.execute(
                "UPDATE tasks SET summary=?,summary_spec_revision=? WHERE id=?",
                (summary, 1 if summary is not None else None, group_id),
            )
            db.execute("INSERT INTO scope_groups VALUES (?,?)", (workstream_id, group_id))
            self._touch_workstream(db, workstream_id)
            detail = self._details(db, self._task(db, group_id, scope))
            scope["after"] = detail
            return detail

        return self._run("group.created", request, operation)

    def add_group_member(self, group_id, expected_revision, task_id, expected_task_revision):
        request = dict(
            group_id=group_id,
            expected_revision=expected_revision,
            task_id=task_id,
            expected_task_revision=expected_task_revision,
        )

        def operation(db, scope):
            group = self._task(db, group_id, scope)
            self._revision(group, expected_revision)
            if group["object_type"] != "group":
                raise TaskError("invalid_group")
            self._require_mutable(db, group, concrete=False)
            member = self._task(db, task_id, {})
            self._revision(member, expected_task_revision)
            if member["object_type"] != "task" or member["parent_group_id"]:
                raise TaskError("invalid_member: nested groups or reassignment are unsupported")
            self._require_mutable(db, member)
            if self._would_cycle(db, group_id, task_id):
                raise TaskError("prerequisite_cycle")
            after_group = {**group, "revision": group["revision"] + 1, "updated_at": timestamp()}
            after_member = {
                **member,
                "parent_group_id": group_id,
                "revision": member["revision"] + 1,
                "updated_at": timestamp(),
            }
            self._save_task(db, after_member)
            self._save_task(db, after_group)
            scope.update(
                project_id=member["project_id"],
                before={"group": group, "member": member},
                after={"group": after_group, "member": after_member},
            )
            return {
                "group": self._details(db, after_group),
                "member": self._details(db, after_member),
            }

        return self._run("group.member_added", request, operation)

    @staticmethod
    def _insert_task(
        db,
        project_id,
        title,
        body,
        acceptance_criteria,
        parent_group_id=None,
        source="agent",
        user_request="",
    ):
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
            raise TaskError("invalid_specification: title and description required")
        if not isinstance(acceptance_criteria, str):
            raise TaskError("invalid_specification: acceptance criteria must be text")
        if not isinstance(source, str) or source not in {"agent", "user", "unknown"}:
            raise TaskError("invalid_source: agent, user or unknown")
        if not isinstance(user_request, str):
            raise TaskError("invalid_user_request: request must be text")
        now = timestamp()
        order = db.execute(
            "SELECT coalesce(max(order_key),0)+1 FROM tasks "
            "WHERE project_id=? AND object_type='task'",
            (project_id,),
        ).fetchone()[0]
        task_id = _id("tsk_")
        db.execute(
            """INSERT INTO tasks
            (id,project_id,title,body,acceptance_criteria,status,object_type,spec_revision,
             accepted_spec_revision,acceptance_note,unresolved_json,parent_group_id,order_key,
             selected_attempt_id,revision,created_at,updated_at,source,user_request,acceptance_basis)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                project_id,
                title.strip(),
                body,
                acceptance_criteria,
                "open",
                "task",
                1,
                None,
                "",
                "[]",
                parent_group_id,
                order,
                None,
                1,
                now,
                now,
                source,
                user_request,
                "unknown",
            ),
        )
        db.execute("UPDATE projects SET order_revision=order_revision+1 WHERE id=?", (project_id,))
        return task_id

    @staticmethod
    def _task_ack(db, task):
        """Continuation state without requirements, legacy authority or proof history."""
        ack = {
            key: task[key]
            for key in ("id", "revision", "spec_revision", "status", "object_type", "project_id")
        }
        ack.update(
            task_id=task["id"],
            task_revision=task["revision"],
            workstream_ids=Store._membership_ids(db, task),
            adopted=bool(Store._membership_ids(db, task)),
            summary_spec_revision=task["summary_spec_revision"],
            summary_stale=Store._summary_stale(task),
        )
        if task["object_type"] == "group":
            return ack | {
                "gate_diagnostics": [],
                "project_id": None,
                "origin_project_id": task.get("origin_project_id", task["project_id"]),
                "group_id": task["id"],
                "group_revision": task["revision"],
                "complete": Store._group_complete(db, task["id"]),
            }
        reasons = [
            r
            for r in Store._gate_reasons(db, task)
            if r not in {"review", "signoff", "closed_or_group"}
        ]
        return ack | {"parent_group_id": task["parent_group_id"], "gate_diagnostics": reasons}

    def create_task(
        self,
        project,
        title,
        body="",
        acceptance_criteria="",
        source="agent",
        user_request="",
        workstream_id=None,
        group_id=None,
        group_expected_revision=None,
        summary=None,
    ):
        request = dict(
            summary=summary,
            project=project,
            title=title,
            body=body,
            acceptance_criteria=acceptance_criteria,
            source=source,
            user_request=user_request,
            workstream_id=workstream_id,
            group_id=group_id,
            group_expected_revision=group_expected_revision,
        )

        def operation(db, event):
            project_id = self._project(db, project, event)["id"]
            if workstream_id is not None:
                self._workstream(db, workstream_id, project_id)
            if group_id:
                group = self._task(db, group_id, {})
                if group["object_type"] != "group":
                    raise TaskError("invalid_group")
                if group_expected_revision is None:
                    raise TaskError("group_revision_required: read the group first")
                self._revision(group, group_expected_revision)
                self._require_mutable(db, group, concrete=False)
            task_id = self._insert_task(
                db,
                project_id,
                title,
                body,
                acceptance_criteria,
                group_id,
                source,
                user_request,
            )
            self._validate_summary(summary)
            db.execute(
                "UPDATE tasks SET summary=?,summary_spec_revision=? WHERE id=?",
                (summary, 1 if summary is not None else None, task_id),
            )
            if group_id:
                self._copy_prerequisites(db, task_id, group_id)
                updated_group = {
                    **group,
                    "revision": group["revision"] + 1,
                    "updated_at": timestamp(),
                }
                self._save_task(db, updated_group)
            if workstream_id:
                db.execute("INSERT INTO scope_members VALUES (?,?)", (workstream_id, task_id))
                self._touch_workstream(db, workstream_id)
            task = self._details(db, self._task(db, task_id, event))
            if group_id:
                event["before"] = {"group": group}
                event["after"] = {"task": task, "group": self._details(db, updated_group)}
            else:
                event["after"] = task
            ack = self._task_ack(db, task)
            ack.update(
                changed=True,
                specification_etag=self._specification_etag(task),
                project_order_revision=self._order_revision(db, project_id),
            )
            if workstream_id:
                ack["workstream_id"] = workstream_id
                ack["workstream_revision"] = self._workstream(db, workstream_id)["revision"]
                ack["in_scope"] = task_id in self._scope_ids(db, workstream_id)
                if not ack["in_scope"] and "task_out_of_scope" not in ack["gate_diagnostics"]:
                    ack["gate_diagnostics"].append("task_out_of_scope")
            if group_id:
                ack.update(group_id=group_id, group_revision=updated_group["revision"])
            return ack

        return self._run("task.created", request, operation)

    def update_task(self, task_id, expected_revision, changes, specification_etag=None):
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            changes=changes,
            specification_etag=specification_etag,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            allowed = {"title", "body", "acceptance_criteria", "summary"}
            if not isinstance(changes, dict) or set(changes) - allowed:
                raise TaskError("invalid_patch: edit title, body, acceptance_criteria or summary")
            if set(changes) != {"summary"}:
                self._require_mutable(db, before, concrete=False)
            if "summary" in changes:
                self._validate_summary(changes["summary"])
            if {"body", "acceptance_criteria"} & changes.keys():
                if specification_etag != self._specification_etag(before):
                    raise TaskError(
                        "specification_read_required: re-read the full specification with "
                        "get_tasks(specification=true), reconcile your replacement, and supply its "
                        "specification_etag and current revision"
                    )
            after = {**before, **changes}
            if (
                not isinstance(after["title"], str)
                or not after["title"].strip()
                or any(not isinstance(after[k], str) for k in ("body", "acceptance_criteria"))
            ):
                raise TaskError("invalid_specification")
            spec_changed = any(before[k] != after[k] for k in allowed - {"summary"})
            if spec_changed:
                after["spec_revision"] = before["spec_revision"] + 1
            if "summary" in changes:
                after["summary_spec_revision"] = (
                    after["spec_revision"] if after["summary"] is not None else None
                )
            summary_changed = any(
                before[k] != after[k] for k in ("summary", "summary_spec_revision")
            )
            after["summary_stale"] = self._summary_stale(after)
            changed = spec_changed or summary_changed
            if changed:
                after.update(
                    revision=before["revision"] + 1,
                    updated_at=timestamp(),
                )
                self._save_task(db, after)
            scope.update(before=before, after=after)
            token = (
                {"specification_etag": self._specification_etag(after)}
                if specification_etag == self._specification_etag(before)
                else {}
            )
            return (
                self._task_ack(db, after)
                | token
                | {
                    "changed": changed,
                    "summary_changed": summary_changed,
                    "spec_changed": spec_changed,
                }
            )

        return self._run("task.updated", request, operation)

    def set_disposition(self, task_id, expected_revision, disposition, note, authorization=None):
        """Change disposition while preserving queue placement and all proof."""
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            disposition=disposition,
            note=note,
            authorization=authorization,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if (
                disposition not in {"open", "deferred", "dropped"}
                or not isinstance(note, str)
                or not note.strip()
            ):
                raise TaskError("invalid_disposition: use open, deferred or dropped with a reason")
            if before["status"] == "dropped" and disposition != "dropped":
                if not isinstance(authorization, str) or not authorization.strip():
                    raise TaskError("revival_authorization_required: record the actual instruction")
            after = {
                **before,
                "status": disposition,
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            changed = before["status"] != after["status"]
            if changed:
                self._save_task(db, after)
            else:
                after = dict(before)
            scope.update(before=before, after=after)
            return self._task_ack(db, after) | {"changed": changed}

        return self._run("task.disposition_changed", request, operation)

    def propose_prerequisite(
        self,
        task_id,
        expected_revision,
        title,
        body="",
        acceptance_criteria="",
        workstream_id=None,
        milestone="review",
    ):
        """Atomically create and link a pending prerequisite in this scope or inbox."""
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            title=title,
            body=body,
            acceptance_criteria=acceptance_criteria,
            workstream_id=workstream_id,
            milestone=milestone,
        )

        def operation(db, scope):
            self._validate_prerequisite_milestone(milestone)
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if workstream_id is not None:
                self._workstream(db, workstream_id, before["project_id"])
                if task_id not in self._scope_ids(db, workstream_id):
                    raise TaskError("task_out_of_scope")
            proposed_id = self._insert_task(
                db, before["project_id"], title, body, acceptance_criteria
            )
            self._insert_prerequisite(db, task_id, proposed_id, milestone)
            if workstream_id:
                db.execute("INSERT INTO scope_members VALUES (?, ?)", (workstream_id, proposed_id))
                self._touch_workstream(db, workstream_id)
            after = {**before, "revision": before["revision"] + 1, "updated_at": timestamp()}
            self._save_task(db, after)
            scope.update(before=before, after={"task": after, "proposal_id": proposed_id})
            return {
                "task": self._details(db, after),
                "proposal": self._details(db, self._task(db, proposed_id, {})),
            }

        return self._run("gate.prerequisite_proposed", request, operation)

    def add_unresolved(self, task_id, expected_revision, text, handling="active"):
        request = dict(
            task_id=task_id, expected_revision=expected_revision, text=text, handling=handling
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if not isinstance(text, str) or not text.strip():
                raise TaskError("invalid_unresolved_item")
            if handling == "observer":
                proposal = dict(
                    id=_id("gat_"),
                    task_id=task_id,
                    gate_type="unresolved",
                    detail=text,
                    proposer=self.actor,
                    created_at=timestamp(),
                )
                db.execute(
                    "INSERT INTO gate_proposals "
                    "(id,task_id,gate_type,detail,proposer,created_at) VALUES "
                    "(:id,:task_id,:gate_type,:detail,:proposer,:created_at)",
                    proposal,
                )
                scope["after"] = proposal
                return proposal
            if handling not in {"active", "user"}:
                raise TaskError("invalid_handling")
            after = {
                **before,
                "unresolved_items": before["unresolved_items"]
                + [{"id": _id("unr_"), "text": text.strip()}],
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            self._save_task(db, after)
            scope.update(before=before, after=after)
            return self._details(db, after)

        return self._run("gate.unresolved_added", request, operation)

    def resolve_unresolved(self, task_id, expected_revision, item_id, user_note):
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            item_id=item_id,
            user_note=user_note,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if not user_note.strip():
                raise TaskError("user_verdict_required")
            items = [x for x in before["unresolved_items"] if x["id"] != item_id]
            if len(items) == len(before["unresolved_items"]):
                raise TaskError("unknown_unresolved_item")
            after = {
                **before,
                "unresolved_items": items,
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            self._save_task(db, after)
            scope.update(before=before, after=after)
            return self._details(db, after)

        return self._run("gate.unresolved_resolved", request, operation)

    def add_prerequisite(
        self, task_id, expected_revision, blocked_by_id, handling="active", milestone="review"
    ):
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            blocked_by_id=blocked_by_id,
            handling=handling,
            milestone=milestone,
        )

        def operation(db, scope):
            self._validate_prerequisite_milestone(milestone)
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            dependency = self._task(db, blocked_by_id, {})
            if task_id == blocked_by_id:
                raise TaskError("invalid_prerequisite")
            if dependency["status"] == "dropped":
                raise TaskError("invalid_prerequisite: dropped work cannot satisfy a dependency")
            if handling == "observer":
                proposal = dict(
                    id=_id("gat_"),
                    task_id=task_id,
                    gate_type="prerequisite",
                    detail=blocked_by_id,
                    proposer=self.actor,
                    created_at=timestamp(),
                    milestone=milestone,
                )
                db.execute(
                    "INSERT INTO gate_proposals "
                    "(id,task_id,gate_type,detail,proposer,created_at,milestone) VALUES "
                    "(:id,:task_id,:gate_type,:detail,:proposer,:created_at,:milestone)",
                    proposal,
                )
                scope["after"] = proposal
                return proposal
            if handling not in {"active", "user"}:
                raise TaskError("invalid_handling")
            if self._would_cycle(db, task_id, blocked_by_id):
                raise TaskError("prerequisite_cycle")
            changed = self._insert_prerequisite(db, task_id, blocked_by_id, milestone)
            after = {
                **before,
                "revision": before["revision"] + int(changed),
                "updated_at": timestamp() if changed else before["updated_at"],
            }
            self._save_task(db, after)
            scope.update(before=before, after=after)
            return self._details(db, after) | {"changed": changed}

        return self._run("gate.prerequisite_added", request, operation)

    def remove_prerequisite(self, task_id, expected_revision, blocked_by_id, note):
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            blocked_by_id=blocked_by_id,
            note=note,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if not isinstance(note, str) or not note.strip():
                raise TaskError("decision_note_required")
            row = db.execute(
                "SELECT * FROM prerequisites WHERE task_id=? AND blocked_by_id=?",
                (task_id, blocked_by_id),
            ).fetchone()
            removed = dict(row) if row is not None else None
            changed = (
                db.execute(
                    "DELETE FROM prerequisites WHERE task_id=? AND blocked_by_id=?",
                    (task_id, blocked_by_id),
                ).rowcount
                > 0
            )
            after = {
                **before,
                "revision": before["revision"] + int(changed),
                "updated_at": timestamp() if changed else before["updated_at"],
            }
            if changed:
                self._save_task(db, after)
            scope.update(
                before={"task": before, "prerequisite": removed},
                after={
                    "task": after,
                    "removed_prerequisite": removed,
                    "note": note.strip(),
                    "changed": changed,
                },
            )
            return self._details(db, after) | {"changed": changed}

        return self._run("gate.prerequisite_removed", request, operation)

    def accept_gate_proposal(self, proposal_id, expected_revision):
        request = dict(proposal_id=proposal_id, expected_revision=expected_revision)

        def operation(db, scope):
            row = db.execute("SELECT * FROM gate_proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise TaskError("unknown_gate_proposal")
            before = self._task(db, row["task_id"], scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if row["gate_type"] == "unresolved":
                after = {
                    **before,
                    "unresolved_items": before["unresolved_items"]
                    + [{"id": _id("unr_"), "text": row["detail"]}],
                }
            else:
                dependency = self._task(db, row["detail"], {})
                if dependency["status"] == "dropped":
                    raise TaskError("invalid_prerequisite")
                if self._would_cycle(db, row["task_id"], row["detail"]):
                    raise TaskError("prerequisite_cycle")
                self._insert_prerequisite(db, row["task_id"], row["detail"], row["milestone"])
                after = dict(before)
            after.update(revision=before["revision"] + 1, updated_at=timestamp())
            self._save_task(db, after)
            db.execute("DELETE FROM gate_proposals WHERE id=?", (proposal_id,))
            scope.update(before=before, after=after)
            return self._details(db, after)

        return self._run("gate.proposal_accepted", request, operation)

    def dismiss_gate_proposal(self, proposal_id, expected_revision, note):
        """Resolve a pending observer proposal without activating its gate."""
        request = dict(proposal_id=proposal_id, expected_revision=expected_revision, note=note)

        def operation(db, scope):
            row = db.execute("SELECT * FROM gate_proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise TaskError("unknown_gate_proposal")
            before = self._task(db, row["task_id"], scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if not isinstance(note, str) or not note.strip():
                raise TaskError("decision_note_required")
            proposal = dict(row)
            after = {**before, "revision": before["revision"] + 1, "updated_at": timestamp()}
            self._save_task(db, after)
            db.execute("DELETE FROM gate_proposals WHERE id=?", (proposal_id,))
            scope.update(
                before={"task": before, "proposal": proposal},
                after={"task": after, "dismissed_proposal": proposal, "note": note.strip()},
            )
            return self._details(db, after)

        return self._run("gate.proposal_dismissed", request, operation)

    @staticmethod
    def _validate_prerequisite_milestone(milestone):
        if not isinstance(milestone, str) or milestone not in {"review", "signoff"}:
            raise TaskError("invalid_prerequisite_milestone: use review or signoff")

    @staticmethod
    def _insert_prerequisite(db, task_id, blocked_by_id, milestone):
        changed = (
            db.execute(
                "INSERT OR IGNORE INTO prerequisites (task_id,blocked_by_id,milestone) "
                "VALUES (?,?,?)",
                (task_id, blocked_by_id, milestone),
            ).rowcount
            > 0
        )
        existing = db.execute(
            "SELECT milestone FROM prerequisites WHERE task_id=? AND blocked_by_id=?",
            (task_id, blocked_by_id),
        ).fetchone()
        if existing["milestone"] != milestone:
            raise TaskError(
                "prerequisite_milestone_conflict: the existing link has another milestone"
            )
        return changed

    @staticmethod
    def _would_cycle(db, task_id, blocked_by_id):
        # A group needs every member to complete, so it has implicit edges to
        # those members in the effective dependency graph.
        pending = [blocked_by_id]
        visited = set()
        while pending:
            current = pending.pop()
            if current == task_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(
                row["blocked_by_id"]
                for row in db.execute(
                    "SELECT blocked_by_id FROM prerequisites WHERE task_id=?", (current,)
                )
            )
            pending.extend(
                row["id"]
                for row in db.execute("SELECT id FROM tasks WHERE parent_group_id=?", (current,))
            )
        return False

    @staticmethod
    def _copy_prerequisites(db, task_id, source_id):
        # Fresh members inherit the parent's edges inside the same transaction.
        # Check the effective graph, including membership already inserted above.
        for row in db.execute(
            "SELECT blocked_by_id,milestone FROM prerequisites WHERE task_id=?", (source_id,)
        ).fetchall():
            if Store._would_cycle(db, task_id, row["blocked_by_id"]):
                raise TaskError("prerequisite_cycle")
            Store._insert_prerequisite(db, task_id, row["blocked_by_id"], row["milestone"])

    def decompose_task(self, task_id, expected_revision, members):
        request = dict(task_id=task_id, expected_revision=expected_revision, members=members)

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            if before["object_type"] != "task" or before["parent_group_id"]:
                raise TaskError("invalid_group: nested groups are not supported")
            self._require_mutable(db, before)
            if before["status"] != "open":
                raise TaskError("invalid_group: resume the task before decomposition")
            if before["unresolved_items"]:
                raise TaskError("unresolved_items: settle them before decomposition")
            if db.execute("SELECT 1 FROM gate_proposals WHERE task_id=?", (task_id,)).fetchone():
                raise TaskError("gate_proposals: resolve proposed gates before decomposition")
            if db.execute("SELECT 1 FROM attempts WHERE task_id=?", (task_id,)).fetchone():
                raise TaskError("attempt_exists: cannot decompose after implementation")
            if not isinstance(members, list) or not 1 <= len(members) <= 20:
                raise TaskError("members_required")
            after = {
                **before,
                "object_type": "group",
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            self._save_task(db, after)
            db.execute(
                "UPDATE projects SET order_revision=order_revision+1 WHERE id=?",
                (before["project_id"],),
            )
            db.execute(
                "INSERT OR IGNORE INTO scope_groups "
                "SELECT workstream_id,? FROM scope_members WHERE task_id=?",
                (task_id, task_id),
            )
            for row in db.execute(
                "SELECT workstream_id FROM scope_members WHERE task_id=?", (task_id,)
            ).fetchall():
                self._touch_workstream(db, row["workstream_id"])
            children = []
            for member in members:
                child_id = self._insert_task(
                    db,
                    before["project_id"],
                    member["title"],
                    member.get("body", ""),
                    member.get("acceptance_criteria", ""),
                    task_id,
                )
                self._copy_prerequisites(db, child_id, task_id)
                children.append(child_id)
            db.execute("DELETE FROM prerequisites WHERE task_id=?", (task_id,))
            scope.update(before=before, after={"group": after, "members": children})
            return self._details(db, after)

        return self._run("task.decomposed", request, operation)

    @staticmethod
    def _order_revision(db, project_id):
        return db.execute(
            "SELECT order_revision FROM projects WHERE id=?", (project_id,)
        ).fetchone()[0]

    def reorder_tasks(
        self, project, task_id, anchor_id, position, expected_order_revision, instruction
    ):
        """Move one concrete task immediately before/after an anchor in shared project order."""
        request = dict(
            project=project,
            task_id=task_id,
            anchor_id=anchor_id,
            position=position,
            expected_order_revision=expected_order_revision,
            instruction=instruction,
        )

        def operation(db, scope):
            project_id = self._project(db, project, scope)["id"]
            revision = self._order_revision(db, project_id)
            if type(expected_order_revision) is not int or expected_order_revision != revision:
                raise TaskError("revision_conflict: re-read the project order revision")
            if not isinstance(position, str) or position not in {"before", "after"}:
                raise TaskError("invalid_order: position must be before or after")
            if not isinstance(instruction, str) or not instruction.strip():
                raise TaskError(
                    "scheduling_instruction_required: record the actual instruction or authority"
                )
            moving = self._task(db, task_id, {})
            anchor = self._task(db, anchor_id, {})
            if task_id == anchor_id:
                raise TaskError("invalid_order: a task cannot anchor itself")
            if any(
                t["object_type"] != "task" or t["project_id"] != project_id
                for t in (moving, anchor)
            ):
                raise TaskError(
                    "invalid_order: task and anchor must be concrete tasks in this project"
                )
            current = [
                dict(row)
                for row in db.execute(
                    "SELECT id,order_key FROM tasks WHERE project_id=? AND object_type='task' "
                    "ORDER BY order_key,id",
                    (project_id,),
                )
            ]
            old_ids = [row["id"] for row in current]
            new_ids = [identity for identity in old_ids if identity != task_id]
            target = new_ids.index(anchor_id) + (position == "after")
            new_ids.insert(target, task_id)
            changed = new_ids != old_ids
            if changed:
                # Order is scheduling metadata. Do not touch task/spec revisions,
                # acceptance, selected attempts, reviews, or completed proof.
                for index, identity in enumerate(new_ids, 1):
                    db.execute("UPDATE tasks SET order_key=? WHERE id=?", (index, identity))
                db.execute(
                    "UPDATE projects SET order_revision=order_revision+1 WHERE id=?", (project_id,)
                )
            ack = {
                "project_id": project_id,
                "task_id": task_id,
                "anchor_id": anchor_id,
                "project_order_revision": revision + int(changed),
                "changed": changed,
            }
            scope.update(
                task_id=task_id,
                before={"project_order_revision": revision, "order_key": moving["order_key"]},
                after=ack,
            )
            return ack

        return self._run("tasks.reordered", request, operation)

    @staticmethod
    def _prerequisite_satisfied(db, dependency, milestone):
        """Canonical milestone across workstreams; never implies local code integration."""
        if dependency["object_type"] == "group":
            members = db.execute(
                "SELECT id,object_type,status,spec_revision FROM tasks WHERE parent_group_id=?",
                (dependency["id"],),
            ).fetchall()
            return bool(members) and all(
                Store._prerequisite_satisfied(db, member, milestone) for member in members
            )
        if dependency["status"] == "done":
            return True
        if milestone == "signoff" or dependency["status"] in {"dropped", "deferred"}:
            return False
        return bool(
            db.execute(
                "SELECT 1 FROM attempts WHERE task_id=? AND spec_revision=? "
                "AND state IN ('passed','human_review') LIMIT 1",
                (dependency["id"], dependency["spec_revision"]),
            ).fetchone()
        )

    @staticmethod
    def _prerequisite_references(db, task_id):
        """Canonical milestones and completion, without remote proof or scope."""
        references = []
        for row in db.execute(
            "SELECT dependency.id,dependency.title,dependency.object_type,dependency.status,"
            "dependency.project_id,dependency.spec_revision,p.milestone,"
            "project.name AS project_name "
            "FROM prerequisites p JOIN tasks dependency ON dependency.id=p.blocked_by_id "
            "LEFT JOIN projects project ON project.id=dependency.project_id "
            "WHERE p.task_id=? ORDER BY dependency.id",
            (task_id,),
        ):
            group = row["object_type"] == "group"
            complete = Store._group_complete(db, row["id"]) if group else row["status"] == "done"
            satisfied = Store._prerequisite_satisfied(db, row, row["milestone"])
            references.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "object_type": row["object_type"],
                    "project_id": None if group else row["project_id"],
                    "project_name": None if group else row["project_name"],
                    "state": ("complete" if complete else "incomplete") if group else row["status"],
                    "complete": complete,
                    "milestone": row["milestone"],
                    "satisfied": satisfied,
                    "blocking": not satisfied,
                }
            )
        return references

    @staticmethod
    def _unsatisfied_prerequisite(db, task_id):
        return any(
            not Store._prerequisite_satisfied(db, row, row["milestone"])
            for row in db.execute(
                "SELECT dependency.id,dependency.object_type,dependency.status,"
                "dependency.spec_revision,p.milestone FROM prerequisites p JOIN tasks dependency "
                "ON dependency.id=p.blocked_by_id WHERE p.task_id=?",
                (task_id,),
            )
        )

    @staticmethod
    def _gate_reasons(db, task, workstream_id=None):
        # Retained gates and attempts on inactive tasks are history, not active work.
        if task["object_type"] == "group" or task["status"] in {"done", "dropped", "deferred"}:
            return ["closed_or_group"]
        reasons = []
        if not Store._membership_ids(db, task):
            reasons.append("inbox")
        if task["unresolved_items"]:
            reasons.append("unresolved_items")
        if Store._unsatisfied_prerequisite(db, task["id"]):
            reasons.append("prerequisites")
        attempts = db.execute(
            "SELECT state FROM attempts WHERE task_id=? AND spec_revision=?"
            + (" AND workstream_id=?" if workstream_id else ""),
            (task["id"], task["spec_revision"], workstream_id)
            if workstream_id
            else (task["id"], task["spec_revision"]),
        ).fetchall()
        if any(r["state"] == "review" for r in attempts):
            reasons.append("review")
        if any(r["state"] in {"passed", "human_review"} for r in attempts):
            reasons.append("signoff")
        return reasons

    def get_next_action(self, workstream_id):
        """One autonomous action in shared order; selected proof is local/current only."""

        def operation(db, scope):
            ws = self._workstream(db, workstream_id)
            scope["project_id"] = ws["project_id"]
            ids = self._scope_ids(db, workstream_id)
            result = {
                "action": None,
                "task": None,
                "attempt": None,
                "project_order_revision": self._order_revision(db, ws["project_id"]),
                "diagnostics": {"scope_empty": not ids, "scoped": len(ids)},
            }
            counts = dict.fromkeys(
                (
                    "inbox",
                    "unresolved_items",
                    "prerequisites",
                    "review",
                    "signoff",
                    "closed_or_group",
                ),
                0,
            )
            if not ids:
                return result | {"diagnostics": result["diagnostics"] | counts}
            marks = ",".join("?" for _ in ids)
            for row in db.execute(
                f"SELECT id FROM tasks WHERE id IN ({marks}) ORDER BY order_key,id", ids
            ):
                task = self._task(db, row["id"], {})
                reasons = self._gate_reasons(db, task, workstream_id)
                # Review is an action, but never bypasses autonomous authority gates.
                blockers = [r for r in reasons if r not in {"review", "signoff"}]
                if not blockers:
                    pending = self._local_attempt(db, task, workstream_id, "review")
                    if pending or "signoff" not in reasons:
                        selected = self._specification(db, task, workstream_id)
                        selected["gate_diagnostics"] = reasons
                        proof = pending or self._local_attempt(db, task, workstream_id, "rework")
                        if proof:
                            selected["concerns"] = [
                                c for c in selected["concerns"] if c["attempt_id"] != proof["id"]
                            ]
                        return result | {
                            "action": "review" if pending else "implement",
                            "task": selected,
                            "attempt": proof,
                        }
                for reason in reasons:
                    counts[reason] += 1
            return result | {"diagnostics": result["diagnostics"] | counts}

        return self._run("task.next_action_read", {"workstream_id": workstream_id}, operation)

    @staticmethod
    def _local_attempt(db, task, workstream_id, state):
        row = db.execute(
            "SELECT * FROM attempts WHERE task_id=? AND workstream_id=? AND spec_revision=? "
            "AND state=? ORDER BY created_at DESC,id ASC LIMIT 1",
            (task["id"], workstream_id, task["spec_revision"], state),
        ).fetchone()
        return Store._attempt_details(row) if row else None

    def record_result(
        self,
        task_id,
        workstream_id,
        expected_revision,
        implementer,
        summary,
        evidence,
        artifacts,
        verification,
        specification_etag,
        concerns=None,
    ):
        request = dict(
            task_id=task_id,
            workstream_id=workstream_id,
            expected_revision=expected_revision,
            implementer=implementer,
            summary=summary,
            evidence=evidence,
            artifacts=artifacts,
            verification=verification,
            specification_etag=specification_etag,
            concerns=concerns,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            self._workstream(db, workstream_id, before["project_id"])
            if specification_etag != self._specification_etag(before):
                raise TaskError(
                    "full_specification_required: read the current full task and use its etag"
                )
            if not all(
                isinstance(x, str) and x.strip()
                for x in (implementer, summary, evidence, verification)
            ):
                raise TaskError(
                    "result_required: implementer, summary, evidence and actual verification"
                )
            if (
                not isinstance(artifacts, list)
                or not artifacts
                or any(
                    not isinstance(ref, dict)
                    or set(ref) != {"kind", "reference"}
                    or not isinstance(ref["kind"], str)
                    or ref["kind"] not in {"artifact", "commit"}
                    or not isinstance(ref["reference"], str)
                    or not ref["reference"].strip()
                    for ref in artifacts
                )
            ):
                raise TaskError("durable_artifacts_required: concrete artifact/commit references")
            recorded_concerns = self._validate_concerns(concerns, "implementer", implementer)
            now = timestamp()
            attempt = dict(
                id=_id("att_"),
                task_id=task_id,
                workstream_id=workstream_id,
                implementer=implementer,
                summary=summary,
                evidence=_json(
                    {
                        "format": "durable-result-v1",
                        "evidence": evidence,
                        "artifacts": artifacts,
                        "verification": verification,
                        "specification_etag": specification_etag,
                    }
                ),
                spec_revision=before["spec_revision"],
                state="review",
                reviewer=None,
                review_note=None,
                human_review_note=None,
                revision=1,
                created_at=now,
                updated_at=now,
                concerns_json=_json(recorded_concerns),
            )
            db.execute(
                """INSERT INTO attempts VALUES (:id,:task_id,:workstream_id,:implementer,
                :summary,:evidence,:spec_revision,:state,:reviewer,:review_note,:human_review_note,:revision,
                :created_at,:updated_at,:concerns_json)""",
                attempt,
            )
            after = {**before, "revision": before["revision"] + 1, "updated_at": now}
            self._save_task(db, after)
            scope.update(before=before, after={"task": after, "attempt": attempt})
            return {
                "id": attempt["id"],
                "revision": attempt["revision"],
                "task_id": task_id,
                "task_revision": after["revision"],
                "spec_revision": after["spec_revision"],
                "state": attempt["state"],
                "concern_count": len(recorded_concerns),
                "workstream_ids": Store._membership_ids(db, after),
                "adopted": bool(Store._membership_ids(db, after)),
                "status": after["status"],
                "gate_diagnostics": [
                    r
                    for r in self._gate_reasons(db, after, workstream_id)
                    if r != "closed_or_group"
                ],
            }

        return self._run("attempt.recorded", request, operation)

    @staticmethod
    def _validate_concerns(concerns, source, author):
        if concerns is None:
            return []
        if not isinstance(concerns, list) or any(
            not isinstance(c, dict)
            or set(c) != {"kind", "text"}
            or not isinstance(c["kind"], str)
            or c["kind"] not in {"value", "design"}
            or not isinstance(c["text"], str)
            or not c["text"].strip()
            for c in concerns
        ):
            raise TaskError("invalid_concerns: provide value/design kinds and nonempty text")
        return [{**c, "source": source, "author": author} for c in concerns]

    @staticmethod
    def _add_concerns(stored, concerns):
        """Append attributed contributions; omission preserves the stored bytes."""
        if not concerns:
            return stored
        return _json([*json.loads(stored), *concerns])

    def record_review(self, attempt_id, expected_revision, reviewer, verdict, note, concerns=None):
        request = dict(
            attempt_id=attempt_id,
            expected_revision=expected_revision,
            reviewer=reviewer,
            verdict=verdict,
            note=note,
            concerns=concerns,
        )

        def operation(db, scope):
            row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if not row:
                raise TaskError("unknown_attempt")
            before = dict(row)
            scope["task_id"] = before["task_id"]
            task = self._task(db, before["task_id"], {})
            scope["project_id"] = task["project_id"]
            self._require_mutable(db, task)
            if before["revision"] != expected_revision:
                raise TaskError(
                    "revision_conflict: reconcile get_attempt(attempt_id) "
                    "or a current list_task_attempts row"
                )
            if before["state"] != "review" or verdict not in {"pass", "rework"}:
                raise TaskError("invalid_review_state")
            if not reviewer.strip() or reviewer == before["implementer"] or not note.strip():
                raise TaskError("independent_review_required: reviewer differs from implementer")
            recorded_concerns = self._validate_concerns(concerns, "reviewer", reviewer)
            after = {
                **before,
                "concerns_json": self._add_concerns(before["concerns_json"], recorded_concerns),
                "state": "passed" if verdict == "pass" else "rework",
                "reviewer": reviewer,
                "review_note": note,
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            db.execute(
                """UPDATE attempts SET state=:state, reviewer=:reviewer,
                review_note=:review_note, concerns_json=:concerns_json, revision=:revision,
                updated_at=:updated_at WHERE id=:id""",
                after,
            )
            scope.update(before=before, after=after)
            return self._attempt_details(after)

        return self._run("attempt.reviewed", request, operation)

    def human_review(self, attempt_id, expected_revision, user_note):
        request = dict(
            attempt_id=attempt_id, expected_revision=expected_revision, user_note=user_note
        )

        def operation(db, scope):
            row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if not row:
                raise TaskError("unknown_attempt")
            before = dict(row)
            scope["task_id"] = before["task_id"]
            task = self._task(db, before["task_id"], {})
            scope["project_id"] = task["project_id"]
            self._require_mutable(db, task)
            if before["revision"] != expected_revision:
                raise TaskError("revision_conflict")
            if before["state"] != "review" or not user_note.strip():
                raise TaskError("human_review_required")
            after = {
                **before,
                "state": "human_review",
                "human_review_note": user_note,
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            db.execute(
                """UPDATE attempts SET state=:state, human_review_note=:human_review_note,
                revision=:revision, updated_at=:updated_at WHERE id=:id""",
                after,
            )
            scope.update(before=before, after=after)
            return self._attempt_details(after)

        return self._run("attempt.human_reviewed", request, operation)

    def signoff_task(
        self,
        task_id,
        expected_revision,
        decision,
        reasons=None,
        attempt_id=None,
        expected_attempt_revision=None,
    ):
        """Record the user's verdict and reasons on one exact reviewed result."""
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            decision=decision,
            reasons=reasons,
            attempt_id=attempt_id,
            expected_attempt_revision=expected_attempt_revision,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if before["status"] not in {"open", "rework"}:
                raise TaskError("invalid_signoff_target: resume the task before judging its result")
            if not isinstance(decision, str) or decision not in {
                "approve",
                "rework",
                "revise",
                "drop",
            }:
                raise TaskError("user_verdict_required: approve, rework, revise or drop")
            if reasons is not None and not isinstance(reasons, str):
                raise TaskError("invalid_reasons: supply text")
            if decision in {"rework", "revise"} and (not reasons or not reasons.strip()):
                raise TaskError("reasons_required: explain the rework or revised design")
            if not attempt_id:
                raise TaskError("attempt_required: select the reviewed result")
            row = db.execute(
                "SELECT * FROM attempts WHERE id=? AND task_id=?", (attempt_id, task_id)
            ).fetchone()
            if (
                not row
                or row["state"] not in {"passed", "human_review"}
                or row["spec_revision"] != before["spec_revision"]
            ):
                raise TaskError(
                    "review_required: select a current-spec passed or human-reviewed attempt"
                )
            attempt = dict(row)
            if (
                type(expected_attempt_revision) is not int
                or attempt["revision"] != expected_attempt_revision
            ):
                raise TaskError("revision_conflict: re-read the selected attempt")
            after = {**before, "revision": before["revision"] + 1, "updated_at": timestamp()}
            unresolved_id = None
            if decision == "approve":
                if before["unresolved_items"] or self._unsatisfied_prerequisite(db, before["id"]):
                    raise TaskError("task_not_ready_for_signoff")
                after.update(status="done", selected_attempt_id=attempt_id)
            elif decision == "rework":
                after["status"] = "rework"
                db.execute(
                    "UPDATE attempts SET state='rework', revision=revision+1, updated_at=? "
                    "WHERE id=?",
                    (after["updated_at"], attempt_id),
                )
            elif decision == "revise":
                unresolved_id = _id("unr_")
                after.update(status="open")
                after["unresolved_items"] = before["unresolved_items"] + [
                    {"id": unresolved_id, "text": reasons}
                ]
            else:
                after["status"] = "dropped"
            recorded = {
                "task_id": task_id,
                "task_revision": before["revision"],
                "spec_revision": before["spec_revision"],
                "attempt_id": attempt_id,
                "attempt_revision": attempt["revision"],
                "decision": decision,
                "reasons": reasons,
                "disposition": after["status"],
                "resulting_task_revision": after["revision"],
                "resulting_attempt_revision": attempt["revision"] + (decision == "rework"),
                "unresolved_id": unresolved_id,
            }
            self._save_task(db, after)
            scope.update(before=before, after={**after, "signoff_decision": recorded})
            return self._task_ack(db, after) | {
                "attempt_id": attempt_id,
                "attempt_revision": recorded["resulting_attempt_revision"],
                "attempt_state": "rework" if decision == "rework" else attempt["state"],
                "selected_attempt_id": after["selected_attempt_id"],
                "decision": decision,
                "unresolved_id": unresolved_id,
            }

        return self._run("task.signoff", request, operation)

    def list_tasks(self, project, workstream_id=None, state=None, limit=20, offset=0):
        request = dict(
            project=project, workstream_id=workstream_id, state=state, limit=limit, offset=offset
        )

        def operation(db, scope):
            self._page(limit, offset)
            project_id = self._project(db, project, scope)["id"]
            ids = None
            if workstream_id is not None:
                self._workstream(db, workstream_id, project_id)
                ids = set(self._scope_ids(db, workstream_id))
            result = []
            for row in db.execute(
                "SELECT id FROM tasks WHERE project_id=? AND object_type='task' "
                "ORDER BY order_key,id",
                (project_id,),
            ):
                if ids is not None and row["id"] not in ids:
                    continue
                task = self._task(db, row["id"], {})
                card = self._card(db, task, workstream_id)
                if state and state != card.get(
                    "view", self._status_view(task, card["gate_diagnostics"])
                ):
                    continue
                result.append(card)
            return {
                "project_id": project_id,
                "project_order_revision": self._order_revision(db, project_id),
                "total": len(result),
                **self._paged(result[offset : offset + limit + 1], limit, offset),
            }

        return self._run("tasks.listed", request, operation)

    def _markdown_export(self, db, project, ws, ids, include_closed):
        tasks = []
        references = {
            identity: {"explicit scope"} for identity in self._scope_group_ids(db, ws["id"])
        }
        for task in sorted(
            (self._details(db, self._task(db, identity, {})) for identity in ids),
            key=lambda task: (task["order_key"], task["id"]),
        ):
            if not include_closed and task["status"] in {"done", "dropped"}:
                continue
            task["view"] = self._status_view(task, self._gate_reasons(db, task, ws["id"]))
            if task["parent_group_id"]:
                references.setdefault(task["parent_group_id"], set()).add("task membership")
            for dependency in task["prerequisites"]:
                if dependency["object_type"] == "group":
                    references.setdefault(dependency["id"], set()).add("prerequisite")
            tasks.append(task)
        groups = []
        scoped = set(ids)
        exported = {task["id"] for task in tasks}
        for identity, reasons in references.items():
            group = self._details(db, self._task(db, identity, {}))
            group.update(
                reference=", ".join(sorted(reasons)),
                project_member_count=group["progress"]["by_project"].get(
                    project["id"], {"total": 0}
                )["total"],
                scoped_member_count=len(scoped.intersection(group["members"])),
                exported_member_count=len(exported.intersection(group["members"])),
            )
            groups.append(group)
        groups.sort(key=lambda group: (group["title"], group["id"]))
        content = render_markdown(project, ws, tasks, groups, len(ids), include_closed)
        return {
            "format": FORMAT,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content": content,
        }

    def export_workstream(self, workstream_id, include_closed=True, format="markdown"):
        def operation(db, scope):
            if format not in ("markdown", "legacy"):
                raise TaskError("invalid_export_format: use markdown or legacy")
            ws = self._workstream(db, workstream_id)
            scope["project_id"] = ws["project_id"]
            project = db.execute(
                "SELECT * FROM projects WHERE id=?", (ws["project_id"],)
            ).fetchone()
            ids = self._scope_ids(db, workstream_id)
            if format == "markdown":
                return self._markdown_export(db, project, ws, ids, include_closed)
            lines = [
                "# Task MCP workstream export",
                "",
                "Format: task-mcp/v1",
                f"Project: {project['id']} ({project['name']})",
                f"Workstream: {ws['id']} ({ws['name']})",
                "",
            ]
            groups = self._status_summary(db, workstream_id)["referenced_groups"]
            if groups:
                lines.extend(
                    [
                        "## Referenced groups",
                        "",
                        "Group progress is global. Task entries below are local to this project.",
                        "",
                        "```json",
                        _json(groups),
                        "```",
                        "",
                    ]
                )
            for task_id in sorted(
                ids,
                key=lambda x: db.execute("SELECT order_key FROM tasks WHERE id=?", (x,)).fetchone()[
                    0
                ],
            ):
                task = self._details(db, self._task(db, task_id, {}))
                if not include_closed and task["status"] in {"done", "dropped"}:
                    continue
                lines.extend(
                    [
                        f"## {task['title']}",
                        "",
                        f"ID: {task_id}",
                        f"Revision: {task['revision']}",
                        f"Workstreams: {', '.join(task['workstream_ids']) or 'inbox'}",
                        f"State: {task['status']}",
                        "",
                        task["body"],
                        "",
                        "### Acceptance criteria",
                        "",
                        task["acceptance_criteria"],
                        "",
                        "### Structured data",
                        "",
                        "```json",
                        _json(
                            {
                                key: task[key]
                                for key in (
                                    "unresolved_items",
                                    "blocked_by",
                                    "attempts",
                                    "selected_attempt_id",
                                    "parent_group_id",
                                    "object_type",
                                )
                            }
                        ),
                        "```",
                        "",
                    ]
                )
            content = "\n".join(lines)
            return {
                "format": "task-mcp/v1",
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content": content,
            }

        return self._run(
            "workstream.exported",
            {"workstream_id": workstream_id, "include_closed": include_closed, "format": format},
            operation,
        )
