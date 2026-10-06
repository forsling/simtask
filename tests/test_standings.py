"""One standing rule (Store._standing) for the viewer's board, task detail and sidebar."""

import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from task_mcp.store import STANDINGS, Store
from task_mcp.viewer import ASSETS, dispatch

NEEDS_INPUT = ("signoff", "decision")


def record(store, task_id, workstream_id):
    task = store.get_tasks([task_id])["items"][0]
    return store.record_result(
        task_id,
        workstream_id,
        task["revision"],
        "worker",
        "Done",
        "Proof",
        [{"kind": "artifact", "reference": "tests/test_standings.py"}],
        "Checked",
        task["specification_etag"],
    )


def review(store, attempt, verdict):
    return store.record_review(attempt["id"], attempt["revision"], "checker", verdict, "Noted")


def revision(store, task_id):
    return store.get_tasks([task_id])["items"][0]["revision"]


@pytest.fixture
def seeded(tmp_path):
    """Every case the sidebar and sections must agree on, in three workstreams."""
    store = Store(tmp_path / "tasks.sqlite3", actor="simon")
    repo = str(tmp_path / "repo")
    made = store.init(repo, "main", action="create_project", confirmed=True)
    project, main = made["project"]["id"], made["workstream"]["id"]
    side = store.init(repo, "side", action="new_workstream", confirmed=True)["workstream"]["id"]
    quiet = store.init(repo, "quiet", action="new_workstream", confirmed=True)["workstream"]["id"]

    def task(title, workstream=main):
        return store.create_task(project, title, workstream_id=workstream)["id"]

    ids = {}
    # Picked first, so get_next_action hands it out in side.
    ids["picked"] = task("Picked by an agent", side)
    store.get_next_action(side)
    ids["question_review"] = task("Question and a result under review")
    record(store, ids["question_review"], main)
    store.add_unresolved(ids["question_review"], revision(store, ids["question_review"]), "Which?")
    ids["passed"] = task("Passed review")
    review(store, record(store, ids["passed"], main), "pass")
    # Shared with side, where it has no result: it stands differently there.
    store.add_to_workstream(ids["passed"], side, revision(store, ids["passed"]))
    ids["blocker"] = task("Open blocker")
    ids["passed_blocked"] = task("Passed with an open blocker")
    review(store, record(store, ids["passed_blocked"], main), "pass")
    store.add_prerequisite(
        ids["passed_blocked"], revision(store, ids["passed_blocked"]), ids["blocker"]
    )
    ids["review"] = task("Under review")
    record(store, ids["review"], main)
    ids["rework"] = task("Sent back")
    review(store, record(store, ids["rework"], main), "rework")
    ids["brief"] = task("Design brief")
    store.add_unresolved(ids["brief"], revision(store, ids["brief"]), "Scope?")
    ids["deferred"] = task("Deferred with a question")
    store.add_unresolved(ids["deferred"], revision(store, ids["deferred"]), "Later?")
    store.set_disposition(ids["deferred"], revision(store, ids["deferred"]), "deferred", "Later")
    ids["dropped"] = task("Dropped")
    store.set_disposition(ids["dropped"], revision(store, ids["dropped"]), "dropped", "No")
    ids["quiet"] = task("Nothing needs input here", quiet)
    ids["idea"] = store.capture_idea(project, "Inbox idea")["id"]
    group = store.create_group(main, "A group", project=project)
    return store, project, {"main": main, "side": side, "quiet": quiet}, ids, group["id"]


EXPECTED = {
    "main": {
        "question_review": "decision",
        "passed": "signoff",
        "blocker": "open",
        "passed_blocked": "signoff",
        "review": "progress",
        "rework": "progress",
        "brief": "decision",
        "deferred": "deferred",
        "dropped": "dropped",
    },
    "side": {"picked": "progress", "passed": "open"},
    "quiet": {"quiet": "open"},
    None: {"picked": "progress", "passed": "signoff", "idea": "decision"},
}


def board(store, project, workstream=None):
    items, offset = [], 0
    while offset is not None:
        page = dispatch(
            store,
            "tasks",
            {"project": project, "workstream_id": workstream, "limit": 2, "offset": offset},
        )
        items += page["items"]
        offset = page["next_offset"]
    return items


def test_board_detail_and_sidebar_share_one_standing(seeded):
    store, project, streams, ids, _ = seeded
    listed = dispatch(store, "workstreams", {"project": project, "include_archived": True})
    by_id = {row["id"]: row["status"] for row in listed["items"]}
    for name, expected in EXPECTED.items():
        workstream = streams.get(name)
        cards = board(store, project, workstream)
        standings = {card["id"]: card["standing"] for card in cards}
        for key, standing in expected.items():
            assert standings[ids[key]] == standing, (name, key)
        for card in cards:
            # The detail stands the task by the same rule, for the same results in view.
            detail = dispatch(
                store,
                "details",
                {"ids": [card["id"]], **({"workstream_id": workstream} if workstream else {})},
            )["items"][0]
            assert detail["standing"] == card["standing"], (name, card["title"])
        if workstream:
            # The sidebar's per-workstream counts are exactly the board's standings.
            counts = by_id[workstream]["standings"]
            assert set(counts) == set(STANDINGS)
            assert {k: v for k, v in counts.items() if v} == dict(Counter(standings.values()))
    needs = {
        name: sum(by_id[workstream]["standings"][s] for s in NEEDS_INPUT)
        for name, workstream in streams.items()
    }
    assert needs == {"main": 4, "side": 0, "quiet": 0}


def test_standing_counts_stay_out_of_mcp_outputs(seeded):
    store, project, streams, ids, _ = seeded
    assert "standings" not in store.list_workstreams(project)["items"][0]["status"]
    assert "standings" not in store.workstream_status(streams["main"])["status"]
    assert "standing" not in store.list_tasks(project, streams["main"])["items"][0]
    assert "standing" not in store.get_tasks([ids["passed"]])["items"][0]
    with pytest.raises(Exception, match="workstream"):
        dispatch(store, "details", {"ids": [ids["passed"]], "workstream_id": "wst_" + "0" * 32})


def test_viewer_has_no_standing_rule_of_its_own():
    """The JavaScript only sections and labels the server's standings."""
    source = (ASSETS / "app.js").read_text()
    # The rule's inputs never reach the viewer's own logic.
    for field in ("attempt_counts", "aggregate_attempt_counts", "unresolved_count", r"\.picked\b"):
        assert not re.search(field, source), field
    sections = re.search(r"const SECTIONS = \[(.*?)\n\];", source, re.S).group(1)
    shown = re.findall(r"standings: \[([^\]]*)\]", sections)
    names = [name for group in shown for name in re.findall(r'"(\w+)"', group)]
    assert sorted(names) == sorted(STANDINGS), "every server standing has exactly one section"
    attention = re.findall(r"standings: \[([^\]]*)\], attention: true", sections)
    assert sorted(re.findall(r'"(\w+)"', "".join(attention))) == sorted(NEEDS_INPUT)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for frontend regression")
def test_shipped_viewer_sidebar_equals_its_sections_on_real_server_data(seeded, tmp_path):
    store, project, streams, _, _ = seeded
    listed = dispatch(store, "workstreams", {"project": project, "include_archived": True})
    fixture = {
        "project": project,
        "projects": dispatch(store, "projects", {})["items"],
        "workstreams": listed,
        "boards": {
            workstream or "": dispatch(
                store,
                "tasks",
                {"project": project, "workstream_id": workstream, "limit": 100, "offset": 0},
            )
            for workstream in [None, *streams.values()]
        },
        "groups": dispatch(store, "groups", {"project": project, "limit": 100, "offset": 0}),
        "expected": {"main": 4, "side": 0, "quiet": 0},
    }
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))
    script = Path(__file__).with_name("viewer_counts.test.cjs")
    result = subprocess.run(
        ["node", str(script), str(path)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "viewer counts ok" in result.stdout
