"""The viewer's Unassigned view: a project's tasks with zero effective memberships."""

import pytest

from simtask.store import Store, TaskError
from simtask.viewer import APP_PATH, dispatch


def revision(store, task_id):
    return store.get_tasks([task_id])["items"][0]["revision"]


def listing(store, project, limit=100, **extra):
    """Every page of the Unassigned listing, as (ids, pages)."""
    ids, pages, offset = [], [], 0
    while offset is not None:
        page = store.list_tasks(project, limit=limit, offset=offset, unassigned=True, **extra)
        pages.append(page)
        ids += [item["id"] for item in page["items"]]
        offset = page["next_offset"]
    return ids, pages


def memberless(store, project):
    """The reference answer: tasks whose details list no effective membership."""
    ids, offset = [], 0
    while offset is not None:
        page = store.list_tasks(project, limit=100, offset=offset, include_inactive=True)
        ids += [item["id"] for item in page["items"]]
        offset = page["next_offset"]
    return [i for i in ids if not store.get_tasks([i])["items"][0]["workstream_ids"]]


@pytest.fixture
def seeded(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="simon")
    repo = str(tmp_path / "repo")
    made = store.init(repo, "main", action="create_project", confirmed=True)
    project, main = made["project"]["id"], made["workstream"]["id"]
    side = store.init(repo, "side", action="new_workstream", confirmed=True)["workstream"]["id"]
    old = store.init(repo, "old", action="new_workstream", confirmed=True)["workstream"]["id"]
    other = store.init(str(tmp_path / "other"), "main", action="create_project", confirmed=True)
    ids = {}

    def task(name, workstream=None, target=project):
        ids[name] = store.create_task(target, name, workstream_id=workstream)["id"]
        return ids[name]

    task("direct", main)
    task("plain")
    # Members of a group included in side: inherited unless excluded there.
    group = store.create_group(side, "Group")["id"]
    for name in ("inherited", "excluded"):
        task(name)
        store.add_group_member(group, revision(store, group), ids[name], revision(store, ids[name]))
    store.remove_from_workstream(ids["excluded"], side, revision(store, ids["excluded"]))
    # A group whose inclusion is excluded in its only workstream confers nothing.
    dead = store.create_group(main, "Excluded group")["id"]
    task("dead_member")
    store.add_group_member(
        dead, revision(store, dead), ids["dead_member"], revision(store, ids["dead_member"])
    )
    store.remove_from_workstream(dead, main, revision(store, dead))
    # Only in an archived workstream: archiving keeps the membership.
    task("archived_only", old)
    ws = store.list_workstreams(project, include_archived=True)["items"]
    old_rev = next(w for w in ws if w["id"] == old)
    store.archive_workstream(old, old_rev.get("archive", {}).get("revision", 0), "Stale")
    # Removed from its last workstream.
    task("removed", main)
    store.remove_from_workstream(ids["removed"], main, revision(store, ids["removed"]))
    task("deferred")
    store.set_disposition(ids["deferred"], revision(store, ids["deferred"]), "deferred", "Later")
    ids["idea"] = store.capture_idea(project, "Quick thought")["id"]
    task("elsewhere", target=other["project"]["id"])
    return store, project, {"main": main, "side": side, "old": old}, ids


def test_lists_exactly_the_tasks_with_no_effective_membership(seeded):
    store, project, _, ids = seeded
    found, pages = listing(store, project, include_inactive=True)
    expected = ["plain", "excluded", "dead_member", "removed", "deferred", "idea"]
    assert set(found) == {ids[name] for name in expected}
    assert found == memberless(store, project)
    for name in ("direct", "inherited", "archived_only", "elsewhere"):
        assert ids[name] not in found
    assert pages[0]["placement"] == "unassigned"
    assert pages[0]["total"] == len(expected)
    # Without include_inactive, closed tasks are only counted, as on other boards.
    active, active_pages = listing(store, project)
    assert ids["deferred"] not in active
    assert active_pages[0]["hidden"] == {"deferred": 1}
    assert active_pages[0]["total"] == len(expected) - 1


def test_archived_membership_keeps_its_meaning(seeded):
    store, project, streams, ids = seeded
    detail = store.get_tasks([ids["archived_only"]])["items"][0]
    assert detail["workstream_ids"] == [streams["old"]]
    assert ids["archived_only"] not in listing(store, project, include_inactive=True)[0]


def test_membership_changes_show_on_the_next_read(seeded):
    store, project, streams, ids = seeded
    store.add_to_workstream(ids["plain"], streams["side"], revision(store, ids["plain"]))
    assert ids["plain"] not in listing(store, project)[0]
    store.remove_from_workstream(ids["direct"], streams["main"], revision(store, ids["direct"]))
    assert ids["direct"] in listing(store, project)[0]
    # Excluded from the group's workstream, then added back: assigned again.
    store.add_to_workstream(ids["excluded"], streams["side"], revision(store, ids["excluded"]))
    assert ids["excluded"] not in listing(store, project)[0]
    assert listing(store, project, include_inactive=True)[0] == memberless(store, project)


def test_pages_cover_every_match_when_assigned_tasks_interleave(tmp_path):
    store = Store(tmp_path / "tasks.sqlite3", actor="simon")
    made = store.init(str(tmp_path / "r"), "main", action="create_project", confirmed=True)
    project, main = made["project"]["id"], made["workstream"]["id"]
    expected = []
    for n in range(23):
        assigned = n % 3 != 1
        created = store.create_task(project, f"Task {n}", workstream_id=main if assigned else None)
        if not assigned:
            expected.append(created["id"])
    for limit in (1, 2, 3, 5, 100):
        found, pages = listing(store, project, limit=limit, include_inactive=True)
        assert found == expected, limit
        # Each page but the last is full of matches; no assigned task takes a place.
        assert all(len(p["items"]) == limit for p in pages[:-1])
        assert all(p["total"] == len(expected) for p in pages)


def test_viewer_dispatch_gives_full_cards_with_project_wide_standings(seeded):
    store, project, _, ids = seeded
    board = dispatch(store, "tasks", {"project": project, "unassigned": True, "limit": 100})
    assert {item["id"] for item in board["items"]} == set(memberless(store, project))
    for item in board["items"]:
        assert item["workstream_ids"] == []
        detail = dispatch(store, "details", {"ids": [item["id"]]})["items"][0]
        assert item["standing"] == detail["standing"]
    standings = {item["id"]: item["standing"] for item in board["items"]}
    assert standings[ids["idea"]] == "decision"
    assert standings[ids["deferred"]] == "deferred"
    assert standings[ids["plain"]] == "open"
    # All tasks still lists unassigned tasks, with their empty memberships.
    everything = dispatch(store, "tasks", {"project": project, "limit": 100})["items"]
    assert set(standings) <= {item["id"] for item in everything}


@pytest.mark.parametrize(
    "extra",
    [{"state": "ready"}, {"group_id": "x"}, {"workstream_id": "x"}, {"unassigned": "yes"}],
)
def test_unassigned_takes_only_a_project(seeded, extra):
    store, project, _, _ = seeded
    with pytest.raises(TaskError, match="invalid_unassigned"):
        store.list_tasks(project, **({"unassigned": True} | extra))
    with pytest.raises(TaskError, match="invalid_unassigned"):
        store.list_tasks(None, unassigned=True)


def test_unassigned_location_paths_are_served():
    for path in (
        "/p/1c4684b6/u",
        "/p/1c4684b6/u/t/a5dfba02",
        "/p/1c4684b6/u/t/id/readable-task-ids",
    ):
        assert APP_PATH.fullmatch(path), path
    assert not APP_PATH.fullmatch("/p/1c4684b6/u/g")
    assert not APP_PATH.fullmatch("/w/1c4684b6/u")
