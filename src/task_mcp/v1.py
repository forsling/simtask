"""V1 task model layered over the durable v0.1 SQLite/audit database."""

import hashlib
import json
import shlex
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from task_mcp.store import SCHEMA, LegacyStore, TaskError, timestamp


def _id(prefix):
    return prefix + uuid4().hex


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store(LegacyStore):
    """Explicit projects, workstream scopes, accepted specifications and attempts."""

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
                version = 1
            if version == 1:
                self._migrate(db)
                version = 2
            if version == 2:
                db.execute("ALTER TABLE tasks ADD COLUMN selected_attempt_id TEXT")
                version = 3
            if version != 3:
                raise RuntimeError(f"Unsupported database version {version}; expected 1, 2 or 3")
            db.execute("PRAGMA user_version=3")
            db.commit()

    @staticmethod
    def _migrate(db):
        for column, declaration in (
            ("object_type", "TEXT NOT NULL DEFAULT 'task'"),
            ("acceptance_criteria", "TEXT NOT NULL DEFAULT ''"),
            ("spec_revision", "INTEGER NOT NULL DEFAULT 1"),
            ("accepted_spec_revision", "INTEGER"),
            ("acceptance_note", "TEXT NOT NULL DEFAULT ''"),
            ("unresolved_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("parent_group_id", "TEXT REFERENCES tasks(id)"),
            ("order_key", "INTEGER NOT NULL DEFAULT 0"),
        ):
            db.execute(f"ALTER TABLE tasks ADD COLUMN {column} {declaration}")
        db.execute("UPDATE tasks SET order_key=rowid")
        db.execute(
            "UPDATE tasks SET accepted_spec_revision=1, "
            "acceptance_note='Migrated Auto task' WHERE kind='auto'"
        )
        statements = """
            CREATE TABLE project_paths (
                path TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id));
            CREATE TABLE workstreams (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                name TEXT NOT NULL, branch TEXT, checkout_path TEXT,
                revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                UNIQUE(project_id, name), UNIQUE(project_id, branch));
            CREATE TABLE scope_members (
                workstream_id TEXT NOT NULL REFERENCES workstreams(id),
                task_id TEXT NOT NULL REFERENCES tasks(id),
                PRIMARY KEY(workstream_id, task_id));
            CREATE TABLE scope_groups (
                workstream_id TEXT NOT NULL REFERENCES workstreams(id),
                group_id TEXT NOT NULL REFERENCES tasks(id),
                PRIMARY KEY(workstream_id, group_id));
            CREATE TABLE prerequisites (
                task_id TEXT NOT NULL REFERENCES tasks(id),
                blocked_by_id TEXT NOT NULL REFERENCES tasks(id),
                PRIMARY KEY(task_id, blocked_by_id));
            CREATE TABLE gate_proposals (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                gate_type TEXT NOT NULL, detail TEXT NOT NULL, proposer TEXT NOT NULL,
                created_at TEXT NOT NULL);
            CREATE TABLE attempts (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                workstream_id TEXT NOT NULL REFERENCES workstreams(id),
                implementer TEXT NOT NULL, summary TEXT NOT NULL, evidence TEXT NOT NULL,
                spec_revision INTEGER NOT NULL,
                state TEXT NOT NULL, reviewer TEXT, review_note TEXT,
                human_review_note TEXT, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX attempt_task ON attempts(task_id, created_at, id);
        """
        for statement in statements.split(";"):
            if statement.strip():
                db.execute(statement)
        for project in db.execute("SELECT * FROM projects").fetchall():
            if project["path"]:
                db.execute(
                    "INSERT INTO project_paths VALUES (?, ?)",
                    (project["path"], project["id"]),
                )
            tracks = db.execute(
                "SELECT DISTINCT track FROM tasks WHERE project_id=? UNION SELECT 'main'",
                (project["id"],),
            ).fetchall()
            for track in tracks:
                workstream_id = _id("wst_")
                db.execute(
                    "INSERT INTO workstreams VALUES (?, ?, ?, NULL, ?, 1, ?)",
                    (workstream_id, project["id"], track[0], project["path"], timestamp()),
                )
                db.execute(
                    "INSERT INTO scope_members SELECT ?, id FROM tasks "
                    "WHERE project_id=? AND track=?",
                    (workstream_id, project["id"], track[0]),
                )
                for legacy in db.execute(
                    """SELECT * FROM tasks WHERE project_id=? AND track=?
                    AND status IN ('review','signoff','rework') AND evidence!=''""",
                    (project["id"], track[0]),
                ).fetchall():
                    db.execute(
                        """INSERT INTO attempts VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            _id("att_"),
                            legacy["id"],
                            workstream_id,
                            "legacy-unknown",
                            "Migrated legacy implementation result",
                            legacy["evidence"],
                            1,
                            "rework" if legacy["status"] == "rework" else "review",
                            None,
                            legacy["review"] or None,
                            None,
                            1,
                            legacy["updated_at"],
                            legacy["updated_at"],
                        ),
                    )

    def _project(self, db, project, scope, create=False):
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
                project_key="path:" + canonical,
                name=Path(canonical).name,
                path=canonical,
                created_at=now,
            )
            db.execute(
                "INSERT INTO projects VALUES (:id,:project_key,:name,:path,:created_at)", project
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

    def list_workstreams(self, project):
        def operation(db, scope):
            selected = self._project(db, project, scope)
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM workstreams WHERE project_id=? ORDER BY created_at,id",
                    (selected["id"],),
                )
            ]
            for row in rows:
                row["scope"] = self._scope_ids(db, row["id"])
            return {"items": rows}

        return self._run("workstreams.listed", {"project": project}, operation)

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
            members, groups = self._scope_expression(db, selected["id"], scope_expression)
            ws = self._insert_workstream(db, selected["id"], name or branch, branch, canonical)
            self._set_scope(db, ws["id"], members, groups)
            scope["after"] = {
                "workstream": ws,
                "members": sorted(members),
                "groups": sorted(groups),
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
            return {"project": selected, "workstream": ws, "scope": self._scope_ids(db, ws["id"])}

        return self._run("workstream.preflight", request, operation)

    @staticmethod
    def _scope_ids(db, workstream_id):
        rows = db.execute(
            """SELECT task_id AS id FROM scope_members WHERE workstream_id=?
            UNION SELECT group_id AS id FROM scope_groups WHERE workstream_id=?
            UNION SELECT t.id FROM tasks t JOIN scope_groups s ON s.group_id=t.parent_group_id
            WHERE s.workstream_id=?""",
            (workstream_id, workstream_id, workstream_id),
        ).fetchall()
        return sorted(row["id"] for row in rows)

    @staticmethod
    def _set_scope(db, workstream_id, members, groups):
        db.execute("DELETE FROM scope_members WHERE workstream_id=?", (workstream_id,))
        db.execute("DELETE FROM scope_groups WHERE workstream_id=?", (workstream_id,))
        db.executemany(
            "INSERT INTO scope_members VALUES (?, ?)",
            [(workstream_id, member) for member in sorted(members)],
        )
        db.executemany(
            "INSERT INTO scope_groups VALUES (?, ?)",
            [(workstream_id, group) for group in sorted(groups)],
        )

    @staticmethod
    def _resolve_reference(db, project_id, term):
        row = db.execute(
            "SELECT id, object_type FROM tasks WHERE id=? AND project_id=?", (term, project_id)
        ).fetchone()
        if row:
            return row["id"], row["object_type"]
        rows = db.execute(
            "SELECT id, object_type FROM tasks WHERE title=? AND project_id=?", (term, project_id)
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
            return set(), set()
        members, groups = set(), set()
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
            tokens = tokens[1:]
        for token in tokens:
            if len(token) < 2 or token[0] not in "+-":
                raise TaskError("invalid_scope_expression: use +reference or -reference")
            identity, kind = self._resolve_reference(db, project_id, token[1:])
            target = groups if kind == "group" else members
            if token[0] == "+":
                target.add(identity)
            else:
                target.discard(identity)
        return members, groups

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
            }
            members, groups = self._scope_expression(db, before["project_id"], expression)
            self._set_scope(db, workstream_id, members, groups)
            db.execute("UPDATE workstreams SET revision=revision+1 WHERE id=?", (workstream_id,))
            scope.update(
                project_id=before["project_id"],
                before={"workstream": before, **previous},
                after={"members": sorted(members), "groups": sorted(groups)},
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
        task["group"] = task.pop("group_name")
        task["unresolved_items"] = json.loads(task.pop("unresolved_json"))
        task["accepted"] = task["accepted_spec_revision"] == task["spec_revision"]
        scope["project_id"] = task["project_id"]
        return task

    @staticmethod
    def _save_task(db, task):
        values = {
            **task,
            "group_name": task["group"],
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
        task["accepted"] = task["accepted_spec_revision"] == task["spec_revision"]
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
            task["members"] = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM tasks WHERE parent_group_id=? ORDER BY order_key,id",
                    (task["id"],),
                )
            ]
            task["complete"] = (
                bool(task["members"])
                and not db.execute(
                    "SELECT 1 FROM tasks WHERE parent_group_id=? AND status!='done' LIMIT 1",
                    (task["id"],),
                ).fetchone()
            )
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
            (id,project_id,title,body,kind,status,group_name,track,priority,evidence,review,
             revision,created_at,updated_at,object_type,acceptance_criteria,spec_revision,
             accepted_spec_revision,acceptance_note,unresolved_json,parent_group_id,order_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                project_id,
                title.strip(),
                body,
                "auto",
                "open",
                "General",
                "main",
                0,
                "",
                "",
                1,
                now,
                now,
                "task",
                acceptance_criteria,
                1,
                1 if accepted else None,
                note if accepted else "",
                "[]",
                parent_group_id,
                order,
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
                if group["project_id"] != project_id or group["object_type"] != "group":
                    raise TaskError("invalid_group")
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
            if scope == "workstream":
                db.execute("INSERT INTO scope_members VALUES (?, ?)", (workstream_id, task_id))
            task = self._details(db, self._task(db, task_id, event))
            event["after"] = task
            return task

        return self._run("task.created", request, operation)

    def add_task(
        self, project, title, body="", kind="design", group="General", track="main", priority=0
    ):
        raise TaskError("legacy_api_removed: use init_project and create_task")

    def update_task(self, task_id, expected_revision, changes):
        request = dict(task_id=task_id, expected_revision=expected_revision, changes=changes)

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
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
            if disposition not in {"open", "deferred", "dropped"} or not note.strip():
                raise TaskError("invalid_disposition: use open, deferred or dropped with a reason")
            if before["status"] == "done":
                raise TaskError("signoff_required: completed tasks cannot be reopened here")
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
            if before["object_type"] != "task":
                raise TaskError("invalid_prerequisite: use a concrete task")
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
            dependency = self._task(db, blocked_by_id, {})
            if before["project_id"] != dependency["project_id"] or task_id == blocked_by_id:
                raise TaskError("invalid_prerequisite")
            if dependency["object_type"] != "task" or before["object_type"] != "task":
                raise TaskError("invalid_prerequisite: use concrete tasks")
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
            if row["gate_type"] == "unresolved":
                after = {
                    **before,
                    "unresolved_items": before["unresolved_items"]
                    + [{"id": _id("unr_"), "text": row["detail"]}],
                }
            else:
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

    @staticmethod
    def _would_cycle(db, task_id, blocked_by_id):
        return bool(
            db.execute(
                """WITH RECURSIVE dependencies(id) AS (
            SELECT blocked_by_id FROM prerequisites WHERE task_id=?
            UNION SELECT p.blocked_by_id FROM prerequisites p JOIN dependencies d ON p.task_id=d.id)
            SELECT 1 FROM dependencies WHERE id=?""",
                (blocked_by_id, task_id),
            ).fetchone()
        )

    def decompose_task(self, task_id, expected_revision, members):
        request = dict(task_id=task_id, expected_revision=expected_revision, members=members)

        def operation(db, scope):
            before = self._task(db, task_id, scope)
            self._revision(before, expected_revision)
            if before["object_type"] != "task" or before["parent_group_id"]:
                raise TaskError("invalid_group: nested groups are not supported")
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
                    "SELECT id FROM tasks WHERE project_id=? ORDER BY order_key,id", (project_id,)
                )
            ]
            if expected_order != current:
                raise TaskError("revision_conflict: re-read the current project order")
            if len(ordered_ids) != len(current) or set(ordered_ids) != set(current):
                raise TaskError("invalid_order: include every project task exactly once")
            for index, task_id in enumerate(ordered_ids, 1):
                db.execute(
                    "UPDATE tasks SET order_key=?, revision=revision+1 WHERE id=?", (index, task_id)
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
                    AND child.status!='done')))
            LIMIT 1""",
                (task_id,),
            ).fetchone()
        )

    @staticmethod
    def _gate_reasons(db, task, workstream_id=None):
        reasons = []
        if task["object_type"] != "task" or task["status"] in {"done", "dropped", "deferred"}:
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
            scope["project_id"] = self._task(db, before["task_id"], {})["project_id"]
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
            scope["project_id"] = self._task(db, before["task_id"], {})["project_id"]
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
                "SELECT id FROM tasks WHERE project_id=? ORDER BY order_key,id", (project_id,)
            ):
                if ids is not None and row["id"] not in ids:
                    continue
                task = self._task(db, row["id"], {})
                reasons = self._gate_reasons(db, task, workstream_id)
                view = (
                    "done"
                    if task["status"] == "done"
                    else "group"
                    if task["object_type"] == "group"
                    else "ready"
                    if not reasons
                    else reasons[0]
                )
                if state and state != view:
                    continue
                result.append(
                    {
                        "id": task["id"],
                        "title": task["title"],
                        "revision": task["revision"],
                        "order_key": task["order_key"],
                        "view": view,
                        "accepted": task["accepted"],
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
