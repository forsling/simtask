"""Disposable workstreams and Git checkouts exercise continuation/integration.

Review and human decisions here are synthetic test records, not actual reviews.
The service does not verify Git; callers inspect commits/trees before recording.
"""

import sqlite3
import subprocess
import sys

import pytest

from task_mcp.store import STATE_WORDS, Store, TaskError


def detail(store, task_id):
    return store.get_tasks([task_id])["items"][0]


def create(store, context, title="Reject blank names", **fields):
    ack = store.create_task(
        context["project"]["id"],
        title,
        body="normalize_name strips names and raises ValueError for whitespace-only input.",
        acceptance_criteria="Nonblank names are stripped; empty/whitespace names raise ValueError.",
        workstream_id=context["workstream"]["id"],
        **fields,
    )
    return detail(store, ack["id"])


def git(path, *args, check=True):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Continuation Test",
            "-c",
            "user.email=continuation@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(path),
            *args,
        ],
        check=check,
        capture_output=True,
        text=True,
    )


def repository(path):
    path.mkdir()
    git(path, "init", "--initial-branch=origin")
    (path / "README").write_text("Disposable continuation proof\n")
    git(path, "add", "README")
    git(path, "commit", "-m", "Baseline")


def assert_view(store, context, task_id, view):
    ws = context["workstream"]["id"]
    project = context["project"]["id"]
    states = {"ready", "rework"} if view == "ready" else {STATE_WORDS.get(view, view)}
    if view == "out_of_scope":
        assert all(r["id"] != task_id for r in store.list_tasks(project, ws)["items"])
        card = store.read_tasks([task_id], workstream_id=ws, include=["ids"])["items"][0]
        assert card["view"] == view and card["state"] in states
        return
    board = store.list_tasks(project, ws, include=["ids"], include_inactive=True)["items"]
    row = next(r for r in board if r["id"] == task_id)
    assert row["view"] == view and row["state"] in states
    status = store.workstream_status(ws, include_inactive=True)
    assert next(r for r in status["items"] if r["id"] == task_id)["state"] in states
    resumed = store.init(
        context["workstream"]["checkout_path"],
        branch=context["workstream"]["branch"],
        include_inactive=True,
    )
    assert next(r for r in resumed["queue"] if r["id"] == task_id)["state"] in states


def record(store, work, context, implementer, evidence, artifacts=None, verification=None):
    ack = store.record_result(
        work["id"],
        context["workstream"]["id"],
        work["revision"],
        implementer,
        "Built",
        evidence,
        artifacts=artifacts or [{"kind": "artifact", "reference": __file__}],
        verification=verification or evidence,
        specification_etag=store.get_tasks([work["id"]])["items"][0]["specification_etag"],
    )

    return next(a for a in detail(store, work["id"])["attempts"] if a["id"] == ack["id"])


def test_competing_workstreams_and_superseded_attempts_do_not_gate_local_selection(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3")
    origin = store.init(str(tmp_path / "repo"), "origin", action="create_project", confirmed=True)
    work = create(store, origin)
    origin = store.init(str(tmp_path / "repo"), "origin")
    target = store.init(
        str(tmp_path / "repo"),
        "alternative",
        action="new_workstream",
        scope_expression=origin["workstream"]["id"],
        expected_revision=origin["scope_revision"],
        confirmed=True,
    )
    source = record(
        store,
        detail(store, work["id"]),
        origin,
        "origin builder",
        "Origin artifact and verification",
    )
    assert_view(store, origin, work["id"], "review")
    assert_view(store, target, work["id"], "ready")
    assert store.get_next_action(target["workstream"]["id"])["task"]["id"] == work["id"]
    store.record_review(source["id"], 1, "origin reviewer", "pass", "Synthetic source check")
    assert_view(store, origin, work["id"], "signoff")
    alternative = record(
        store, detail(store, work["id"]), target, "alternative builder", "Independent alternative"
    )
    assert alternative["state"] == "review" and alternative["reviewer"] is None
    assert_view(store, target, work["id"], "review")
    assert store.get_next_action(origin["workstream"]["id"])["task"] is None
    resumed_review = store.get_next_action(target["workstream"]["id"])
    assert (
        resumed_review["action"] == "review"
        and resumed_review["attempt"]["id"] == alternative["id"]
    )

    current = detail(store, work["id"])
    store.update_task(
        work["id"],
        current["revision"],
        {"title": "Reject blank names and retain spelling"},
    )
    assert_view(store, origin, work["id"], "ready")
    assert store.get_next_action(origin["workstream"]["id"])["task"]["id"] == work["id"]
    assert_view(store, target, work["id"], "ready")
    selected = store.get_next_action(target["workstream"]["id"])["task"]
    assert selected["spec_revision"] == 2 and "attempts" not in selected
    assert {
        (a["workstream_id"], a["spec_revision"]) for a in detail(store, work["id"])["attempts"]
    } == {(origin["workstream"]["id"], 1), (target["workstream"]["id"], 1)}


def test_resume_and_rebind_keep_scope_and_history_but_do_not_prove_checkout(tmp_path):
    source_path, moved_path = tmp_path / "source", tmp_path / "moved"
    repository(source_path)
    repository(moved_path)
    (source_path / "artifact.txt").write_text("Source-only implementation\n")
    git(source_path, "add", "artifact.txt")
    git(source_path, "commit", "-m", "Durable source result")
    source_commit = git(source_path, "rev-parse", "HEAD").stdout.strip()
    store = Store(tmp_path / "tasks.sqlite3")
    original = store.init(str(source_path), "origin", action="create_project", confirmed=True)
    work = create(store, original)
    excluded = create(store, original, "Excluded member")
    group = store.create_group(original["workstream"]["id"], "Live group")
    store.add_group_member(group["id"], 1, excluded["id"], excluded["revision"])
    ws = store.init(str(source_path), "origin")["workstream"]
    store.set_scope(ws["id"], ws["revision"], f"{ws['id']} -{excluded['id']}")
    attempt = record(
        store, work, original, "source builder", f"Actual source commit {source_commit}"
    )
    store.record_review(attempt["id"], 1, "source reviewer", "pass", "Synthetic source review")
    history = detail(store, work["id"])["attempts"]
    before = store.preflight(original["project"]["id"], str(source_path), "origin")
    with sqlite3.connect(store.path) as db:
        exclusions = db.execute("SELECT * FROM scope_exclusions").fetchall()

    # Reopening the Store models a new session/process, not a new workstream.
    store = Store(store.path)
    resumed = store.init(str(source_path), "origin")
    assert resumed["workstream"] == before["workstream"]
    assert detail(store, work["id"])["attempts"] == history
    moved = store.init(
        str(moved_path),
        "origin",
        action="rebind_workstream",
        workstream_id=ws["id"],
        expected_revision=resumed["workstream"]["revision"],
        confirmed=True,
    )
    after = store.preflight(original["project"]["id"], str(moved_path), "origin")
    assert after["scope"] == before["scope"] == [work["id"]]
    assert after["groups"] == before["groups"] == [group["id"]]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT * FROM scope_exclusions").fetchall() == exclusions
    assert moved["workstream"]["id"] == ws["id"]
    assert moved["workstream"]["checkout_path"] == str(moved_path)
    assert moved["workstream"]["revision"] == resumed["workstream"]["revision"] + 1
    assert store.init(str(moved_path), "origin")["workstream"] == moved["workstream"]
    assert detail(store, work["id"])["attempts"] == history
    assert_view(store, moved, work["id"], "signoff")
    # The recorded signoff view is not a claim that these files/commits moved.
    assert not (moved_path / "artifact.txt").exists()
    assert git(moved_path, "cat-file", "-e", source_commit, check=False).returncode != 0


def test_deliberate_cherry_pick_records_target_provenance_and_requires_fresh_review(tmp_path):
    source_path, target_path = tmp_path / "source", tmp_path / "target"
    repository(source_path)
    git(source_path, "worktree", "add", "-b", "target", str(target_path))
    (target_path / "target.txt").write_text("Independent target base\n")
    git(target_path, "add", "target.txt")
    git(target_path, "commit", "-m", "Target context")
    (source_path / "names.py").write_text(
        "def normalize_name(name):\n"
        "    value = name.strip()\n"
        "    if not value:\n"
        "        raise ValueError('blank name')\n"
        "    return value\n"
    )
    git(source_path, "add", "names.py")
    git(source_path, "commit", "-m", "Implement normalization")
    source_commit = git(source_path, "rev-parse", "HEAD").stdout.strip()
    store = Store(tmp_path / "tasks.sqlite3")
    origin = store.init(str(source_path), "origin", action="create_project", confirmed=True)
    work = create(store, origin)
    origin = store.init(str(source_path), "origin")
    target = store.init(
        str(target_path),
        "target",
        action="attach_workstream",
        project=origin["project"]["id"],
        scope_expression=origin["workstream"]["id"],
        expected_revision=origin["scope_revision"],
        confirmed=True,
    )
    source = record(
        store, detail(store, work["id"]), origin, "origin builder", f"Source commit {source_commit}"
    )
    reviewed_source = store.record_review(
        source["id"], 1, "origin reviewer", "pass", "Synthetic origin verification"
    )
    current = detail(store, work["id"])
    assert current["body"] == work["body"] and current["spec_revision"] == 1
    assert not (target_path / "names.py").exists()
    assert_view(store, target, work["id"], "ready")
    git(target_path, "cherry-pick", source_commit)
    target_commit = git(target_path, "rev-parse", "HEAD").stdout.strip()
    assert source_commit != target_commit
    assert git(target_path, "status", "--short").stdout == ""
    assert (target_path / "names.py").read_text() == (source_path / "names.py").read_text()
    verification = (
        "from names import normalize_name\n"
        "assert normalize_name(' Simon ') == 'Simon'\n"
        "for name in ('', '   ', '\\t\\n'):\n"
        "    try: normalize_name(name)\n"
        "    except ValueError: pass\n"
        "    else: raise AssertionError('blank name accepted')\n"
        "print('target normalization checks passed')\n"
    )
    verified = subprocess.run(
        [sys.executable, "-B", "-c", verification],
        cwd=target_path,
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = (
        f"Origin attempt {source['id']} in workstream {origin['workstream']['id']}; "
        f"integrated source commit {source_commit} by cherry-pick "
        f"to target commit {target_commit}; "
        f"target checkout {target_path}, current spec {current['spec_revision']}; "
        f"target verification python -B -c {verification!r}: {verified.stdout.strip()}"
    )
    integrated = record(
        store,
        current,
        target,
        "target integrator",
        evidence,
        artifacts=[
            {"kind": "commit", "reference": source_commit},
            {"kind": "commit", "reference": target_commit},
        ],
        verification=f"python -B -c {verification!r}: {verified.stdout.strip()}",
    )
    assert integrated["workstream_id"] == target["workstream"]["id"]
    assert integrated["evidence"] == evidence and integrated["spec_revision"] == 1
    assert integrated["state"] == "review"
    assert integrated["reviewer"] is integrated["review_note"] is None
    assert detail(store, work["id"])["attempts"][0] == reviewed_source
    assert_view(store, target, work["id"], "review")
    with pytest.raises(TaskError, match="review_required"):
        store.signoff_task(
            work["id"], current["revision"] + 1, "approve", "Synthetic verdict", integrated["id"], 1
        )
    store.record_review(
        integrated["id"],
        1,
        "fresh target reviewer",
        "pass",
        f"Synthetic target check: {target_commit}",
    )
    assert_view(store, target, work["id"], "signoff")
    store.signoff_task(
        work["id"],
        current["revision"] + 1,
        "approve",
        "Synthetic target approval",
        integrated["id"],
        2,
    )
    completed = detail(store, work["id"])
    assert completed["selected_attempt_id"] == integrated["id"] and completed["status"] == "done"
    with pytest.raises(TaskError, match="completed_task_immutable"):
        record(store, completed, origin, "later integrator", "Would rewrite completed proof")
    with pytest.raises(TaskError, match="completed_task_immutable"):
        store.update_task(work["id"], completed["revision"], {"title": "Later integration"})
    assert detail(store, work["id"]) == completed
    new_requirement = store.create_task(
        origin["project"]["id"],
        "Integrate into a later target",
        body=f"Integrate the completed result of {work['id']} into a new target checkout.",
        acceptance_criteria="Check the new target tree and record its provenance and verification.",
        workstream_id=origin["workstream"]["id"],
    )
    assert new_requirement["id"] != work["id"]
    assert detail(store, work["id"]) == completed
