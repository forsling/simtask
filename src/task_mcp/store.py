"""SQLite persistence. Each operation and its audit event commit together."""

import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

KINDS = {"design", "auto"}
STATUSES = {"open", "review", "signoff", "rework", "blocked", "deferred", "done", "dropped"}
TEXT_FIELDS = {"title", "body", "group", "track", "kind", "status", "evidence", "review"}
EDITABLE = TEXT_FIELDS | {"priority"}
SUMMARY = "id, project_id, title, kind, status, group_name, track, priority, revision"
SCHEMA = [
    """CREATE TABLE projects (
        id TEXT PRIMARY KEY, project_key TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL, path TEXT, created_at TEXT NOT NULL)""",
    """CREATE TABLE tasks (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        title TEXT NOT NULL, body TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
        group_name TEXT NOT NULL, track TEXT NOT NULL, priority INTEGER NOT NULL,
        evidence TEXT NOT NULL, review TEXT NOT NULL, revision INTEGER NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE INDEX task_board ON tasks(project_id, track, status, priority, created_at, id)""",
    """CREATE TABLE events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        actor TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL,
        project_id TEXT, task_id TEXT, request_json TEXT NOT NULL,
        before_json TEXT, after_json TEXT, error TEXT)""",
    "CREATE INDEX task_history ON events(task_id, sequence)",
    "CREATE INDEX project_history ON events(project_id, sequence)",
]


class TaskError(ValueError):
    """An actionable domain error, safe to show to the caller."""


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def default_database() -> Path:
    if configured := os.environ.get("TASK_MCP_DB"):
        return Path(configured).expanduser().absolute()
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "task-mcp" / "tasks.sqlite3"


def task_dict(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["group"] = result.pop("group_name")
    return result


class LegacyStore:
    def __init__(self, path: Path, actor: str = "local-agent"):
        self.path = path.expanduser().absolute()
        self.actor = actor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                for statement in SCHEMA:
                    db.execute(statement)
                db.execute("PRAGMA user_version=1")
            elif version != 1:
                raise RuntimeError(f"Unsupported database version {version}; expected 1")
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _event(self, db, action, request, scope, outcome, error=None):
        db.execute(
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

    def _run(self, action: str, request: dict, operation: Callable) -> Any:
        scope: dict = {}
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                result = operation(db, scope)
                self._event(db, action, request, scope, "ok")
                db.commit()
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

    def _project(self, db, project: str, scope: dict, create=False) -> dict:
        if not isinstance(project, str) or not project.strip():
            raise TaskError("project_required: use a project ID, absolute directory path, or name")
        project = project.strip()
        path = Path(project).expanduser()
        if path.is_absolute():
            canonical = str(path.resolve())
            key, name = "path:" + canonical, Path(canonical).name or canonical
            rows = db.execute("SELECT * FROM projects WHERE project_key=?", (key,)).fetchall()
        else:
            canonical, key, name = None, "name:" + project, project
            rows = db.execute("SELECT * FROM projects WHERE id=?", (project,)).fetchall()
            if not rows:
                rows = db.execute("SELECT * FROM projects WHERE name=?", (project,)).fetchall()
        if len(rows) > 1:
            raise TaskError("ambiguous_project: use an absolute path or the ID from list_projects")
        if rows:
            result = dict(rows[0])
        elif create and not project.startswith("prj_"):
            result = dict(
                id="prj_" + uuid4().hex,
                project_key=key,
                name=name,
                path=canonical,
                created_at=timestamp(),
            )
            db.execute(
                "INSERT INTO projects VALUES (:id, :project_key, :name, :path, :created_at)", result
            )
            self._event(
                db,
                "project.created",
                {"project": project},
                {"project_id": result["id"], "after": result},
                "ok",
            )
        else:
            raise TaskError("unknown_project: list projects or create its first task with add_task")
        scope["project_id"] = result["id"]
        return result

    @staticmethod
    def _task(db, task_id: str, scope: dict) -> dict:
        scope["task_id"] = task_id
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskError(f"unknown_task: {task_id}")
        task = task_dict(row)
        scope["project_id"] = task["project_id"]
        return task

    @staticmethod
    def _validate(task: dict):
        for field in TEXT_FIELDS:
            value = task[field]
            max_length = 100_000 if field in {"body", "evidence", "review"} else 240
            if not isinstance(value, str) or len(value) > max_length:
                raise TaskError(
                    f"invalid_field: {field} must be text up to {max_length} characters"
                )
        if any(not task[field].strip() for field in ("title", "group", "track")):
            raise TaskError("invalid_field: title, group, and track cannot be blank")
        if task["kind"] not in KINDS or task["status"] not in STATUSES:
            raise TaskError("invalid_state: unknown kind or status")
        if type(task["priority"]) is not int or not -1000 <= task["priority"] <= 1000:
            raise TaskError(
                "invalid_priority: use an integer from -1000 to 1000; lower comes first"
            )
        if task["kind"] == "design" and task["status"] in {"review", "signoff", "rework"}:
            raise TaskError("invalid_state: promote Design to Auto before implementation review")
        if task["status"] == "signoff" and not (
            task["evidence"].strip() and task["review"].strip()
        ):
            raise TaskError(
                "missing_evidence: signoff needs evidence and an independent review summary"
            )

    @staticmethod
    def _revision(task: dict, expected_revision: int):
        if type(expected_revision) is not int or task["revision"] != expected_revision:
            raise TaskError(
                f"revision_conflict: expected {expected_revision}, current {task['revision']}; "
                "get_tasks, reconcile your edit, and retry with the current revision"
            )

    @staticmethod
    def _save(db, task: dict):
        values = {**task, "group_name": task["group"]}
        db.execute(
            """UPDATE tasks SET title=:title, body=:body, kind=:kind, status=:status,
            group_name=:group_name, track=:track, priority=:priority, evidence=:evidence,
            review=:review, revision=:revision, updated_at=:updated_at WHERE id=:id""",
            values,
        )

    def add_task(
        self, project, title, body="", kind="design", group="General", track="main", priority=0
    ):
        request = dict(
            project=project,
            title=title,
            body=body,
            kind=kind,
            group=group,
            track=track,
            priority=priority,
        )

        def operation(db, scope):
            now = timestamp()
            task = dict(
                id="tsk_" + uuid4().hex,
                title=title,
                body=body,
                kind=kind,
                status="open",
                group=group,
                track=track,
                priority=priority,
                evidence="",
                review="",
                revision=1,
                created_at=now,
                updated_at=now,
            )
            self._validate(task)
            task["project_id"] = self._project(db, project, scope, create=True)["id"]
            db.execute(
                """INSERT INTO tasks VALUES
                (:id, :project_id, :title, :body, :kind, :status, :group, :track, :priority,
                 :evidence, :review, :revision, :created_at, :updated_at)""",
                task,
            )
            scope.update(task_id=task["id"], after=task)
            return task

        return self._run("task.created", request, operation)

    def list_projects(self, limit=50, offset=0):
        def operation(db, scope):
            self._page(limit, offset)
            rows = db.execute(
                """SELECT p.*, count(t.id) AS task_count FROM projects p LEFT JOIN tasks t
                ON p.id=t.project_id GROUP BY p.id ORDER BY p.name, p.id LIMIT ? OFFSET ?""",
                (limit + 1, offset),
            ).fetchall()
            return self._paged([dict(row) for row in rows], limit, offset)

        return self._run("projects.listed", dict(limit=limit, offset=offset), operation)

    @staticmethod
    def _page(limit, offset):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TaskError("invalid_limit: use 1 through 100")
        if type(offset) is not int or offset < 0:
            raise TaskError("invalid_offset: use a nonnegative integer")

    @staticmethod
    def _paged(rows, limit, offset):
        return {"items": rows[:limit], "next_offset": offset + limit if len(rows) > limit else None}

    def list_tasks(
        self, project, kind=None, status=None, group=None, track="main", limit=50, offset=0
    ):
        request = dict(
            project=project,
            kind=kind,
            status=status,
            group=group,
            track=track,
            limit=limit,
            offset=offset,
        )

        def operation(db, scope):
            self._page(limit, offset)
            if kind is not None and kind not in KINDS:
                raise TaskError("invalid_kind: use design or auto")
            if status is not None and status not in STATUSES:
                raise TaskError("invalid_status: unknown status")
            project_id = self._project(db, project, scope)["id"]
            where, values = ["project_id=?", "track=?"], [project_id, track]
            for column, value in (("kind", kind), ("status", status), ("group_name", group)):
                if value is not None:
                    where.append(f"{column}=?")
                    values.append(value)
            if status is None:
                where.append("status NOT IN ('deferred', 'done', 'dropped')")
            rows = db.execute(
                f"SELECT {SUMMARY} FROM tasks WHERE {' AND '.join(where)} "
                "ORDER BY CASE status WHEN 'rework' THEN 0 ELSE 1 END, "
                "priority, created_at, id LIMIT ? OFFSET ?",
                (*values, limit + 1, offset),
            ).fetchall()
            return self._paged([task_dict(row) for row in rows], limit, offset)

        return self._run("tasks.listed", request, operation)

    def get_tasks(self, ids):
        def operation(db, scope):
            if not isinstance(ids, list) or not 1 <= len(ids) <= 20:
                raise TaskError("invalid_ids: request between 1 and 20 task IDs")
            tasks = [self._task(db, task_id, {}) for task_id in ids]
            if len({t["project_id"] for t in tasks}) == 1:
                scope["project_id"] = tasks[0]["project_id"]
            if len(ids) == 1:
                scope["task_id"] = ids[0]
            return {"items": tasks}

        return self._run("tasks.read", {"ids": ids}, operation)

    def update_task(self, task_id, expected_revision, changes):
        request = dict(task_id=task_id, expected_revision=expected_revision, changes=changes)

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            if not changes or set(changes) - EDITABLE:
                raise TaskError("invalid_patch: supply only editable task fields")
            after = {**before, **changes}
            if after["kind"] == "auto" and after["status"] == "done":
                raise TaskError("signoff_required: use signoff_task after the user's approval")
            if before["status"] == "signoff" and after["status"] == "signoff":
                if any(before[k] != after[k] for k in ("title", "body", "kind", "evidence")):
                    raise TaskError("review_required: move changed signoff work back to review")
            self._validate(after)
            if after != before:
                after.update(revision=before["revision"] + 1, updated_at=timestamp())
                self._save(db, after)
            scope.update(before=before, after=after)
            return after

        return self._run("task.updated", request, operation)

    def signoff_task(self, task_id, expected_revision, verdict, user_note):
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            verdict=verdict,
            user_note=user_note,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            if before["status"] != "signoff":
                raise TaskError("not_ready: only a task awaiting signoff can receive a verdict")
            if verdict not in {"approve", "reject"} or not user_note.strip():
                raise TaskError(
                    "invalid_verdict: supply approve/reject and the user's actual decision"
                )
            after = {
                **before,
                "status": "done" if verdict == "approve" else "rework",
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            if verdict == "reject":
                after["review"] = user_note
            self._save(db, after)
            scope.update(before=before, after=after)
            return after

        return self._run("task.signoff", request, operation)

    def list_events(
        self,
        project=None,
        task_id=None,
        after_sequence=0,
        through_sequence=None,
        limit=50,
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
                # Include batch retrieval events mentioning this task.
                where.append(
                    "(task_id=? OR EXISTS (SELECT 1 FROM "
                    "json_each(events.request_json, '$.ids') WHERE value=?))"
                )
                values.extend([task_id, task_id])
            columns = (
                "*"
                if include_details
                else ("sequence, timestamp, actor, action, outcome, project_id, task_id, error")
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

    def export_project(self, project, track="main", include_closed=False):
        request = dict(project=project, track=track, include_closed=include_closed)

        def operation(db, scope):
            selected = self._project(db, project, scope)
            closed = "" if include_closed else "AND status NOT IN ('done', 'dropped')"
            tasks = [
                task_dict(row)
                for row in db.execute(
                    f"SELECT * FROM tasks WHERE project_id=? AND track=? {closed} "
                    "ORDER BY group_name, priority, created_at, id",
                    (selected["id"], track),
                ).fetchall()
            ]
            lines = [f"# {selected['name']} — {track}", "", f"Project: {selected['id']}", ""]
            group = None
            for task in tasks:
                if task["group"] != group:
                    group = task["group"]
                    lines.extend([f"## {group}", ""])
                lines.extend(
                    [
                        f"### {task['title']}",
                        "",
                        f"`{task['id']}` · {task['kind']} · {task['status']} "
                        f"· revision {task['revision']}",
                        "",
                        task["body"],
                        "",
                    ]
                )
                for field in ("evidence", "review"):
                    if task[field]:
                        lines.extend([f"**{field.title()}**", "", task[field], ""])
            return "\n".join(lines)

        return self._run("project.exported", request, operation)


# The v0.1 storage implementation remains here solely as the migration substrate.
# Resolve the public Store lazily so importing task_mcp.v1 directly has no cycle.
def __getattr__(name):
    if name == "Store":
        from task_mcp.v1 import Store

        return Store
    raise AttributeError(name)
