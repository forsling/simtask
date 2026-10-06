"""Local SQLite task state and audit history."""

import base64
import hashlib
import json
import os
import re
import shlex
import sqlite3
import unicodedata
from collections.abc import Callable
from contextlib import closing
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from task_mcp.export import FORMAT, render_markdown

DATABASE_SCHEMA_REVISION = 11
COMPACT_CALL = ContextVar("compact_task_mcp_call", default=False)
# Concerns have explicit provenance in their own column, never in arbitrary proof text.
CONCERN_COUNT_SQL = "json_array_length(concerns_json)"
# Boards show active work by default; these statuses are counted, not listed.
INACTIVE_STATUSES = ("done", "deferred", "dropped")
# Optional board-card detail groups, in documentation order.
CARD_INCLUDE_GROUPS = ("blockers", "attempt", "concerns", "workstreams", "ids")
# Where a task stands for the user, as the viewer sections its board and counts
# Needs input in the sidebar. Store._standing is the only definition of this rule.
STANDINGS = ("signoff", "decision", "progress", "open", "deferred", "done", "dropped")
# Slim-card state words for gate views whose internal names are longer.
STATE_WORDS = {"unresolved_items": "question", "prerequisites": "blocked"}
# Values the list_tasks state filter accepts: slim state words, then older view names.
STATE_FILTERS = (
    "ready",
    "rework",
    "blocked",
    "question",
    "review",
    "signoff",
    "inbox",
    "out_of_scope",
    *INACTIVE_STATUSES,
    "group",
    *STATE_WORDS,
)

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
        order_revision INTEGER NOT NULL DEFAULT 0,
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
    """CREATE TABLE IF NOT EXISTS workstream_task_order (
        workstream_id TEXT NOT NULL REFERENCES workstreams(id),
        task_id TEXT NOT NULL REFERENCES tasks(id), order_key INTEGER NOT NULL,
        PRIMARY KEY(workstream_id, task_id))""",
    """CREATE TABLE IF NOT EXISTS legacy_membership_migration (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id), source_schema INTEGER NOT NULL,
        task_json TEXT NOT NULL, scopes_json TEXT NOT NULL,
        unresolved_id TEXT)""",
    # Personal project/workstream notes. An additive table that schema 10 servers never
    # read, so it needs no schema revision bump; a cleared note keeps its row (empty text)
    # so its revision never repeats.
    """CREATE TABLE IF NOT EXISTS notes (
        owner_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('project','workstream')),
        project_id TEXT NOT NULL REFERENCES projects(id),
        workstream_id TEXT REFERENCES workstreams(id),
        text TEXT NOT NULL CHECK(length(text) <= 2000),
        revision INTEGER NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
        CHECK(owner_id = coalesce(workstream_id, project_id)),
        CHECK((kind = 'workstream') = (workstream_id IS NOT NULL)))""",
    # Workstream archive state, additive like notes: schema 10 servers never read it, so
    # they keep listing an archived workstream as before. Unarchiving keeps the row
    # (archived=0) so its revision never repeats; no row means never archived.
    """CREATE TABLE IF NOT EXISTS workstream_archive (
        workstream_id TEXT PRIMARY KEY REFERENCES workstreams(id),
        archived INTEGER NOT NULL CHECK(archived IN (0,1)),
        reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 200),
        revision INTEGER NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)""",
    # When get_next_action last handed a task out in a workstream: information only,
    # never a lock. Additive like notes; a newer pick replaces the row, and reads ignore
    # it once it is older than PICK_TTL or a result/review in that workstream is newer.
    """CREATE TABLE IF NOT EXISTS task_picks (
        task_id TEXT NOT NULL REFERENCES tasks(id),
        workstream_id TEXT NOT NULL REFERENCES workstreams(id),
        action TEXT NOT NULL CHECK(action IN ('implement','review')),
        picked_at TEXT NOT NULL,
        PRIMARY KEY (task_id, workstream_id))""",
    """CREATE TABLE IF NOT EXISTS task_identities (
        internal_uuid TEXT PRIMARY KEY NOT NULL,
        public_id TEXT NOT NULL UNIQUE REFERENCES tasks(id))""",
)
# Tables added after schema 10 without a revision bump, so older servers keep opening
# the database. An existing database missing one is backed up before it is created.
ADDITIVE_TABLES = ("notes", "workstream_archive", "task_picks")
# A picked marker counts as work in progress for this long without a newer result.
PICK_TTL = timedelta(hours=4)
# Archive reasons are a short label for why a workstream left discovery.
ARCHIVE_REASON_LIMIT = 200
# Archived workstreams are left out of discovery and selection SQL unless requested.
ARCHIVED_IDS_SQL = "SELECT workstream_id FROM workstream_archive WHERE archived=1"

# init answers every request that is not ready in one shape: the state's fixed message,
# what exists (workstreams with their roles) and the calls that work (next).
INIT_MESSAGES = {
    "mismatch": "The request does not match the recorded bindings.",
    "archived": "The workstream bound here is archived and was not resumed. To unarchive it, "
    "also give a reason, then init again.",
    "new_branch": "No workstream is bound to this branch or name at this checkout.",
    "unregistered_checkout": "This checkout is not attached to a project.",
}
INIT_NAME_TAKEN = (
    " The name is already used (see name_holder), so calls that need it are not offered; "
    "give another workstream_name to use a different name."
)
INIT_NEXT = " workstreams shows what exists; next lists the calls that work, with their arguments."
INIT_WORKSTREAM_KEYS = (
    "id",
    "project_id",
    "name",
    "branch",
    "checkout_path",
    "revision",
    "archive",
)
# Notes are bounded so they stay a current summary rather than a growing log.
NOTE_LIMIT = 2000
NOTE_KINDS = ("project", "workstream")


# A quick idea captured in the browser waits in the inbox, held by this item, until an
# agent processes it with the user. Agents and the viewer recognise ideas by its prefix.
IDEA_ITEM = "Idea to process: turn into a proper brief or task with the user"
IDEA_TITLE_LIMIT = 200
IDEA_NOTE_LIMIT = 500

# Public task/group IDs: a new explicit or automatic ID has at most PUBLIC_ID_LIMIT
# characters. Older, longer IDs stay valid and resolvable (up to LEGACY_PUBLIC_ID_LIMIT).
PUBLIC_ID_LIMIT = 40
LEGACY_PUBLIC_ID_LIMIT = 96
PUBLIC_ID_PATTERN = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
# An omitted ID becomes a short slug of the title's first meaningful words: at most
# AUTO_ID_WORDS words, cut on a word boundary at AUTO_ID_TARGET characters.
AUTO_ID_TARGET = 30
AUTO_ID_WORDS = 5
ID_FILLER_WORDS = frozenset(
    """a an the and or but nor of to for in on at by with from into onto as via per is are
    was were be been being it its this that these those each every all any some instead so
    than then when while which who whom whose what will should can could would may might
    must shall there their them they we our us you your i me my""".split()
)


def short_task_slug(title):
    """The automatic public ID for a title, before any numeric suffix for duplicates.

    ASCII-fold and lowercase the title, drop apostrophes, split on anything else that is
    not a letter or digit, and drop filler words (unless every word is one). Keep words
    in order while the slug stays within AUTO_ID_TARGET characters and AUTO_ID_WORDS
    words; the first word is always kept, cut if it alone is too long. A slug that would
    start with a digit gets a leading "task" word; an empty one is "task".
    """
    ascii_title = unicodedata.normalize("NFKD", str(title)).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9]+", " ", ascii_title.lower().replace("'", "")).split()
    words = [w for w in words if w not in ID_FILLER_WORDS] or words
    if not words or not words[0][0].isalpha():
        words = ["task", *words]
    slug = words[0][:AUTO_ID_TARGET]
    for word in words[1:AUTO_ID_WORDS]:
        if len(slug) + 1 + len(word) > AUTO_ID_TARGET:
            break
        slug += "-" + word
    return slug


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
                missing = [
                    table
                    for table in ADDITIVE_TABLES
                    if not db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone()
                ]
                if existing and (version < DATABASE_SCHEMA_REVISION or missing):
                    # The writer lock prevents a commit between this online snapshot
                    # and the migration. A separate read connection includes WAL data.
                    self.migration_backup_path = (
                        self._backup_for_migration(version)
                        if version < DATABASE_SCHEMA_REVISION
                        else self._backup_for_migration(version, "pre-" + "-".join(missing))
                    )
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
        workstream_columns = {row["name"] for row in db.execute("PRAGMA table_info(workstreams)")}
        if "order_revision" not in workstream_columns:
            db.execute(
                "ALTER TABLE workstreams ADD COLUMN order_revision INTEGER NOT NULL DEFAULT 0"
            )
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
        if source_version < 10:
            Store._sync_orders(db, advance=False)
        # Public IDs and every existing relationship/audit byte stay unchanged. UUIDs
        # live in a private registry, never in task rows decoded for public payloads.
        for row in db.execute(
            "SELECT id FROM tasks WHERE id NOT IN (SELECT public_id FROM task_identities)"
        ).fetchall():
            db.execute("INSERT INTO task_identities VALUES (?,?)", (uuid4().hex, row["id"]))
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

    def _backup_for_migration(self, version: int, label: str | None = None) -> Path:
        """Create and verify a fresh SQLite online backup before changing schema."""
        label = label or f"pre-schema-{DATABASE_SCHEMA_REVISION}"
        backup = self.path.with_name(f"{self.path.name}.{label}.{uuid4().hex}.sqlite3")
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
                if (action == "task.updated" and result.get("group_changed")) or action in {
                    "project.initialized",
                    "session.initialized",
                    "workstream.initialized",
                    "scope.changed",
                    "task.workstream_added",
                    "task.workstream_removed",
                    "task.created",
                    "task.decomposed",
                    "group.member_added",
                    "group.created",
                }:
                    # Persist effective membership changes with the original mutation/audit.
                    # Reads never reconcile or reorder lists.
                    self._sync_orders(db)
                    if "workstream" in result:
                        result["workstream"] = self._workstream(db, result["workstream"]["id"])
                    identity = result.get("workstream_id") or (result.get("workstream") or {}).get(
                        "id"
                    )
                    if action == "scope.changed":
                        identity = result["id"]
                        result["order_revision"] = self._order_revision(db, identity)
                    if identity:
                        result["workstream_order_revision"] = self._order_revision(db, identity)
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
            "workstream.initialized",
            "scope.changed",
        }:
            if action == "scope.changed":
                return {k: result[k] for k in ("id", "project_id", "revision")} | {
                    "workstream_id": result["id"],
                    "workstream_revision": result["revision"],
                    "workstream_order_revision": result["order_revision"],
                    "changed": result["changed"],
                    **{k + "_count": len(result[k]) for k in ("members", "groups", "exclusions")},
                }
            if action == "workstream.initialized":
                return {
                    "workstream": result["workstream"],
                    "workstream_order_revision": result["workstream_order_revision"],
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
                    {
                        "id": r["id"],
                        "revision": r["revision"],
                        "order_revision": r["order_revision"],
                    }
                    for r in db.execute(
                        "SELECT w.id,w.revision,w.order_revision FROM workstreams w "
                        "JOIN scope_groups s "
                        "ON s.workstream_id=w.id WHERE s.group_id=? ORDER BY w.id",
                        (result["id"],),
                    )
                ],
            }
        if action.startswith("gate."):
            task = self._task(db, scope["task_id"], {})
            ack = self._task_ack(db, task) | {
                "task_id": task["id"],
                "task_revision": task["revision"],
                "changed": result.get("changed", True),
            }
            if action == "gate.unresolved_added":
                ack["unresolved_id"] = task["unresolved_items"][-1]["id"]
            if action == "gate.unresolved_resolved":
                ack["resolved_id"] = request["item_id"]
            if request.get("blocked_by_id"):
                ack["blocked_by_id"] = request["blocked_by_id"]
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
            return self._paged(
                [{key: row[key] for key in row.keys() if key != "order_revision"} for row in rows],
                limit,
                offset,
            )

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
            raise TaskError(
                "project_not_initialized: use init action=create_project before task operations"
            )
        result = dict(rows[0])
        result.pop("order_revision", None)
        scope["project_id"] = result["id"]
        return result

    @staticmethod
    def _workstream(db, workstream_id, project_id=None):
        row = db.execute("SELECT * FROM workstreams WHERE id=?", (workstream_id,)).fetchone()
        if row is None or (project_id and row["project_id"] != project_id):
            raise TaskError("unknown_workstream: initialize or select a workstream")
        return Store._with_archive(db, row)

    @staticmethod
    def _with_archive(db, row):
        """A workstream row plus its archive state, present once it was ever archived."""
        if row is None:
            return None
        ws = dict(row)
        archive = db.execute(
            "SELECT * FROM workstream_archive WHERE workstream_id=?", (ws["id"],)
        ).fetchone()
        if archive:
            ws["archive"] = {
                "archived": bool(archive["archived"]),
                "reason": archive["reason"],
                "revision": archive["revision"],
                "updated_at": archive["updated_at"],
                "updated_by": archive["updated_by"],
            }
        return ws

    @staticmethod
    def _archived(ws):
        return bool(ws and (ws.get("archive") or {}).get("archived"))

    @staticmethod
    def _archive_hint(ws):
        """How to unarchive ws, with the arguments that work when followed."""
        return (
            f"archive_workstream workstream_id={ws['id']} archived=false "
            f"expected_revision={ws['archive']['revision']} reason=<why>"
        )

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
                raise TaskError("project_initialized: use init to resume it")
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
            order_revision=0,
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

    def list_workstreams(
        self, project=None, limit=50, offset=0, include_archived=False, standings=False
    ):
        """standings=True (the viewer's internal option) adds each status's standing counts."""

        def operation(db, scope):
            self._page(limit, offset)
            self._validate_include_archived(include_archived)
            selected = self._project(db, project, scope) if project is not None else None
            where = (["w.project_id=?"] if selected else []) + (
                [] if include_archived else [f"w.id NOT IN ({ARCHIVED_IDS_SQL})"]
            )
            where_sql = ("WHERE " + " AND ".join(where) + " ") if where else ""
            values = (selected["id"],) if selected else ()
            rows = [
                self._with_archive(db, row)
                for row in db.execute(
                    "SELECT w.*, p.name AS project_name, p.canonical_path AS project_path "
                    "FROM workstreams w JOIN projects p ON p.id=w.project_id "
                    + where_sql
                    + "ORDER BY p.name,p.id,w.created_at,w.id LIMIT ? OFFSET ?",
                    values + (limit + 1, offset),
                )
            ]
            for row in rows:
                all_ids = self._ordered_scope_ids(db, row["id"])
                row["scope"] = all_ids[:3]
                groups = self._scope_group_ids(db, row["id"])
                row["groups"] = groups[:3]
                row["scope_total"] = len(all_ids)
                row["scope_has_more"] = len(all_ids) > 3
                row["groups_has_more"] = len(groups) > 3
                row["group_total"] = len(groups)
                row["status"] = self._status_summary(db, row["id"], all_ids, standings)
            page = self._paged(rows, limit, offset)
            if not include_archived:
                page["archived_hidden"] = db.execute(
                    f"SELECT count(*) FROM workstreams WHERE id IN ({ARCHIVED_IDS_SQL})"
                    + (" AND project_id=?" if selected else ""),
                    values,
                ).fetchone()[0]
            return page

        request = {
            "project": project,
            "limit": limit,
            "offset": offset,
            "include_archived": include_archived,
        }
        if standings:
            request["standings"] = True
        return self._run("workstreams.listed", request, operation)

    def workstream_status(
        self,
        workstream_id,
        limit=20,
        offset=0,
        include_scope=False,
        include_inactive=False,
        include_archived=False,
    ):
        request = dict(
            workstream_id=workstream_id,
            limit=limit,
            offset=offset,
            include_scope=include_scope,
            include_inactive=include_inactive,
            include_archived=include_archived,
        )

        def operation(db, scope):
            self._page(limit, offset)
            self._validate_include_inactive(include_inactive)
            self._validate_include_archived(include_archived)
            ws = self._workstream(db, workstream_id)
            project = db.execute(
                "SELECT * FROM projects WHERE id=?", (ws["project_id"],)
            ).fetchone()
            scope["project_id"] = ws["project_id"]
            if self._archived(ws) and not include_archived:
                # An archived workstream reports its state and counts, never its task list.
                return {
                    "project": {k: project[k] for k in project.keys() if k != "order_revision"},
                    "workstream": ws,
                    "status": self._status_summary(db, workstream_id),
                    "workstream_order_revision": ws["order_revision"],
                    "listing_skipped": "archived",
                    "message": (
                        f"Workstream {ws['name']!r} is archived ({ws['archive']['reason']}); "
                        "its tasks are not listed. Pass include_archived=true to list them, "
                        f"or unarchive it with {self._archive_hint(ws)}"
                    ),
                }
            queue = self._scoped_queue(db, workstream_id)
            hidden = (
                [] if include_inactive else [c for c in queue if c["status"] in INACTIVE_STATUSES]
            )
            queue = [c for c in queue if include_inactive or c["status"] not in INACTIVE_STATUSES]
            # A separate page keeps concerns visible even beyond the ordinary queue page.
            concerned = [item for item in queue if item["concern_count"]]
            concerned.sort(
                key=lambda item: (
                    item["view"] != "signoff",
                    item["workstream_order_key"],
                    item["id"],
                )
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
                    rows = [
                        r[0]
                        for r in db.execute(
                            f"SELECT {column} FROM {table} WHERE workstream_id=? ORDER BY {column}",
                            (workstream_id,),
                        )
                    ]
                    scope_page[name] = {
                        "ids": rows[offset : offset + limit],
                        "total": len(rows),
                        "next_offset": offset + limit if len(rows) > offset + limit else None,
                    }
            page = self._paged(queue[offset : offset + limit + 1], limit, offset)
            cache = {}
            page["items"] = [
                self._board_card(
                    db, self._task(db, card["id"], {}), workstream_id, full=card, cache=cache
                )
                for card in page["items"]
            ]
            return {
                "project": {key: project[key] for key in project.keys() if key != "order_revision"},
                "workstream": ws,
                "status": self._status_summary(db, workstream_id),
                "workstream_order_revision": ws["order_revision"],
                "total": len(queue),
                **({} if include_inactive else {"hidden": self._hidden_counts(hidden)}),
                "concern_tasks": {"total": len(concerned), **concern_page},
                **({"scope": scope_page} if include_scope else {}),
                **page,
            }

        return self._run("workstream.status_read", request, operation)

    def _scoped_queue(self, db, workstream_id):
        return [
            self._card(db, self._task(db, identity, {}), workstream_id)
            | {"workstream_order_key": index}
            for index, identity in enumerate(self._ordered_scope_ids(db, workstream_id), 1)
        ]

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

    def _status_summary(self, db, workstream_id, ids=None, standings=False):
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
        standing_counts = dict.fromkeys(STANDINGS, 0)
        concerned_tasks = concern_count = 0
        for item in self._scoped_queue(db, workstream_id):
            if standings and (standing := self._standing(item)) in standing_counts:
                standing_counts[standing] += 1
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
            **({"standings": standing_counts} if standings else {}),
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
        include_inactive=False,
        include_archived=False,
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
            include_inactive=include_inactive,
            include_archived=include_archived,
        )

        def operation(db, scope):
            self._validate_include_inactive(include_inactive)
            self._validate_include_archived(include_archived)
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
            if selected:
                selected.pop("order_revision", None)
            # An unknown workstream_id is unknown_workstream before any other report.
            requested = self._workstream(db, workstream_id) if workstream_id else None
            if requested and action in {"create_project", "new_workstream", "attach_workstream"}:
                # These actions create a workstream; the ID is accepted only when it already
                # is exactly this binding (an idempotent retry that resumes it unchanged).
                if not (
                    selected
                    and requested["project_id"] == selected["id"]
                    and requested["checkout_path"] == canonical
                    and (
                        requested["branch"] == branch
                        if branch
                        else requested["branch"] is None and requested["name"] == workstream_name
                    )
                ):
                    raise TaskError(
                        f"workstream_id_not_used: action={action} creates a new workstream and "
                        "does not use workstream_id; omit it, or confirm "
                        f"action=rebind_workstream to move workstream {requested['name']!r} here"
                    )
            if project is not None:
                chosen = dict(self._project(db, project, scope))
            elif selected is None and requested:
                # An unregistered checkout naming a workstream is checked within that
                # workstream's project, so a branch already bound there is reported.
                chosen = dict(
                    db.execute(
                        "SELECT * FROM projects WHERE id=?", (requested["project_id"],)
                    ).fetchone()
                )
            else:
                chosen = selected
            if chosen:
                chosen.pop("order_revision", None)
            here = {"path": canonical, "branch": branch, "workstream_name": workstream_name}
            here = {key: value for key, value in here.items() if value is not None}
            # A rebind renames the moved workstream to workstream_name or branch; a new one
            # takes that name stripped. UNIQUE(project_id, name) must hold.
            rebind_name = workstream_name or branch

            def bound_in(project_id):
                # The workstream bound to this branch (or, without one, this name).
                where = "branch=?" if branch else "branch IS NULL AND name=?"
                row = db.execute(
                    f"SELECT * FROM workstreams WHERE project_id=? AND {where}",
                    (project_id, branch or workstream_name),
                ).fetchone()
                return self._with_archive(db, row)

            def name_holder(project_id, moving=None):
                # Who holds the name a new workstream, or moving after a rebind, would take.
                row = db.execute(
                    "SELECT * FROM workstreams WHERE project_id=? AND name=? AND id IS NOT ?",
                    (
                        project_id,
                        rebind_name if moving else rebind_name.strip(),
                        moving and moving["id"],
                    ),
                ).fetchone()
                return self._with_archive(db, row)

            # Every response that is not ready has one shape: what exists, then each call
            # that works when made exactly as given. A call that would fail is left out.
            def offer(choice, arguments, tool="init"):
                return {"choice": choice, "tool": tool, "arguments": arguments}

            def resumable(ws):
                return {"include_archived": True} if self._archived(ws) else {}

            def resume(ws, own=False):
                # Resume ws here, or at its own checkout and branch (or name).
                where = here
                if own:
                    key, value = (
                        ("branch", ws["branch"])
                        if ws["branch"]
                        else ("workstream_name", ws["name"])
                    )
                    where = {"path": ws["checkout_path"], key: value}
                return offer("resume", where | {"workstream_id": ws["id"]} | resumable(ws))

            def rebind(ws):
                moved = {"action": "rebind_workstream", "workstream_id": ws["id"]}
                moved |= {"expected_revision": ws["revision"], "confirmed": True}
                return offer("rebind", here | moved | resumable(ws))

            def check(project_id=None):
                into = {"project": project_id} if project_id else {}
                listed = {"include_archived": True} if include_archived else {}
                return offer("check", here | into | listed)

            def movable(project_id, rows, new_choice):
                # A new workstream, then each listed one moved here, unless the name is taken.
                calls, listed = [], []
                holder = name_holder(project_id)
                if holder:
                    listed.append((holder, "name_holder"))
                else:
                    into = {"project": project_id} if new_choice == "attach_workstream" else {}
                    calls.append(
                        offer("create", here | {"action": new_choice, **into, "confirmed": True})
                    )
                for ws in rows:
                    holder = name_holder(project_id, ws)
                    if holder:
                        listed.append((holder, "name_holder"))
                    else:
                        calls.append(rebind(ws))
                return calls, listed

            def report(state, project_row, listed, calls, **extra):
                found = {}
                for ws, role in listed:
                    if ws:
                        item = found.setdefault(
                            ws["id"],
                            {key: ws[key] for key in INIT_WORKSTREAM_KEYS if key in ws}
                            | {"roles": []},
                        )
                        if role not in item["roles"]:
                            item["roles"].append(role)
                taken = any("name_holder" in item["roles"] for item in found.values())
                return {
                    "state": state,
                    "message": INIT_MESSAGES[state]
                    + (INIT_NAME_TAKEN if taken else "")
                    + INIT_NEXT,
                    "path": canonical,
                    "branch": branch,
                    "workstream_name": workstream_name,
                    "project": project_row,
                    "workstreams": list(found.values()),
                    "next": calls,
                    **extra,
                }

            if selected and chosen["id"] != selected["id"]:
                # project= names another project than the one this checkout is attached to.
                bound = bound_in(selected["id"])
                local = bound if bound and bound["checkout_path"] == canonical else None
                calls = [resume(local) if local else check()]
                if requested and requested["id"] != (local or {}).get("id"):
                    calls.append(resume(requested, own=True))
                listed = [(requested, "requested"), (bound, "bound")]
                return report("mismatch", selected, listed, calls)
            bound = bound_in(chosen["id"]) if chosen else None
            if bound and bound["checkout_path"] == canonical and selected:
                if requested and requested["id"] != bound["id"]:
                    # Report the requested workstream's real binding, never the local one
                    # in its place.
                    listed = [(requested, "requested"), (bound, "bound")]
                    calls = [resume(bound), resume(requested, own=True)]
                    return report("mismatch", selected, listed, calls)
                if self._archived(bound) and not include_archived:
                    # Never silently resume an archived workstream at its own checkout.
                    unarchive = {"workstream_id": bound["id"], "archived": False}
                    unarchive["expected_revision"] = bound["archive"]["revision"]
                    calls = [resume(bound), offer("unarchive", unarchive, "archive_workstream")]
                    listed = [(requested, "requested"), (bound, "bound")]
                    return report("archived", selected, listed, calls)
                return self._ready_init(db, selected, bound, include_inactive) | {"changed": False}
            if bound and not (
                action == "rebind_workstream" and confirmed and workstream_id == bound["id"]
            ):
                # The branch is bound at another checkout: offer its own binding (or the
                # requested workstream's), or moving it here. Never one for the other.
                holder = name_holder(chosen["id"], bound)
                calls = [resume(requested or bound, own=True)] + ([] if holder else [rebind(bound)])
                listed = [(requested, "requested"), (bound, "bound"), (holder, "name_holder")]
                return report("mismatch", chosen, listed, calls)
            new_choice = "new_workstream" if selected else "attach_workstream"
            if requested and not (action and confirmed):
                # The checkout/branch match check: a named binding must match exactly.
                # Rebinding never crosses projects (workstream_project_mismatch).
                rows = [requested] if requested["project_id"] == chosen["id"] else []
                calls, listed = movable(chosen["id"], rows, new_choice)
                calls = [resume(requested, own=True)] + calls
                return report("mismatch", chosen, [(requested, "requested")] + listed, calls)
            if not action or not confirmed:
                # Discovery leaves archived workstreams out unless include_archived=true.
                unarchived = "" if include_archived else f" AND id NOT IN ({ARCHIVED_IDS_SQL})"
                if chosen:
                    rows = [
                        self._with_archive(db, row)
                        for row in db.execute(
                            "SELECT * FROM workstreams WHERE project_id=?"
                            + unarchived
                            + " ORDER BY created_at,id LIMIT 11",
                            (chosen["id"],),
                        ).fetchall()
                    ]
                    more = len(rows) > 10
                    rows = rows[:10]
                    # When the name is taken, only its holder can move here; it may be
                    # archived or beyond the first ten, so it is listed too.
                    holder = name_holder(chosen["id"])
                    if holder and holder["id"] not in {ws["id"] for ws in rows}:
                        rows.append(holder)
                    calls, listed = movable(chosen["id"], rows, new_choice)
                    hidden = 0
                    if not include_archived:
                        hidden = db.execute(
                            "SELECT count(*) FROM workstreams WHERE project_id=? "
                            f"AND id IN ({ARCHIVED_IDS_SQL})",
                            (chosen["id"],),
                        ).fetchone()[0]
                    return report(
                        "new_branch" if selected else "unregistered_checkout",
                        chosen,
                        [(ws, "candidate") for ws in rows] + listed,
                        calls,
                        more_workstreams=more,
                        **({"archived_hidden": hidden} if hidden else {}),
                    )
                # Without project=, create a project or check one to join; checking it
                # lists the calls that work within that project.
                projects = [
                    {key: row[key] for key in row.keys() if key != "order_revision"}
                    for row in db.execute(
                        "SELECT * FROM projects ORDER BY name,id LIMIT 11"
                    ).fetchall()
                ]
                create = offer("create", here | {"action": "create_project", "confirmed": True})
                return report(
                    "unregistered_checkout",
                    None,
                    [],
                    [create] + [check(row["id"]) for row in projects[:10]],
                    projects=projects[:10],
                    more_projects=len(projects) > 10,
                )
            if action not in {
                "create_project",
                "new_workstream",
                "attach_workstream",
                "rebind_workstream",
            }:
                raise TaskError("invalid_init_action")
            if action == "create_project":
                if selected:
                    raise TaskError("checkout_already_registered: choose its existing project")
                if project:
                    raise TaskError(
                        "project_not_used: action=create_project creates a new project; omit "
                        "project=, or choose attach_workstream to join that project"
                    )
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
                if self._archived(ws) and not include_archived:
                    raise TaskError(
                        f"workstream_archived: workstream {ws['name']!r} is archived "
                        f"({ws['archive']['reason']}); pass include_archived=true to move it "
                        f"here as archived, or unarchive it first with {self._archive_hint(ws)}"
                    )
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
            return self._ready_init(db, selected, ws, include_inactive) | {"changed": True}

        return self._run("session.initialized", request, operation)

    def _ready_init(self, db, project, ws, include_inactive=False):
        queue = self._scoped_queue(db, ws["id"])
        hidden = [] if include_inactive else [c for c in queue if c["status"] in INACTIVE_STATUSES]
        queue = [c for c in queue if include_inactive or c["status"] not in INACTIVE_STATUSES]
        cache = {}
        return {
            "state": "ready",
            "message": (
                f"Archived workstream resumed on request ({ws['archive']['reason']}); it "
                f"stays out of discovery until unarchived with {self._archive_hint(ws)}"
                if self._archived(ws)
                else "Workstream ready"
            ),
            "project": {key: value for key, value in project.items() if key != "order_revision"},
            "workstream": ws,
            "scope_revision": ws["revision"],
            "workstream_order_revision": ws["order_revision"],
            "queue": [
                self._board_card(
                    db, self._task(db, card["id"], {}), ws["id"], full=card, cache=cache
                )
                for card in queue[:10]
            ],
            "queue_total": len(queue),
            "queue_next_offset": 10 if len(queue) > 10 else None,
            **({} if include_inactive else {"queue_hidden": self._hidden_counts(hidden)}),
            "groups": self._scope_group_ids(db, ws["id"])[:10],
            "groups_total": len(self._scope_group_ids(db, ws["id"])),
            "status": self._status_summary(db, ws["id"]),
            **({"notes": notes} if (notes := self._notes(db, project["id"], ws["id"])) else {}),
        }

    @staticmethod
    def _notes(db, project_id, workstream_id=None):
        """The nonempty project/workstream notes with their revision, time and author."""
        notes = {}
        for kind, owner_id in (("project", project_id), ("workstream", workstream_id)):
            if owner_id is None:
                continue
            row = db.execute(
                "SELECT * FROM notes WHERE owner_id=? AND kind=?", (owner_id, kind)
            ).fetchone()
            if row and row["text"]:
                notes[kind] = {
                    "text": row["text"],
                    "revision": row["revision"],
                    "updated_at": row["updated_at"],
                    "updated_by": row["updated_by"],
                }
        return notes

    def read_notes(self, project, workstream_id=None):
        """Read a project's note and optionally one of its workstreams' (viewer display)."""

        def operation(db, scope):
            selected = self._project(db, project, scope)
            if workstream_id is not None:
                self._workstream(db, workstream_id, selected["id"])
            return {"notes": self._notes(db, selected["id"], workstream_id)}

        return self._run(
            "notes.read", {"project": project, "workstream_id": workstream_id}, operation
        )

    def set_note(self, kind, target_id, expected_revision, text):
        """Replace (or with empty text clear) one project or workstream note."""
        request = dict(
            kind=kind, target_id=target_id, expected_revision=expected_revision, text=text
        )

        def operation(db, scope):
            if kind not in NOTE_KINDS:
                raise TaskError("invalid_note_kind: use project or workstream")
            if kind == "project":
                project_id = self._project(db, target_id, scope)["id"]
                workstream_id = None
            else:
                if not isinstance(target_id, str) or not target_id.strip():
                    raise TaskError("workstream_id_required: provide a workstream ID")
                ws = self._workstream(db, target_id.strip())
                project_id, workstream_id = ws["project_id"], ws["id"]
                scope["project_id"] = project_id
            if not isinstance(text, str):
                raise TaskError("invalid_note_text: provide plain text, or empty text to clear")
            if len(text) > NOTE_LIMIT:
                raise TaskError(
                    f"note_too_long: a note holds at most {NOTE_LIMIT:,} characters; this "
                    f"text has {len(text):,}. Replace outdated content instead of appending"
                )
            owner_id = workstream_id or project_id
            row = db.execute("SELECT * FROM notes WHERE owner_id=?", (owner_id,)).fetchone()
            before = dict(row) if row else None
            current_text = before["text"] if before else ""
            current_revision = before["revision"] if before else 0
            # A note that init omits (empty) is saved with expected_revision 0.
            if type(expected_revision) is not int or not (
                expected_revision == current_revision
                or (not current_text and expected_revision == 0)
            ):
                raise TaskError(
                    f"revision_conflict: expected {expected_revision}, current "
                    f"{current_revision if current_text else 0}; re-read the note with init "
                    "and reconcile your text with it before saving"
                )
            new_text = text if text.strip() else ""
            ack = {"kind": kind, "target_id": owner_id, "project_id": project_id}
            if new_text == current_text:
                return ack | {
                    "revision": current_revision,
                    "length": len(current_text),
                    "limit": NOTE_LIMIT,
                    "changed": False,
                }
            after = {
                "owner_id": owner_id,
                "kind": kind,
                "project_id": project_id,
                "workstream_id": workstream_id,
                "text": new_text,
                "revision": current_revision + 1,
                "updated_at": timestamp(),
                "updated_by": self.actor,
            }
            db.execute(
                "INSERT INTO notes (owner_id,kind,project_id,workstream_id,text,revision,"
                "updated_at,updated_by) VALUES (:owner_id,:kind,:project_id,:workstream_id,"
                ":text,:revision,:updated_at,:updated_by) ON CONFLICT(owner_id) DO UPDATE SET "
                "text=excluded.text,revision=excluded.revision,updated_at=excluded.updated_at,"
                "updated_by=excluded.updated_by",
                after,
            )
            scope.update(before=before, after=after)
            return ack | {
                "revision": after["revision"],
                "length": len(new_text),
                "limit": NOTE_LIMIT,
                "cleared": not new_text,
                "updated_at": after["updated_at"],
                "updated_by": after["updated_by"],
                "changed": True,
            }

        return self._run("note.set", request, operation)

    def archive_workstream(self, workstream_id, expected_revision, reason, archived=True):
        """Archive (or with archived=False unarchive) one workstream, with a short reason.

        Only discovery changes: tasks, memberships, order, attempts and history are kept.
        """
        request = dict(
            workstream_id=workstream_id,
            expected_revision=expected_revision,
            reason=reason,
            archived=archived,
        )

        def operation(db, scope):
            if not isinstance(workstream_id, str) or not workstream_id.strip():
                raise TaskError("workstream_id_required: provide a workstream ID")
            ws = self._workstream(db, workstream_id.strip())
            scope["project_id"] = ws["project_id"]
            if type(archived) is not bool:
                raise TaskError("invalid_archived: use true to archive or false to unarchive")
            if not isinstance(reason, str) or not reason.strip():
                raise TaskError("archive_reason_required: give a short reason")
            text = reason.strip()
            if len(text) > ARCHIVE_REASON_LIMIT:
                raise TaskError(
                    f"archive_reason_too_long: a reason holds at most {ARCHIVE_REASON_LIMIT} "
                    f"characters; this one has {len(text)}"
                )
            before = ws.get("archive")
            current = before["revision"] if before else 0
            if type(expected_revision) is not int or expected_revision != current:
                raise TaskError(
                    f"revision_conflict: expected {expected_revision}, current archive "
                    f"revision {current}; re-read the workstream (its archive.revision, or 0 "
                    "when it has none) before changing its archive state"
                )
            ack = {
                "workstream_id": ws["id"],
                "project_id": ws["project_id"],
                "name": ws["name"],
                "branch": ws["branch"],
                "checkout_path": ws["checkout_path"],
            }
            if self._archived(ws) == archived:
                return ack | {"archive": before, "changed": False}
            after = {
                "archived": archived,
                "reason": text,
                "revision": current + 1,
                "updated_at": timestamp(),
                "updated_by": self.actor,
            }
            db.execute(
                "INSERT INTO workstream_archive (workstream_id,archived,reason,revision,"
                "updated_at,updated_by) VALUES (?,?,?,?,?,?) ON CONFLICT(workstream_id) DO "
                "UPDATE SET archived=excluded.archived,reason=excluded.reason,"
                "revision=excluded.revision,updated_at=excluded.updated_at,"
                "updated_by=excluded.updated_by",
                (ws["id"], int(archived), text, after["revision"], after["updated_at"], self.actor),
            )
            scope.update(
                before={"workstream_id": ws["id"], "archive": before},
                after=ack | {"archive": after},
            )
            return ack | {"archive": after, "changed": True}

        return self._run(
            "workstream.archived" if archived is True else "workstream.unarchived",
            request,
            operation,
        )

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
                raise TaskError("checkout_not_attached: use init action=attach_workstream")
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
                raise TaskError(
                    "unknown_scope_base: start with none or a workstream ID/name, "
                    "then +/-task or group references"
                )
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
        """Add effective membership here while preserving all other workstreams.

        A group ID includes the group, and so its live members, in this scope."""
        return self._membership_change(task_id, workstream_id, expected_revision, True)

    def remove_from_workstream(self, task_id, workstream_id, expected_revision):
        """Remove only the named workstream, including inherited group membership.

        A group ID removes the group's inclusion here; a member ID excludes just that member."""
        return self._membership_change(task_id, workstream_id, expected_revision, False)

    @staticmethod
    def _group_included(db, workstream_id, group_id):
        return bool(
            db.execute(
                "SELECT 1 FROM scope_groups s WHERE s.workstream_id=? AND s.group_id=? "
                "AND NOT EXISTS (SELECT 1 FROM scope_exclusions e "
                "WHERE e.workstream_id=s.workstream_id AND e.task_id=s.group_id)",
                (workstream_id, group_id),
            ).fetchone()
        )

    def _group_scope_change(self, db, scope, before, workstream_id, adding):
        """Include or remove a shared group exactly as a +group/-group scope term did.

        This is a scope-only change, so it is allowed for completed groups as set_scope
        was; their row and revision stay untouched, while an open group's revision
        still advances."""
        group_id = before["id"]
        completed = before["status"] == "done" or self._group_complete(db, group_id)
        workstream = self._workstream(db, workstream_id)
        scope["project_id"] = workstream["project_id"]
        changed = self._group_included(db, workstream_id, group_id) != adding
        if changed:
            if adding:
                db.execute(
                    "INSERT OR IGNORE INTO scope_groups VALUES (?,?)", (workstream_id, group_id)
                )
                db.execute(
                    "DELETE FROM scope_exclusions WHERE workstream_id=? AND task_id=?",
                    (workstream_id, group_id),
                )
            else:
                db.execute(
                    "DELETE FROM scope_groups WHERE workstream_id=? AND group_id=?",
                    (workstream_id, group_id),
                )
                db.execute(
                    "INSERT OR IGNORE INTO scope_exclusions VALUES (?,?)",
                    (workstream_id, group_id),
                )
            self._touch_workstream(db, workstream_id)
            if not completed:
                db.execute(
                    "UPDATE tasks SET revision=revision+1,updated_at=? WHERE id=?",
                    (timestamp(), group_id),
                )
        return changed

    def _membership_change(self, task_id, workstream_id, expected_revision, adding):
        request = dict(
            task_id=task_id, workstream_id=workstream_id, expected_revision=expected_revision
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            if before["object_type"] == "group":
                changed = self._group_scope_change(db, scope, before, workstream_id, adding)
                after = self._task(db, task_id, {})
                scope.update(before=before, after=after)
                return self._task_ack(db, after) | {
                    "changed": changed,
                    "included": self._group_included(db, workstream_id, task_id),
                    "workstream_id": workstream_id,
                    "workstream_revision": self._workstream(db, workstream_id)["revision"],
                    "workstream_revisions": {
                        workstream_id: self._workstream(db, workstream_id)["revision"]
                    },
                }
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
            if picks := Store._live_picks(db, task["id"], workstream_id):
                card["picked"] = picks[0]
        return card

    @staticmethod
    def _standing(card):
        """Where a task stands for the user, from its card (see STANDINGS).

        The card's results and picks are those in view: one workstream's, or every
        workstream's for a project-wide card. The viewer sorts its board by this and
        counts each workstream's Needs input with it, so the two always agree.
          decision  held by any unresolved item (briefs, revised at sign-off, ideas)
          signoff   a current-spec result passed review (or was human-reviewed)
          progress  a result is with the agents (in review or being fixed), or an
                    agent picked the task up recently and has recorded nothing since
          open      no result and no recent pick, ready or blocked
        Closed tasks stand as their status; groups have no standing ("group").
        """
        if card["object_type"] == "group":
            return "group"
        view = card.get("view")
        for status in (card["status"], view):
            if status in INACTIVE_STATUSES:
                return status
        if card["unresolved_count"] or view == "unresolved_items":
            return "decision"
        counts = card.get("attempt_counts") or card.get("aggregate_attempt_counts") or {}
        if counts.get("passed") or counts.get("human_review") or view == "signoff":
            return "signoff"
        if counts.get("review") or counts.get("rework") or view == "review" or card.get("picked"):
            return "progress"
        return "open"

    @staticmethod
    def _include_groups(include):
        """Validate optional card detail groups; order and duplicates are irrelevant."""
        if include is None:
            return ()
        choices = ", ".join(CARD_INCLUDE_GROUPS)
        if not isinstance(include, list) or any(not isinstance(g, str) for g in include):
            raise TaskError(f"invalid_include: pass a list of group names from {choices}")
        unknown = [g for g in include if g not in CARD_INCLUDE_GROUPS]
        if unknown:
            raise TaskError(
                f"invalid_include: unknown group {', '.join(map(repr, unknown))}; "
                f"choose from {choices}"
            )
        return tuple(g for g in CARD_INCLUDE_GROUPS if g in include)

    @staticmethod
    def _validate_include_inactive(include_inactive):
        if type(include_inactive) is not bool:
            raise TaskError("invalid_include_inactive: use true or false")

    @staticmethod
    def _validate_include_archived(include_archived):
        if type(include_archived) is not bool:
            raise TaskError("invalid_include_archived: use true or false")

    @staticmethod
    def _card_view(db, task, full, workstream_id=None):
        """Return (internal view, slim state word) for one task or group."""
        if task["object_type"] == "group":
            return "group", "group"
        if workstream_id:
            view = full["view"]
        else:
            # Without a workstream, review/sign-off reflect current results anywhere.
            view = Store._status_view(task, Store._gate_reasons(db, task))
        state = STATE_WORDS.get(view, view)
        counts = full.get("attempt_counts") or full.get("aggregate_attempt_counts") or {}
        if state == "ready" and (task["status"] == "rework" or counts.get("rework")):
            state = "rework"
        return view, state

    @staticmethod
    def _validate_state_filter(state):
        """Reject typos such as 'sign-off' instead of silently matching nothing."""
        if state is None or state == "":
            return
        if not isinstance(state, str) or state not in STATE_FILTERS:
            raise TaskError(
                f"invalid_state: unknown state filter {state!r}; "
                f"use one of {', '.join(STATE_FILTERS)}"
            )

    @staticmethod
    def _state_matches(requested, view, state):
        """Old view names and slim state words both filter; ready includes rework."""
        return requested in {view, state}

    @staticmethod
    def _rejection_waiting(db, task):
        """A review/sign-off rejection with no newer recorded result for this task."""
        rejection = task["latest_rejection"]
        if not rejection or task["object_type"] != "task" or task["status"] in INACTIVE_STATUSES:
            return False
        return not db.execute(
            "SELECT 1 FROM events WHERE task_id=? AND action='attempt.recorded' "
            "AND outcome='ok' AND sequence>? LIMIT 1",
            (task["id"], rejection["decision_ref"]),
        ).fetchone()

    @staticmethod
    def _positions(db, workstream_id, cache):
        if workstream_id not in cache:
            cache[workstream_id] = {
                identity: index
                for index, identity in enumerate(Store._ordered_scope_ids(db, workstream_id), 1)
            }
        return cache[workstream_id]

    @staticmethod
    def _board_card(db, task, workstream_id=None, include=(), full=None, cache=None):
        """Slim card: identity, one state word and gate/attention counts.

        Fields at their empty default (no summary, blockers, questions, concerns or
        waiting rejection) are omitted; closed tasks have no blockers. Include groups
        restore the dropped detail.
        """
        cache = {} if cache is None else cache
        full = full if full is not None else Store._card(db, task, workstream_id)
        view, state = Store._card_view(db, task, full, workstream_id)
        card = {"id": task["id"], "title": task["title"]}
        if task["summary"] is not None:
            card["summary"] = task["summary"]
        card.update(state=state, revision=task["revision"])
        if workstream_id and task["object_type"] == "task":
            position = Store._positions(db, workstream_id, cache).get(task["id"])
            if position is not None:
                card["position"] = position
        if task["object_type"] == "group":
            card.update(
                progress=full["progress"],
                complete=full["complete"],
                project_count=full["project_count"],
            )
        if task["status"] not in INACTIVE_STATUSES:
            blockers = [
                p["id"] for p in Store._prerequisite_references(db, task["id"]) if p["blocking"]
            ]
            if blockers:
                card["blockers"] = blockers
        # Open questions stay visible on closed cards: deferred work keeps its questions.
        if task["unresolved_items"]:
            card["question_count"] = len(task["unresolved_items"])
        if full.get("concern_count"):
            card["concern_count"] = full["concern_count"]
        if Store._rejection_waiting(db, task):
            card["rejected"] = True
        for name in include:
            card.update(Store._card_detail(db, task, full, workstream_id, name, view, cache))
        return card

    @staticmethod
    def _card_detail(db, task, full, workstream_id, name, view, cache):
        """One include group; together with the slim card they cover the old card."""
        is_task = task["object_type"] == "task"
        if name == "blockers":
            return {
                "prerequisites": Store._prerequisite_references(db, task["id"]),
                "gate_diagnostics": full["gate_diagnostics"],
            }
        rows = Store._current_attempt_rows(db, task, workstream_id) if is_task else []
        if name == "attempt":
            reference = full.get(
                "attempt_reference", Store._attempt_summary(rows[0]) if rows else None
            )
            return {
                "attempt": reference,
                "attempt_counts": {
                    state: sum(r["state"] == state for r in rows)
                    for state in ("review", "passed", "human_review", "rework")
                },
                "alternative_attempt_count": max(0, len(rows) - 1),
                "attempt_scope": workstream_id or "cross_workstream",
                "selected_attempt_id": task["selected_attempt_id"],
                "latest_rejection": full["latest_rejection"],
                **({"picked": full["picked"]} if "picked" in full else {}),
            }
        if name == "concerns":
            concerned = [row for row in rows if row["concern_count"]]
            return {
                "concern_count": sum(row["concern_count"] for row in concerned),
                "concerns": Store._concern_window(db, concerned[:3]),
                "concern_attempt_total": len(concerned),
                "concern_attempt_references": full.get("concern_attempt_references", []),
                "concern_attempts_has_more": len(concerned) > 3,
            }
        if name == "workstreams":
            if is_task:
                memberships = [
                    {
                        "id": identity,
                        "position": Store._positions(db, identity, cache).get(task["id"]),
                    }
                    for identity in Store._membership_ids(db, task)
                ]
            else:
                # Groups are referenced by scope, not ordered as members.
                memberships = [
                    {"id": row["workstream_id"], "position": None}
                    for row in db.execute(
                        "SELECT workstream_id FROM scope_groups WHERE group_id=? "
                        "ORDER BY workstream_id",
                        (task["id"],),
                    )
                ]
            detail = {"workstreams": memberships, "adopted": bool(is_task and memberships)}
            if "in_scope" in full:
                detail["in_scope"] = full["in_scope"]
            return detail
        detail = {
            key: task[key]
            for key in (
                "project_id",
                "object_type",
                "status",
                "spec_revision",
                "summary_spec_revision",
                "order_key",
                "parent_group_id",
            )
        }
        detail.update(
            summary_stale=Store._summary_stale(task),
            specification_complete=False,
            view=view,
        )
        if workstream_id:
            detail["workstream_id"] = workstream_id
        if not is_task:
            detail.update(project_id=None, origin_project_id=task["project_id"])
        return detail

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

    def read_tasks(
        self, ids, specification=False, workstream_id=None, attempt_ids=None, include=None
    ):
        """Bounded MCP projection; get_tasks retains full internal/viewer detail."""
        request = dict(
            ids=ids,
            specification=specification,
            workstream_id=workstream_id,
            attempt_ids=attempt_ids,
            include=include,
        )

        def operation(db, scope):
            if not isinstance(ids, list) or not 1 <= len(ids) <= 20:
                raise TaskError("invalid_ids: request 1–20 IDs")
            groups = self._include_groups(include)
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
            cache = {}
            if specification:
                items = []
                for task in tasks:
                    item = self._specification(db, task, workstream_id)
                    if groups:
                        full = self._card(db, task, workstream_id)
                        view, _ = self._card_view(db, task, full, workstream_id)
                        for name in groups:
                            # The specification already carries most groups; keep its fields.
                            detail = self._card_detail(
                                db, task, full, workstream_id, name, view, cache
                            )
                            for key, value in detail.items():
                                item.setdefault(key, value)
                    items.append(item)
            else:
                items = [
                    self._board_card(db, task, workstream_id, groups, cache=cache) for task in tasks
                ]
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
            # Only a reference: a group read by its ID gives the body and criteria.
            group = db.execute(
                "SELECT id,title,summary,spec_revision,summary_spec_revision FROM tasks WHERE id=?",
                (task["parent_group_id"],),
            ).fetchone()
            group = dict(group)
            task["parent_group"] = {
                "id": group["id"],
                "title": group["title"],
                "summary": group["summary"],
                "summary_stale": Store._summary_stale(group),
            }
        task["blocked_by"] = [
            r["blocked_by_id"]
            for r in db.execute(
                "SELECT blocked_by_id FROM prerequisites WHERE task_id=? ORDER BY blocked_by_id",
                (task["id"],),
            )
        ]
        task["prerequisites"] = Store._prerequisite_references(db, task["id"])
        if history and task["object_type"] == "task":
            if picks := Store._live_picks(db, task["id"]):
                task["picks"] = picks
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

    def get_tasks(self, ids, workstream_id=None, standing=False):
        """Full internal/viewer detail. standing=True (the viewer's option) adds each task's
        standing for the results in view: the named workstream's, or every workstream's."""

        def operation(db, scope):
            if not isinstance(ids, list) or not 1 <= len(ids) <= 20:
                raise TaskError("invalid_ids: request 1–20 IDs")
            if workstream_id is not None:
                self._workstream(db, workstream_id)
            tasks = []
            for task_id in ids:
                task = self._task(db, task_id, {})
                detail = self._details(db, task)
                if standing:
                    detail["standing"] = self._standing(self._card(db, task, workstream_id))
                tasks.append(detail)
            if len({t["project_id"] for t in tasks}) == 1:
                scope["project_id"] = tasks[0]["project_id"]
            if len(ids) == 1:
                scope["task_id"] = ids[0]
            return {"items": tasks}

        request = {"ids": ids}
        if workstream_id is not None:
            request["workstream_id"] = workstream_id
        if standing:
            request["standing"] = True
        return self._run("tasks.read", request, operation)

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

    def resolve_prefix(self, kind, prefix, match="prefix"):
        """Resolve a legacy viewer prefix or an explicitly marked exact public group ID."""
        request = dict(kind=kind, prefix=prefix, match=match)

        def operation(db, scope):
            sources = {
                "workstream": ("workstreams", "wst_", ""),
                "group": ("tasks", "tsk_", "object_type='group' AND "),
            }
            if not isinstance(kind, str) or kind not in sources:
                raise TaskError("invalid_kind: use workstream or group")
            if match not in ("prefix", "public_id"):
                raise TaskError("invalid_match: use prefix or public_id")
            if match == "public_id":
                if (
                    kind != "group"
                    or not isinstance(prefix, str)
                    or len(prefix) > LEGACY_PUBLIC_ID_LIMIT
                    or not re.fullmatch(PUBLIC_ID_PATTERN, prefix)
                ):
                    raise TaskError("invalid_public_id: use a complete public group ID")
                row = db.execute(
                    "SELECT id,project_id FROM tasks WHERE id=? AND object_type='group'", (prefix,)
                ).fetchone()
                return {"items": [dict(row)] if row is not None else []}
            if (
                not isinstance(prefix, str)
                or not 1 <= len(prefix) <= 32
                or any(c not in "0123456789abcdef" for c in prefix)
            ):
                raise TaskError("invalid_prefix: use 1-32 lowercase hexadecimal characters")
            table, start, condition = sources[kind]
            # A primary-key range scan: IDs are a type prefix followed by lowercase hex.
            low = start + prefix
            rows = db.execute(
                f"SELECT id, project_id FROM {table} WHERE {condition}id >= ? AND id < ? "
                "ORDER BY id LIMIT 2",
                (low, low + "g"),
            ).fetchall()
            return {"items": [dict(row) for row in rows]}

        return self._run("prefixes.read", request, operation)

    @staticmethod
    def _public_task_id(db, title, public_id=None):
        """Choose once under the writer lock; explicit names never silently change."""
        if public_id is not None:
            if isinstance(public_id, str) and len(public_id) > PUBLIC_ID_LIMIT:
                raise TaskError(
                    f"invalid_public_id: an ID has at most {PUBLIC_ID_LIMIT} characters; "
                    f"this one has {len(public_id)}. Choose a shorter ID"
                )
            if not isinstance(public_id, str) or not re.fullmatch(PUBLIC_ID_PATTERN, public_id):
                raise TaskError(
                    f"invalid_public_id: use up to {PUBLIC_ID_LIMIT} lowercase letters, digits "
                    "and single hyphens, beginning with a letter (for example readable-task-ids). "
                    "Choose another ID"
                )
            if db.execute("SELECT 1 FROM tasks WHERE id=?", (public_id,)).fetchone():
                raise TaskError(
                    f"public_id_conflict: {public_id} is already reserved; choose a different "
                    "descriptive public_id and retry (closed task IDs cannot be reused)"
                )
            return public_id
        return Store._free_task_slug(db, title)

    @staticmethod
    def _free_task_slug(db, title):
        """The title's short slug, with a numeric suffix if that ID is already reserved."""
        base = short_task_slug(title)
        candidate, suffix = base, 2
        while db.execute("SELECT 1 FROM tasks WHERE id=?", (candidate,)).fetchone():
            ending = f"-{suffix}"
            candidate = base[: PUBLIC_ID_LIMIT - len(ending)].rstrip("-") + ending
            suffix += 1
        return candidate

    def suggest_task_id(self, title):
        """The ID an omitted public_id would get now (viewer prefill; no audit, no write)."""
        if not isinstance(title, str):
            raise TaskError("invalid_title: the title must be text")
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            public_id = self._free_task_slug(db, title[:IDEA_TITLE_LIMIT])
        return {"public_id": public_id, "limit": PUBLIC_ID_LIMIT}

    @staticmethod
    def _register_task_identity(db, public_id):
        db.execute("INSERT INTO task_identities VALUES (?,?)", (uuid4().hex, public_id))

    def create_group(
        self,
        workstream_id,
        title,
        body="",
        acceptance_criteria="",
        summary=None,
        project=None,
        source="unknown",
        user_request="",
        public_id=None,
    ):
        request = dict(
            public_id=public_id,
            workstream_id=workstream_id,
            title=title,
            body=body,
            acceptance_criteria=acceptance_criteria,
            summary=summary,
        )
        if project is not None:
            request.update(project=project, source=source, user_request=user_request)

        def operation(db, scope):
            if project is not None:
                if workstream_id is None:
                    raise TaskError(
                        "workstream_required: a group is created into a workstream's scope"
                    )
                self._workstream(db, workstream_id, self._project(db, project, scope)["id"])
            ws = self._workstream(db, workstream_id)
            scope["project_id"] = ws["project_id"]
            if not isinstance(source, str) or source not in {"agent", "user", "unknown"}:
                raise TaskError("invalid_source: agent, user or unknown")
            if not isinstance(user_request, str):
                raise TaskError("invalid_user_request: request must be text")
            if (
                not isinstance(title, str)
                or not title.strip()
                or not isinstance(body, str)
                or not isinstance(acceptance_criteria, str)
            ):
                raise TaskError("invalid_specification")
            for field, value in (("body", body), ("acceptance_criteria", acceptance_criteria)):
                self._validate_specification_text(value, field, creating=True)
            now = timestamp()
            self._validate_summary(summary)
            group_id = self._public_task_id(db, title, public_id)
            db.execute(
                """INSERT INTO tasks
                (id,project_id,title,body,acceptance_criteria,status,object_type,
                spec_revision,accepted_spec_revision,acceptance_note,unresolved_json,
                parent_group_id,order_key,selected_attempt_id,revision,created_at,updated_at)
                VALUES (?,NULL,?,?,?,'open','group',1,NULL,'','[]',NULL,0,NULL,1,?,?)""",
                (group_id, title.strip(), body, acceptance_criteria, now, now),
            )
            self._register_task_identity(db, group_id)
            db.execute(
                "UPDATE tasks SET summary=?,summary_spec_revision=?,source=?,user_request=? "
                "WHERE id=?",
                (summary, 1 if summary is not None else None, source, user_request, group_id),
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
            member = self._task(db, task_id, {})
            self._revision(member, expected_task_revision)
            self._validate_group_member(db, group, member)
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
    def _validate_group_member(db, group, member):
        if group["object_type"] != "group":
            raise TaskError("invalid_group")
        Store._require_mutable(db, group, concrete=False)
        if member["object_type"] != "task" or member["parent_group_id"]:
            raise TaskError("invalid_member: nested groups or reassignment are unsupported")
        Store._require_mutable(db, member)
        if Store._would_cycle(db, group["id"], member["id"]):
            raise TaskError("prerequisite_cycle")

    @staticmethod
    def _validate_specification_text(value, field, *, creating=False):
        """Reject strong prose-separator signals, without decoding any input.

        Code spans/blocks are examples, not prose. Look within actual lines so
        ordinary multiline specifications and technical escape mentions stay
        untouched. Ambiguous escapes deliberately pass this conservative guard.
        """
        # Strip Markdown containers before recognizing fences. Be permissive
        # about indentation: an ambiguous example should pass, rather than risk
        # rejecting quoted or list-nested code. Closing runs may be longer than
        # their opener, but must use the same character and contain only spaces.
        prose_lines = []
        fence = None
        for line in value.splitlines():
            content = re.sub(r"^(?:[ \t]*>[ \t]?|[ \t]*(?:[-*+]|\d+[.)])[ \t]+)*", "", line)
            content = content.lstrip(" \t")
            marker = re.match(r"(`{3,}|~{3,})(.*)$", content)
            if fence is not None:
                if (
                    marker
                    and marker[1][0] == fence[0]
                    and len(marker[1]) >= len(fence)
                    and not marker[2].strip()
                ):
                    fence = None
                prose_lines.append("")
            elif marker and (marker[1][0] == "~" or "`" not in marker[2]):
                fence = marker[1]
                prose_lines.append("")
            else:
                prose_lines.append(line)
        prose = "\n".join(prose_lines)
        prose = re.sub(r"(`+)(?!`).*?(?<!`)\1(?!`)", " ", prose, flags=re.DOTALL)
        escape = r"\\+n"
        for line in prose.splitlines():
            if len(re.findall(escape, line)) < 2:
                continue
            # An explicitly introduced regex can intentionally contain the same
            # sentence/list shapes as damaged prose. Treat that context as
            # ambiguous instead of rejecting an unquoted technical example.
            introduction = re.split(escape, line, maxsplit=1)[0]
            if re.search(r"\b(?:regex(?:p)?|regular expression)\b", introduction, re.IGNORECASE):
                continue
            # Two escaped bullet boundaries or a blank paragraph between prose
            # are strong signals. Sentence boundaries cover single-line prose
            # whose author escaped every line, but require repeated evidence.
            bullets = re.findall(escape + r"[ \t]*(?:[-*+] |\d+[.)] )", line)
            paragraphs = re.split(escape + r"[ \t]*" + escape, line)
            paragraph_signal = any(
                len(re.findall(r"\b[A-Za-z]+\b", left)) >= 3
                and len(re.findall(r"\b[A-Za-z]+\b", right)) >= 3
                and re.search(r"[.!?]$", left.rstrip())
                and re.match(r"[ \t]*[A-Z][a-z]+\b", right)
                for left, right in zip(paragraphs, paragraphs[1:], strict=False)
            )
            sentences = re.findall(r"[.!?]" + escape + r"[ \t]*[A-Z][a-z]+ ", line)
            if len(bullets) >= 2 or paragraph_signal or len(sentences) >= 2:
                outcome = "No task was created" if creating else "No changes were saved"
                raise TaskError(
                    f"likely_double_escaped_specification: {field} contains likely "
                    "double-escaped line breaks. Submit actual line breaks through your "
                    f"JSON serializer without pre-escaping the text. {outcome}."
                )

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
        public_id=None,
        specification_creating=True,
        specification_prefix="",
    ):
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
            raise TaskError("invalid_specification: title and description required")
        if not isinstance(acceptance_criteria, str):
            raise TaskError("invalid_specification: acceptance criteria must be text")
        for field, value in (("body", body), ("acceptance_criteria", acceptance_criteria)):
            Store._validate_specification_text(
                value, specification_prefix + field, creating=specification_creating
            )
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
        task_id = Store._public_task_id(db, title, public_id)
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
        Store._register_task_identity(db, task_id)
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
        kind="task",
        public_id=None,
    ):
        if kind == "group":
            if group_id is not None or group_expected_revision is not None:
                raise TaskError("invalid_group: nested groups are not supported")
            return self.create_group(
                workstream_id,
                title,
                body,
                acceptance_criteria,
                summary,
                project=project,
                source=source,
                user_request=user_request,
                public_id=public_id,
            )
        if kind != "task":
            raise TaskError("invalid_kind: use task or group")
        request = dict(
            public_id=public_id,
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
                public_id,
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

    def capture_idea(self, project, text, note="", public_id=None):
        """A quick idea from the browser: one inbox task held by an Idea to process item.

        The task and its item are written together at revision 1, so no state exists in
        which the idea is buildable as is. Reached only through the viewer, not MCP.
        public_id follows create_task's rules; omitted, the title's short slug is used.
        """
        request = dict(project=project, text=text, note=note)
        if public_id is not None:
            request["public_id"] = public_id

        def operation(db, scope):
            project_id = self._project(db, project, scope)["id"]
            if not isinstance(text, str) or not isinstance(note, str):
                raise TaskError("invalid_idea: the idea title and details must be text")
            title, details = text.strip(), note.strip()
            if not title or len(title) > IDEA_TITLE_LIMIT or "\n" in title or "\r" in title:
                raise TaskError(f"invalid_idea: one line of up to {IDEA_TITLE_LIMIT} characters")
            if len(note) > IDEA_NOTE_LIMIT:
                raise TaskError(f"invalid_idea: details of up to {IDEA_NOTE_LIMIT} characters")
            task_id = self._insert_task(
                db,
                project_id,
                title,
                note if details else "",
                "",
                source="user",
                user_request=text + ("\n\n" + note if details else ""),
                public_id=public_id,
            )
            item = [{"id": _id("unr_"), "text": IDEA_ITEM}]
            db.execute("UPDATE tasks SET unresolved_json=? WHERE id=?", (_json(item), task_id))
            task = self._details(db, self._task(db, task_id, scope))
            scope["after"] = task
            return self._task_ack(db, task) | {
                "changed": True,
                "title": task["title"],
                "specification_etag": self._specification_etag(task),
            }

        return self._run("task.idea_captured", request, operation)

    def update_task(
        self,
        task_id,
        expected_revision,
        changes=None,
        specification_etag=None,
        group_id=None,
        group_expected_revision=None,
    ):
        """Edit a task; group_id also attaches it to that shared group atomically."""
        changes = {} if changes is None else changes
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            changes=changes,
            specification_etag=specification_etag,
        )
        if group_id is not None:
            request.update(group_id=group_id, group_expected_revision=group_expected_revision)

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            group = None
            if group_id is not None:
                group = self._task(db, group_id, {})
                if group_expected_revision is None:
                    raise TaskError("group_revision_required: use the group's last revision")
                self._revision(group, group_expected_revision)
                if before["parent_group_id"] == group_id:
                    group = None  # Already a member: membership is unchanged.
                else:
                    self._validate_group_member(db, group, before)
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
            for field in ("body", "acceptance_criteria"):
                if field in changes:
                    self._validate_specification_text(changes[field], field)
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
            if group is not None:
                after["parent_group_id"] = group_id
                group = {**group, "revision": group["revision"] + 1, "updated_at": timestamp()}
                self._save_task(db, group)
            changed = spec_changed or summary_changed or group is not None
            if changed:
                after.update(
                    revision=before["revision"] + 1,
                    updated_at=timestamp(),
                )
                self._save_task(db, after)
            scope.update(before=before, after=after)
            membership = {}
            if group_id is not None:
                membership = {
                    "group_id": group_id,
                    "group_revision": self._task(db, group_id, {})["revision"],
                    "group_changed": group is not None,
                }
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
                | membership
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
            if handling not in {"active", "user"}:
                raise TaskError("invalid_handling: use active or user")
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
            if handling not in {"active", "user"}:
                raise TaskError("invalid_handling: use active or user")
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
                "INSERT OR IGNORE INTO scope_groups "
                "SELECT workstream_id,? FROM scope_members WHERE task_id=?",
                (task_id, task_id),
            )
            for row in db.execute(
                "SELECT workstream_id FROM scope_members WHERE task_id=?", (task_id,)
            ).fetchall():
                self._touch_workstream(db, row["workstream_id"])
            children = []
            for index, member in enumerate(members):
                child_id = self._insert_task(
                    db,
                    before["project_id"],
                    member["title"],
                    member.get("body", ""),
                    member.get("acceptance_criteria", ""),
                    task_id,
                    public_id=member.get("public_id"),
                    specification_creating=False,
                    specification_prefix=f"members[{index}].",
                )
                self._copy_prerequisites(db, child_id, task_id)
                children.append(child_id)
            db.execute("DELETE FROM prerequisites WHERE task_id=?", (task_id,))
            scope.update(before=before, after={"group": after, "members": children})
            return self._details(db, after)

        return self._run("task.decomposed", request, operation)

    @staticmethod
    def _order_revision(db, workstream_id):
        return Store._workstream(db, workstream_id)["order_revision"]

    @staticmethod
    def _ordered_scope_ids(db, workstream_id):
        """Read local order, with baseline append for an in-transaction new inclusion."""
        effective = set(Store._scope_ids(db, workstream_id))
        retained = [
            row["task_id"]
            for row in db.execute(
                "SELECT task_id FROM workstream_task_order WHERE workstream_id=? "
                "ORDER BY order_key,task_id",
                (workstream_id,),
            )
            if row["task_id"] in effective
        ]
        added = effective - set(retained)
        baseline = [
            row["id"]
            for row in db.execute(
                "SELECT id FROM tasks WHERE project_id=(SELECT project_id FROM workstreams "
                "WHERE id=?) AND object_type='task' ORDER BY order_key,id",
                (workstream_id,),
            )
            if row["id"] in added
        ]
        return retained + baseline

    @staticmethod
    def _sync_orders(db, advance=True):
        """Reconcile derived members without snapshotting scopes or changing retained order."""
        for ws in db.execute("SELECT id FROM workstreams ORDER BY id").fetchall():
            identity = ws["id"]
            current = {
                row["task_id"]: row["order_key"]
                for row in db.execute(
                    "SELECT task_id,order_key FROM workstream_task_order WHERE workstream_id=?",
                    (identity,),
                )
            }
            ordered = Store._ordered_scope_ids(db, identity)
            effective = set(ordered)
            removed = current.keys() - effective
            added = effective - current.keys()
            if not removed and not added:
                continue
            db.executemany(
                "DELETE FROM workstream_task_order WHERE workstream_id=? AND task_id=?",
                [(identity, task_id) for task_id in sorted(removed)],
            )
            last = max(current.values(), default=0)
            for task_id in ordered:
                if task_id in added:
                    last += 1
                    db.execute(
                        "INSERT INTO workstream_task_order VALUES (?,?,?)",
                        (identity, task_id, last),
                    )
            if advance:
                db.execute(
                    "UPDATE workstreams SET order_revision=order_revision+1 WHERE id=?", (identity,)
                )

    def reorder_tasks(self, workstream_id, task_ids, expected_order_revision):
        """Set one workstream's ordered prefix; retain unlisted members in their old order."""
        request = dict(
            workstream_id=workstream_id,
            task_ids=task_ids,
            expected_order_revision=expected_order_revision,
        )

        def operation(db, scope):
            ws = self._workstream(db, workstream_id)
            scope["project_id"] = ws["project_id"]
            revision = ws["order_revision"]
            if type(expected_order_revision) is not int or expected_order_revision != revision:
                raise TaskError("revision_conflict: re-read this workstream's order revision")
            if not isinstance(task_ids, list) or any(
                not isinstance(identity, str) or not identity.strip() for identity in task_ids
            ):
                raise TaskError("invalid_order: task_ids must be a list of task IDs")
            if len(set(task_ids)) != len(task_ids):
                raise TaskError("invalid_order: duplicate task IDs")
            old_ids = self._ordered_scope_ids(db, workstream_id)
            supplied = set(task_ids)
            if supplied - set(old_ids):
                raise TaskError(
                    "invalid_order: every ID must be an included concrete task in this workstream"
                )
            new_ids = task_ids + [identity for identity in old_ids if identity not in supplied]
            changed = new_ids != old_ids
            if changed:
                db.executemany(
                    "UPDATE workstream_task_order SET order_key=? "
                    "WHERE workstream_id=? AND task_id=?",
                    [(index, workstream_id, identity) for index, identity in enumerate(new_ids, 1)],
                )
                db.execute(
                    "UPDATE workstreams SET order_revision=order_revision+1 WHERE id=?",
                    (workstream_id,),
                )
            ack = {
                "project_id": ws["project_id"],
                "workstream_id": workstream_id,
                "workstream_order_revision": revision + int(changed),
                "changed": changed,
                "supplied_count": len(task_ids),
                "total": len(old_ids),
            }
            scope.update(
                before={
                    "workstream_id": workstream_id,
                    "workstream_order_revision": revision,
                    "total": len(old_ids),
                },
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

    @staticmethod
    def _pick_cutoff():
        """Picks at or after this time are recent enough to count as work in progress."""
        return (
            (datetime.now(UTC) - PICK_TTL).isoformat(timespec="microseconds").replace("+00:00", "Z")
        )

    @staticmethod
    def _live_picks(db, task_id, workstream_id=None):
        """Recent picks of a task with no result or review in that workstream since."""
        rows = db.execute(
            "SELECT p.workstream_id, p.action, p.picked_at FROM task_picks p "
            "WHERE p.task_id=? AND p.picked_at>=? "
            + ("AND p.workstream_id=? " if workstream_id else "")
            + "AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.task_id=p.task_id "
            "AND a.workstream_id=p.workstream_id AND a.updated_at>=p.picked_at) "
            "ORDER BY p.picked_at DESC, p.workstream_id",
            (task_id, Store._pick_cutoff()) + ((workstream_id,) if workstream_id else ()),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_next_action(self, workstream_id, include_archived=False):
        """One autonomous action in workstream order; selected proof is local/current only."""

        def operation(db, scope):
            self._validate_include_archived(include_archived)
            ws = self._workstream(db, workstream_id)
            scope["project_id"] = ws["project_id"]
            ids = self._ordered_scope_ids(db, workstream_id)
            result = {
                "action": None,
                "task": None,
                "attempt": None,
                "workstream_order_revision": ws["order_revision"],
                "diagnostics": {"scope_empty": not ids, "scoped": len(ids)},
                **({"archive": ws["archive"]} if self._archived(ws) else {}),
            }
            if self._archived(ws) and not include_archived:
                # An archived workstream offers no action unless explicitly included.
                return result | {
                    "diagnostics": result["diagnostics"] | {"workstream_archived": True},
                    "message": (
                        f"Workstream {ws['name']!r} is archived ({ws['archive']['reason']}); "
                        "no action is selected. Pass include_archived=true to select one "
                        f"anyway, or unarchive it with {self._archive_hint(ws)}"
                    ),
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
            for identity in ids:
                task = self._task(db, identity, {})
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
                        action = "review" if pending else "implement"
                        # Information only: nothing skips or locks a picked task.
                        db.execute(
                            "DELETE FROM task_picks WHERE picked_at<?", (self._pick_cutoff(),)
                        )
                        db.execute(
                            "INSERT OR REPLACE INTO task_picks "
                            "(task_id,workstream_id,action,picked_at) VALUES (?,?,?,?)",
                            (task["id"], workstream_id, action, timestamp()),
                        )
                        return result | {"action": action, "task": selected, "attempt": proof}
                for reason in reasons:
                    counts[reason] += 1
            return result | {"diagnostics": result["diagnostics"] | counts}

        return self._run(
            "task.next_action_read",
            {"workstream_id": workstream_id, "include_archived": include_archived},
            operation,
        )

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
                raise TaskError(
                    "durable_artifacts_required: a non-empty list of {kind, reference} where "
                    "kind is artifact or commit and reference is a non-empty path, URL or hash"
                )
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
            # An explicit user approval may accept a result awaiting independent review.
            unreviewed = decision == "approve" and row is not None and row["state"] == "review"
            if (
                not row
                or (row["state"] not in {"passed", "human_review"} and not unreviewed)
                or row["spec_revision"] != before["spec_revision"]
            ):
                raise TaskError(
                    "review_required: select a current-spec passed or human-reviewed attempt "
                    "(approve alone also accepts one awaiting independent review)"
                )
            if unreviewed and (not reasons or not reasons.strip()):
                raise TaskError(
                    "reasons_required: approving without independent review needs reasons "
                    "stating the user's explicit approval"
                )
            attempt = dict(row)
            if (
                type(expected_attempt_revision) is not int
                or attempt["revision"] != expected_attempt_revision
            ):
                raise TaskError("revision_conflict: re-read the selected attempt")
            after = {**before, "revision": before["revision"] + 1, "updated_at": timestamp()}
            unresolved_id = open_prerequisites = None
            if decision == "approve":
                if before["unresolved_items"]:
                    raise TaskError(
                        "task_not_ready_for_signoff: answer the open questions first, "
                        "or choose rework, revise or drop"
                    )
                # The user judges whether an unsatisfied prerequisite affects the verdict;
                # the decision records which ones were still open.
                open_prerequisites = [
                    {k: ref[k] for k in ("id", "title", "milestone", "state")}
                    for ref in self._prerequisite_references(db, before["id"])
                    if ref["blocking"]
                ]
                after.update(status="done", selected_attempt_id=attempt_id)
                if unreviewed:
                    # The user's explicit approval is the review of record for this result.
                    db.execute(
                        "UPDATE attempts SET state='human_review', human_review_note=?, "
                        "revision=revision+1, updated_at=? WHERE id=?",
                        (reasons, after["updated_at"], attempt_id),
                    )
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
                "resulting_attempt_revision": attempt["revision"]
                + (decision == "rework" or unreviewed),
                "unresolved_id": unresolved_id,
                "independent_review": row["state"] == "passed",
            }
            if open_prerequisites is not None:
                recorded["open_prerequisites"] = open_prerequisites
            self._save_task(db, after)
            scope.update(before=before, after={**after, "signoff_decision": recorded})
            ack = self._task_ack(db, after) | {
                "attempt_id": attempt_id,
                "attempt_revision": recorded["resulting_attempt_revision"],
                "attempt_state": "rework"
                if decision == "rework"
                else "human_review"
                if unreviewed
                else attempt["state"],
                "independent_review": row["state"] == "passed",
                "selected_attempt_id": after["selected_attempt_id"],
                "decision": decision,
                "unresolved_id": unresolved_id,
            }
            if open_prerequisites is not None:
                ack["open_prerequisites"] = open_prerequisites
            return ack

        return self._run("task.signoff", request, operation)

    @staticmethod
    def _hidden_counts(tasks):
        counts = {status: 0 for status in INACTIVE_STATUSES}
        for task in tasks:
            counts[task["status"]] += 1
        return {status: count for status, count in counts.items() if count}

    def list_tasks(
        self,
        project=None,
        workstream_id=None,
        state=None,
        limit=20,
        offset=0,
        include_inactive=False,
        include=None,
        full_cards=False,
        group_id=None,
    ):
        """Board of active work; full_cards is the viewer's internal unabridged projection.

        state="group" lists shared groups instead of tasks (those in the workstream's scope,
        or related to the project, or all); group_id lists that group's members.
        """
        request = dict(
            project=project,
            workstream_id=workstream_id,
            state=state,
            limit=limit,
            offset=offset,
            include_inactive=include_inactive,
            include=include,
        )
        if full_cards:
            request["full_cards"] = True
        if group_id is not None:
            request["group_id"] = group_id

        def operation(db, scope):
            self._page(limit, offset)
            self._validate_include_inactive(include_inactive)
            self._validate_state_filter(state)
            groups = self._include_groups(include)
            if group_id is not None:
                group = self._task(db, group_id, {})
                if group["object_type"] != "group":
                    raise TaskError("invalid_group")
                if state == "group":
                    raise TaskError("invalid_state: groups have no group members")
            if project is None and group_id is None and state != "group":
                raise TaskError("project_required: name the project, or pass group_id")
            project_id = self._project(db, project, scope)["id"] if project is not None else None
            if state == "group":
                result = self._group_listing(db, project_id, workstream_id, limit, offset, groups)
                scope["project_id"] = result["project_id"]
                return result
            if workstream_id is not None:
                project_id = self._workstream(db, workstream_id, project_id)["project_id"]
                identities = self._ordered_scope_ids(db, workstream_id)
                ordering = {
                    "workstream_id": workstream_id,
                    "workstream_order_revision": self._order_revision(db, workstream_id),
                }
            elif project_id is None:
                # Shared group members across projects, in creation order.
                identities = [
                    row["id"]
                    for row in db.execute(
                        "SELECT id FROM tasks WHERE parent_group_id=? ORDER BY created_at,id",
                        (group_id,),
                    )
                ]
                ordering = {"ordering": "group_creation"}
            else:
                identities = [
                    row["id"]
                    for row in db.execute(
                        "SELECT id FROM tasks WHERE project_id=? AND object_type='task' "
                        "ORDER BY order_key,id",
                        (project_id,),
                    )
                ]
                ordering = {"ordering": "project_baseline"}
            tasks = [self._task(db, identity, {}) for identity in identities]
            positions = {task["id"]: index for index, task in enumerate(tasks, 1)}
            if group_id is not None:
                tasks = [t for t in tasks if t["parent_group_id"] == group_id]
                ordering["group_id"] = group_id
            # An explicit closed state filter is itself the request to list closed work.
            show_inactive = include_inactive or state in INACTIVE_STATUSES
            hidden = [] if show_inactive else [t for t in tasks if t["status"] in INACTIVE_STATUSES]
            tasks = [t for t in tasks if show_inactive or t["status"] not in INACTIVE_STATUSES]
            fulls = {}
            if state:
                selected = []
                for task in tasks:
                    full = fulls[task["id"]] = self._card(db, task, workstream_id)
                    view, word = self._card_view(db, task, full, workstream_id)
                    if self._state_matches(state, view, word):
                        selected.append(task)
            else:
                selected = tasks
            page = self._paged(selected[offset : offset + limit + 1], limit, offset)
            cache = {workstream_id: positions} if workstream_id else {}
            items = []
            for task in page["items"]:
                full = fulls.get(task["id"]) or self._card(db, task, workstream_id)
                if full_cards:
                    full = full | {"standing": self._standing(full)}
                    items.append(
                        full | {"workstream_order_key": positions[task["id"]]}
                        if workstream_id
                        else full
                    )
                else:
                    items.append(
                        self._board_card(db, task, workstream_id, groups, full=full, cache=cache)
                    )
            return {
                "project_id": project_id,
                **ordering,
                "total": len(selected),
                **({} if show_inactive or state else {"hidden": self._hidden_counts(hidden)}),
                **page,
                "items": items,
            }

        return self._run("tasks.listed", request, operation)

    def _group_listing(self, db, project_id, workstream_id, limit, offset, include):
        """Shared groups as slim cards: in scope, related to a project, or all."""
        if workstream_id is not None:
            project_id = self._workstream(db, workstream_id, project_id)["project_id"]
            rows = db.execute(
                "SELECT g.id FROM tasks g JOIN scope_groups s ON s.group_id=g.id "
                "WHERE s.workstream_id=? AND g.object_type='group' AND NOT EXISTS "
                "(SELECT 1 FROM scope_exclusions e WHERE e.workstream_id=s.workstream_id "
                "AND e.task_id=g.id) ORDER BY g.created_at,g.id",
                (workstream_id,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT g.id FROM tasks g WHERE g.object_type='group' "
                + (
                    "AND (g.project_id=? OR EXISTS (SELECT 1 FROM tasks child "
                    "WHERE child.parent_group_id=g.id AND child.project_id=?) "
                    "OR EXISTS (SELECT 1 FROM scope_groups s JOIN workstreams w "
                    "ON w.id=s.workstream_id WHERE s.group_id=g.id AND w.project_id=?)) "
                    if project_id
                    else ""
                )
                + "ORDER BY g.created_at,g.id",
                (project_id,) * 3 if project_id else (),
            ).fetchall()
        identities = [row["id"] for row in rows]
        page = self._paged(identities[offset : offset + limit + 1], limit, offset)
        return {
            "project_id": project_id,
            **({"workstream_id": workstream_id} if workstream_id else {}),
            "ordering": "group_creation",
            "total": len(identities),
            **page,
            "items": [
                self._board_card(db, self._task(db, identity, {}), workstream_id, include)
                for identity in page["items"]
            ],
        }

    def _markdown_export(self, db, project, ws, ids, include_closed):
        tasks = []
        references = {
            identity: {"explicit scope"} for identity in self._scope_group_ids(db, ws["id"])
        }
        for identity in ids:
            task = self._details(db, self._task(db, identity, {}))
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
            "workstream_order_revision": ws["order_revision"],
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
            ids = self._ordered_scope_ids(db, workstream_id)
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
            for task_id in ids:
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
                "workstream_order_revision": ws["order_revision"],
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content": content,
            }

        return self._run(
            "workstream.exported",
            {"workstream_id": workstream_id, "include_closed": include_closed, "format": format},
            operation,
        )
