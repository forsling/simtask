"""Local SQLite task state and audit history."""

import hashlib
import json
import os
import shlex
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, canonical_path TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS project_paths (
        path TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id))""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id),
        title TEXT NOT NULL, body TEXT NOT NULL, acceptance_criteria TEXT NOT NULL,
        status TEXT NOT NULL, object_type TEXT NOT NULL, spec_revision INTEGER NOT NULL,
        accepted_spec_revision INTEGER, acceptance_note TEXT NOT NULL,
        unresolved_json TEXT NOT NULL, parent_group_id TEXT REFERENCES tasks(id),
        order_key INTEGER NOT NULL, selected_attempt_id TEXT,
        revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
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
        PRIMARY KEY(task_id, blocked_by_id))""",
    """CREATE TABLE IF NOT EXISTS gate_proposals (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        gate_type TEXT NOT NULL, detail TEXT NOT NULL, proposer TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS attempts (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        workstream_id TEXT NOT NULL REFERENCES workstreams(id),
        implementer TEXT NOT NULL, summary TEXT NOT NULL, evidence TEXT NOT NULL,
        spec_revision INTEGER NOT NULL, state TEXT NOT NULL, reviewer TEXT,
        review_note TEXT, human_review_note TEXT, revision INTEGER NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS attempt_task ON attempts(task_id, created_at, id)",
    """CREATE TABLE IF NOT EXISTS events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        actor TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL,
        project_id TEXT, task_id TEXT, request_json TEXT NOT NULL,
        before_json TEXT, after_json TEXT, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS task_history ON events(task_id, sequence)",
    "CREATE INDEX IF NOT EXISTS project_history ON events(project_id, sequence)",
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
    """Explicit projects, workstream scopes, accepted specifications and attempts."""

    def __init__(self, path: Path, actor: str = "local-agent"):
        self.path = path.expanduser().absolute()
        self.actor = actor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA:
                db.execute(statement)
            columns = {row["name"]: row for row in db.execute("PRAGMA table_info(tasks)")}
            if columns["project_id"]["notnull"]:
                db.execute(
                    SCHEMA[2].replace(
                        "CREATE TABLE IF NOT EXISTS tasks (", "CREATE TABLE tasks_new ("
                    )
                )
                db.execute("INSERT INTO tasks_new SELECT * FROM tasks")
                db.execute("DROP TABLE tasks")
                db.execute("ALTER TABLE tasks_new RENAME TO tasks")
                db.execute(SCHEMA[3])
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("task database has invalid foreign keys")
            db.commit()
            db.execute("PRAGMA foreign_keys=ON")

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

    @staticmethod
    def _revision(task: dict, expected_revision: int):
        if type(expected_revision) is not int or task["revision"] != expected_revision:
            raise TaskError(
                f"revision_conflict: expected {expected_revision}, current {task['revision']}; "
                "get_tasks, reconcile your edit, and retry with the current revision"
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
                "INSERT INTO projects VALUES (:id,:name,:canonical_path,:created_at)", project
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
            return scope["after"]

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
                row["scope"] = self._scope_ids(db, row["id"])
                row["groups"] = self._scope_group_ids(db, row["id"])
                row["status"] = self._status_summary(db, row["id"], row["scope"])
            return self._paged(rows, limit, offset)

        return self._run(
            "workstreams.listed", {"project": project, "limit": limit, "offset": offset}, operation
        )

    def workstream_status(self, workstream_id, limit=50, offset=0):
        request = dict(workstream_id=workstream_id, limit=limit, offset=offset)

        def operation(db, scope):
            self._page(limit, offset)
            ws = self._workstream(db, workstream_id)
            project = db.execute(
                "SELECT * FROM projects WHERE id=?", (ws["project_id"],)
            ).fetchone()
            scope["project_id"] = ws["project_id"]
            queue = self._scoped_queue(db, workstream_id)
            return {
                "project": dict(project),
                "workstream": ws,
                "status": self._status_summary(db, workstream_id),
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
            reasons = self._gate_reasons(db, task, workstream_id)
            view = self._status_view(task, reasons)
            queue.append(
                {
                    "id": task["id"],
                    "title": task["title"],
                    "revision": task["revision"],
                    "order_key": task["order_key"],
                    "view": view,
                    "accepted": task["accepted"] if task["object_type"] == "task" else None,
                    "object_type": task["object_type"],
                    "gate_diagnostics": reasons,
                }
            )
        return queue

    @staticmethod
    def _status_view(task, reasons):
        if task["object_type"] == "group":
            return "group"
        if task["status"] in {"done", "dropped", "deferred"}:
            return task["status"]
        if "signoff" in reasons:
            return "signoff"
        if "review" in reasons:
            return "review"
        for reason in ("pending_acceptance", "unresolved_items", "prerequisites"):
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
                "pending_acceptance",
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
                "pending_acceptance",
                "unresolved_items",
                "prerequisites",
                "review",
                "signoff",
            )
        }
        for item in self._scoped_queue(db, workstream_id):
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
                    "global_progress": group["progress"],
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
            "agent_liveness": "unknown",
            "scoped_count": len(ids),
            "counts": counts,
            "overlapping_gate_diagnostics": overlapping,
            "referenced_groups": referenced_groups,
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
                return self._ready_init(db, selected, ws)
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
                        "SELECT * FROM workstreams WHERE project_id=? ORDER BY created_at,id",
                        (selected["id"],),
                    ).fetchall()
                    return {
                        "state": "new_branch",
                        "message": "Choose an initial scope or an explicit workstream rebind",
                        "path": canonical,
                        "project": selected,
                        "candidates": [dict(row) for row in rows],
                        "choices": ["new_workstream", "rebind_workstream"],
                    }
                projects = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM projects ORDER BY name,id LIMIT 51"
                    ).fetchall()
                ]
                workstreams = [
                    dict(row)
                    for row in db.execute(
                        "SELECT id,project_id,name,branch,checkout_path,revision "
                        "FROM workstreams ORDER BY created_at,id LIMIT 51"
                    ).fetchall()
                ]
                return {
                    "state": "unregistered_checkout",
                    "message": "Choose how this checkout relates to existing projects",
                    "path": canonical,
                    "choices": ["create_project", "attach_workstream", "rebind_workstream"],
                    "project_candidates": projects[:50],
                    "more_projects": len(projects) > 50,
                    "workstream_candidates": workstreams[:50],
                    "more_workstreams": len(workstreams) > 50,
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
                    "INSERT INTO projects VALUES (:id,:name,:canonical_path,:created_at)", selected
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
                self._set_scope(db, ws["id"], members, groups, exclusions)
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
            return self._ready_init(db, selected, ws)

        return self._run("session.initialized", request, operation)

    def _ready_init(self, db, project, ws):
        return {
            "state": "ready",
            "message": "Workstream ready",
            "project": project,
            "workstream": ws,
            "scope_revision": ws["revision"],
            "queue": self._scoped_queue(db, ws["id"]),
            "groups": self._scope_group_ids(db, ws["id"]),
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
            self._set_scope(db, ws["id"], members, groups, exclusions)
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
            return after

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
                "scope": self._scope_ids(db, ws["id"]),
                "groups": self._scope_group_ids(db, ws["id"]),
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
            return {**self._workstream(db, workstream_id), **scope["after"]}

        return self._run("scope.changed", request, operation)

    @staticmethod
    def _task(db, task_id, scope):
        scope["task_id"] = task_id
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskError(f"unknown_task: {task_id}")
        task = dict(row)
        task["unresolved_items"] = json.loads(task.pop("unresolved_json"))
        task["accepted"] = task["accepted_spec_revision"] == task["spec_revision"]
        scope["project_id"] = task["project_id"]
        return task

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
            accepted_spec_revision=:accepted_spec_revision, acceptance_note=:acceptance_note,
            unresolved_json=:unresolved_json, object_type=:object_type,
            parent_group_id=:parent_group_id, order_key=:order_key, status=:status,
            selected_attempt_id=:selected_attempt_id,
            revision=:revision, updated_at=:updated_at WHERE id=:id""",
            values,
        )

    @staticmethod
    def _details(db, task):
        task = dict(task)
        task["accepted"] = (
            task["accepted_spec_revision"] == task["spec_revision"]
            if task["object_type"] == "task"
            else None
        )
        if task["parent_group_id"]:
            group = db.execute(
                "SELECT id,title,body,acceptance_criteria FROM tasks WHERE id=?",
                (task["parent_group_id"],),
            ).fetchone()
            task["parent_group"] = dict(group)
        task["blocked_by"] = [
            r["blocked_by_id"]
            for r in db.execute(
                "SELECT blocked_by_id FROM prerequisites WHERE task_id=? ORDER BY blocked_by_id",
                (task["id"],),
            )
        ]
        task["attempts"] = [
            dict(r)
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
        return task

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

    def list_groups(self, project=None, limit=50, offset=0):
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
            groups = []
            for row in rows:
                detail = self._details(db, self._task(db, row["id"], {}))
                groups.append(
                    {
                        key: detail[key]
                        for key in ("id", "title", "revision", "progress", "complete")
                    }
                )
            return self._paged(groups, limit, offset)

        return self._run("groups.listed", request, operation)

    def create_group(self, workstream_id, title, body="", acceptance_criteria=""):
        request = dict(
            workstream_id=workstream_id,
            title=title,
            body=body,
            acceptance_criteria=acceptance_criteria,
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
            group_id = _id("tsk_")
            db.execute(
                """INSERT INTO tasks
                (id,project_id,title,body,acceptance_criteria,status,object_type,
                spec_revision,accepted_spec_revision,acceptance_note,unresolved_json,
                parent_group_id,order_key,selected_attempt_id,revision,created_at,updated_at)
                VALUES (?,NULL,?,?,?,'open','group',1,NULL,'','[]',NULL,0,NULL,1,?,?)""",
                (group_id, title.strip(), body, acceptance_criteria, now, now),
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
        db, project_id, title, body, acceptance_criteria, accepted, note, parent_group_id=None
    ):
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
            raise TaskError("invalid_specification: title and description required")
        if not isinstance(acceptance_criteria, str):
            raise TaskError("invalid_specification: acceptance criteria must be text")
        if accepted and not note.strip():
            raise TaskError("user_request_required: record the explicit user request")
        now = timestamp()
        order = db.execute(
            "SELECT coalesce(max(order_key),0)+1 FROM tasks WHERE project_id=?", (project_id,)
        ).fetchone()[0]
        task_id = _id("tsk_")
        db.execute(
            """INSERT INTO tasks
            (id,project_id,title,body,acceptance_criteria,status,object_type,spec_revision,
             accepted_spec_revision,acceptance_note,unresolved_json,parent_group_id,order_key,
             selected_attempt_id,revision,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                project_id,
                title.strip(),
                body,
                acceptance_criteria,
                "open",
                "task",
                1,
                1 if accepted else None,
                note if accepted else "",
                "[]",
                parent_group_id,
                order,
                None,
                1,
                now,
                now,
            ),
        )
        return task_id

    def create_task(
        self,
        project,
        title,
        body="",
        acceptance_criteria="",
        source="agent",
        user_request="",
        workstream_id=None,
        scope="inbox",
        group_id=None,
        group_expected_revision=None,
    ):
        request = dict(
            project=project,
            title=title,
            body=body,
            acceptance_criteria=acceptance_criteria,
            source=source,
            user_request=user_request,
            workstream_id=workstream_id,
            scope=scope,
            group_id=group_id,
            group_expected_revision=group_expected_revision,
        )

        def operation(db, event):
            project_id = self._project(db, project, event)["id"]
            if source not in {"agent", "user"}:
                raise TaskError("invalid_source: agent or user")
            if scope not in {"inbox", "workstream"}:
                raise TaskError("invalid_scope: inbox or workstream")
            if scope == "workstream":
                if not workstream_id:
                    raise TaskError("workstream_required")
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
                source == "user",
                user_request,
                group_id,
            )
            if group_id:
                db.execute(
                    "INSERT INTO prerequisites SELECT ?,blocked_by_id "
                    "FROM prerequisites WHERE task_id=?",
                    (task_id, group_id),
                )
                updated_group = {
                    **group,
                    "revision": group["revision"] + 1,
                    "updated_at": timestamp(),
                }
                self._save_task(db, updated_group)
            if scope == "workstream":
                db.execute("INSERT INTO scope_members VALUES (?, ?)", (workstream_id, task_id))
                self._touch_workstream(db, workstream_id)
            task = self._details(db, self._task(db, task_id, event))
            if group_id:
                event["before"] = {"group": group}
                event["after"] = {"task": task, "group": self._details(db, updated_group)}
            else:
                event["after"] = task
            return task

        return self._run("task.created", request, operation)

    def update_task(self, task_id, expected_revision, changes):
        request = dict(task_id=task_id, expected_revision=expected_revision, changes=changes)

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before, concrete=False)
            allowed = {"title", "body", "acceptance_criteria"}
            if not isinstance(changes, dict) or not changes or set(changes) - allowed:
                raise TaskError("invalid_patch: edit title, body, or acceptance_criteria")
            after = {**before, **changes}
            if (
                not isinstance(after["title"], str)
                or not after["title"].strip()
                or any(not isinstance(after[k], str) for k in ("body", "acceptance_criteria"))
            ):
                raise TaskError("invalid_specification")
            if any(before[k] != after[k] for k in allowed):
                after.update(
                    spec_revision=before["spec_revision"] + 1,
                    revision=before["revision"] + 1,
                    updated_at=timestamp(),
                )
                self._save_task(db, after)
            scope.update(before=before, after=after)
            return self._details(db, after)

        return self._run("task.updated", request, operation)

    def accept_task(self, task_id, expected_revision, user_note):
        request = dict(task_id=task_id, expected_revision=expected_revision, user_note=user_note)

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if not isinstance(user_note, str) or not user_note.strip():
                raise TaskError("user_verdict_required")
            after = {
                **before,
                "accepted_spec_revision": before["spec_revision"],
                "acceptance_note": user_note,
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            self._save_task(db, after)
            scope.update(before=before, after=after)
            return self._details(db, after)

        return self._run("task.accepted", request, operation)

    def set_disposition(self, task_id, expected_revision, disposition, note):
        """Defer, resume, or drop without changing the accepted specification."""
        request = dict(
            task_id=task_id, expected_revision=expected_revision, disposition=disposition, note=note
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if disposition not in {"open", "deferred", "dropped"} or not note.strip():
                raise TaskError("invalid_disposition: use open, deferred or dropped with a reason")
            after = {
                **before,
                "status": disposition,
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            self._save_task(db, after)
            scope.update(before=before, after=after)
            return self._details(db, after)

        return self._run("task.disposition_changed", request, operation)

    def propose_prerequisite(
        self, task_id, expected_revision, title, body="", acceptance_criteria="", workstream_id=None
    ):
        """Atomically create and link a pending prerequisite in this scope or inbox."""
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            title=title,
            body=body,
            acceptance_criteria=acceptance_criteria,
            workstream_id=workstream_id,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            if workstream_id:
                self._workstream(db, workstream_id, before["project_id"])
                if task_id not in self._scope_ids(db, workstream_id):
                    raise TaskError("task_out_of_scope")
            proposed_id = self._insert_task(
                db, before["project_id"], title, body, acceptance_criteria, False, ""
            )
            db.execute("INSERT INTO prerequisites VALUES (?, ?)", (task_id, proposed_id))
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
                    "INSERT INTO gate_proposals VALUES "
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

    def add_prerequisite(self, task_id, expected_revision, blocked_by_id, handling="active"):
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            blocked_by_id=blocked_by_id,
            handling=handling,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            self._require_mutable(db, before)
            dependency = self._task(db, blocked_by_id, {})
            if (
                dependency["object_type"] != "group"
                and before["project_id"] != dependency["project_id"]
            ) or task_id == blocked_by_id:
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
                )
                db.execute(
                    "INSERT INTO gate_proposals VALUES "
                    "(:id,:task_id,:gate_type,:detail,:proposer,:created_at)",
                    proposal,
                )
                scope["after"] = proposal
                return proposal
            if handling not in {"active", "user"}:
                raise TaskError("invalid_handling")
            if self._would_cycle(db, task_id, blocked_by_id):
                raise TaskError("prerequisite_cycle")
            db.execute(
                "INSERT OR IGNORE INTO prerequisites VALUES (?, ?)", (task_id, blocked_by_id)
            )
            after = {**before, "revision": before["revision"] + 1, "updated_at": timestamp()}
            self._save_task(db, after)
            scope.update(before=before, after=after)
            return self._details(db, after)

        return self._run("gate.prerequisite_added", request, operation)

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
                if (
                    dependency["object_type"] != "group"
                    and dependency["project_id"] != before["project_id"]
                ) or dependency["status"] == "dropped":
                    raise TaskError("invalid_prerequisite")
                if self._would_cycle(db, row["task_id"], row["detail"]):
                    raise TaskError("prerequisite_cycle")
                db.execute(
                    "INSERT OR IGNORE INTO prerequisites VALUES (?, ?)",
                    (row["task_id"], row["detail"]),
                )
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
            if not isinstance(members, list) or not members:
                raise TaskError("members_required")
            after = {
                **before,
                "object_type": "group",
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            self._save_task(db, after)
            db.execute(
                """INSERT OR IGNORE INTO scope_groups
                SELECT workstream_id, ? FROM scope_members WHERE task_id=?""",
                (task_id, task_id),
            )
            db.execute(
                """UPDATE workstreams SET revision=revision+1
                WHERE id IN (SELECT workstream_id FROM scope_members WHERE task_id=?)""",
                (task_id,),
            )
            children = []
            for member in members:
                child_id = self._insert_task(
                    db,
                    before["project_id"],
                    member["title"],
                    member.get("body", ""),
                    member.get("acceptance_criteria", ""),
                    False,
                    "",
                    task_id,
                )
                db.execute(
                    "INSERT INTO prerequisites SELECT ?,blocked_by_id "
                    "FROM prerequisites WHERE task_id=?",
                    (child_id, task_id),
                )
                children.append(child_id)
            db.execute("DELETE FROM prerequisites WHERE task_id=?", (task_id,))
            scope.update(before=before, after={"group": after, "members": children})
            return self._details(db, after)

        return self._run("task.decomposed", request, operation)

    def reorder_tasks(self, project, ordered_ids, expected_order=None):
        request = dict(project=project, ordered_ids=ordered_ids, expected_order=expected_order)

        def operation(db, scope):
            project_id = self._project(db, project, scope)["id"]
            current = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM tasks WHERE project_id=? AND object_type='task' "
                    "ORDER BY order_key,id",
                    (project_id,),
                )
            ]
            if expected_order != current:
                raise TaskError("revision_conflict: re-read the current project order")
            if len(ordered_ids) != len(current) or set(ordered_ids) != set(current):
                raise TaskError("invalid_order: include every project task exactly once")
            old_positions = {identity: index for index, identity in enumerate(current, 1)}
            completed = {
                row["id"]
                for row in db.execute(
                    "SELECT id FROM tasks WHERE project_id=? AND status='done'", (project_id,)
                )
            }
            if any(
                old_positions[identity] != index
                for index, identity in enumerate(ordered_ids, 1)
                if identity in completed
            ):
                raise TaskError("completed_task_immutable: ordering cannot move signed-off tasks")
            for index, task_id in enumerate(ordered_ids, 1):
                if old_positions[task_id] != index:
                    db.execute(
                        "UPDATE tasks SET order_key=?, revision=revision+1 WHERE id=?",
                        (index, task_id),
                    )
            scope.update(before={"ordered_ids": current}, after={"ordered_ids": ordered_ids})
            return scope["after"]

        return self._run("tasks.reordered", request, operation)

    @staticmethod
    def _unsatisfied_prerequisite(db, task_id):
        return bool(
            db.execute(
                """SELECT 1 FROM prerequisites p JOIN tasks dependency
            ON dependency.id=p.blocked_by_id WHERE p.task_id=? AND (
                (dependency.object_type='task' AND dependency.status!='done') OR
                (dependency.object_type='group' AND EXISTS (
                    SELECT 1 FROM tasks child WHERE child.parent_group_id=dependency.id
                    AND child.status!='done')) OR
                (dependency.object_type='group' AND NOT EXISTS (
                    SELECT 1 FROM tasks child WHERE child.parent_group_id=dependency.id)))
            LIMIT 1""",
                (task_id,),
            ).fetchone()
        )

    @staticmethod
    def _gate_reasons(db, task, workstream_id=None):
        if task["object_type"] == "group":
            return ["closed_or_group"]
        reasons = []
        if task["status"] in {"done", "dropped", "deferred"}:
            reasons.append("closed_or_group")
        if not task["accepted"]:
            reasons.append("pending_acceptance")
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

    def get_next_task(self, workstream_id):
        def operation(db, scope):
            ws = self._workstream(db, workstream_id)
            scope["project_id"] = ws["project_id"]
            ids = self._scope_ids(db, workstream_id)
            if not ids:
                return {"task": None, "diagnostics": {"scope_empty": True, "scoped": 0}}
            marks = ",".join("?" for _ in ids)
            tasks = [
                self._task(db, r["id"], {})
                for r in db.execute(
                    f"SELECT id FROM tasks WHERE id IN ({marks}) ORDER BY order_key,id", ids
                )
            ]
            counts = {
                key: 0
                for key in (
                    "pending_acceptance",
                    "unresolved_items",
                    "prerequisites",
                    "review",
                    "signoff",
                    "closed_or_group",
                )
            }
            for task in tasks:
                reasons = self._gate_reasons(db, task, workstream_id)
                if not reasons:
                    return {
                        "task": self._details(db, task),
                        "diagnostics": {"scope_empty": False, "scoped": len(ids)},
                    }
                for reason in reasons:
                    counts[reason] += 1
            return {
                "task": None,
                "diagnostics": {"scope_empty": False, "scoped": len(ids), **counts},
            }

        return self._run("task.next_read", {"workstream_id": workstream_id}, operation)

    def record_result(
        self, task_id, workstream_id, expected_revision, implementer, summary, evidence
    ):
        request = dict(
            task_id=task_id,
            workstream_id=workstream_id,
            expected_revision=expected_revision,
            implementer=implementer,
            summary=summary,
            evidence=evidence,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            if before["object_type"] != "task":
                raise TaskError("group_not_executable: groups have no implementation attempts")
            self._workstream(db, workstream_id, before["project_id"])
            if task_id not in self._scope_ids(db, workstream_id):
                raise TaskError("task_out_of_scope")
            if self._gate_reasons(db, before, workstream_id):
                raise TaskError("task_not_eligible: clear gates before recording a result")
            if not all(isinstance(x, str) and x.strip() for x in (implementer, summary, evidence)):
                raise TaskError("result_required: implementer, summary and evidence")
            now = timestamp()
            attempt = dict(
                id=_id("att_"),
                task_id=task_id,
                workstream_id=workstream_id,
                implementer=implementer,
                summary=summary,
                evidence=evidence,
                spec_revision=before["spec_revision"],
                state="review",
                reviewer=None,
                review_note=None,
                human_review_note=None,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            db.execute(
                """INSERT INTO attempts VALUES (:id,:task_id,:workstream_id,:implementer,
                :summary,:evidence,:spec_revision,:state,:reviewer,:review_note,:human_review_note,:revision,
                :created_at,:updated_at)""",
                attempt,
            )
            after = {**before, "revision": before["revision"] + 1, "updated_at": now}
            self._save_task(db, after)
            scope.update(before=before, after={"task": after, "attempt": attempt})
            return attempt

        return self._run("attempt.recorded", request, operation)

    def record_review(self, attempt_id, expected_revision, reviewer, verdict, note):
        request = dict(
            attempt_id=attempt_id,
            expected_revision=expected_revision,
            reviewer=reviewer,
            verdict=verdict,
            note=note,
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
                raise TaskError("revision_conflict: re-read the attempt")
            if before["state"] != "review" or verdict not in {"pass", "rework"}:
                raise TaskError("invalid_review_state")
            if not reviewer.strip() or reviewer == before["implementer"] or not note.strip():
                raise TaskError("independent_review_required: reviewer differs from implementer")
            after = {
                **before,
                "state": "passed" if verdict == "pass" else "rework",
                "reviewer": reviewer,
                "review_note": note,
                "revision": before["revision"] + 1,
                "updated_at": timestamp(),
            }
            db.execute(
                """UPDATE attempts SET state=:state, reviewer=:reviewer,
                review_note=:review_note, revision=:revision,
                updated_at=:updated_at WHERE id=:id""",
                after,
            )
            scope.update(before=before, after=after)
            return after

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
            return after

        return self._run("attempt.human_reviewed", request, operation)

    def signoff_task(
        self, task_id, expected_revision, verdict, user_note, attempt_id=None, rejection="rework"
    ):
        request = dict(
            task_id=task_id,
            expected_revision=expected_revision,
            verdict=verdict,
            user_note=user_note,
            attempt_id=attempt_id,
            rejection=rejection,
        )

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            if before["object_type"] != "task" or before["status"] == "done":
                raise TaskError("invalid_signoff_target")
            if verdict not in {"approve", "reject"} or not user_note.strip():
                raise TaskError("user_verdict_required")
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
                raise TaskError("review_required: select a passed attempt")
            after = {**before, "revision": before["revision"] + 1, "updated_at": timestamp()}
            if verdict == "approve":
                if (
                    not before["accepted"]
                    or before["unresolved_items"]
                    or "prerequisites" in self._gate_reasons(db, before)
                ):
                    raise TaskError("task_not_ready_for_signoff")
                after["status"] = "done"
                after["selected_attempt_id"] = attempt_id
            elif rejection == "rework":
                after["status"] = "rework"
                db.execute(
                    "UPDATE attempts SET state='rework', revision=revision+1 WHERE id=?",
                    (attempt_id,),
                )
            elif rejection == "revise":
                after["status"] = "open"
                after["spec_revision"] += 1
                after["unresolved_items"] = before["unresolved_items"] + [
                    {"id": _id("unr_"), "text": user_note}
                ]
            else:
                raise TaskError("invalid_rejection: rework or revise")
            self._save_task(db, after)
            scope.update(before=before, after=after)
            return self._details(db, after)

        return self._run("task.signoff", request, operation)

    def list_tasks(self, project, workstream_id=None, state=None, limit=50, offset=0):
        request = dict(
            project=project, workstream_id=workstream_id, state=state, limit=limit, offset=offset
        )

        def operation(db, scope):
            self._page(limit, offset)
            project_id = self._project(db, project, scope)["id"]
            ids = None
            if workstream_id:
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
                reasons = self._gate_reasons(db, task, workstream_id)
                view = self._status_view(task, reasons)
                if state and state != view:
                    continue
                result.append(
                    {
                        "id": task["id"],
                        "title": task["title"],
                        "revision": task["revision"],
                        "order_key": task["order_key"],
                        "view": view,
                        "accepted": task["accepted"] if task["object_type"] == "task" else None,
                        "object_type": task["object_type"],
                    }
                )
            return self._paged(result[offset : offset + limit + 1], limit, offset)

        return self._run("tasks.listed", request, operation)

    def export_workstream(self, workstream_id, include_closed=True):
        def operation(db, scope):
            ws = self._workstream(db, workstream_id)
            scope["project_id"] = ws["project_id"]
            project = db.execute(
                "SELECT * FROM projects WHERE id=?", (ws["project_id"],)
            ).fetchone()
            ids = self._scope_ids(db, workstream_id)
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
                        f"Accepted: {str(task['accepted']).lower()}",
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
            {"workstream_id": workstream_id, "include_closed": include_closed},
            operation,
        )
