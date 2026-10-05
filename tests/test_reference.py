import hashlib
from importlib.resources import files

import pytest

from task_mcp.reference import default_skills


def test_catalog_is_exact_canonical_on_disk_content():
    catalog = default_skills()
    assert catalog["version"] == "1.17.0"
    assert {item["name"] for item in catalog["items"]} == {
        "init",
        "feature-capture",
        "feature-design",
        "proposal-review",
        "superdevloop",
        "signoff",
    }
    root = files("task_mcp").joinpath("reference_skills")
    for item in catalog["items"]:
        contents = root.joinpath(item["path"]).read_bytes()
        assert "content" not in item and item["description"]
        named = default_skills(item["name"])
        assert len(named["items"]) == 1
        content = named["items"][0]["content"]
        assert content.encode() == contents
        frontmatter = content.split("---", 2)[1]
        assert f"name: task-mcp-{item['name']}\n" in frontmatter
        assert f"description: {item['description']}\n" in frontmatter
        assert item["sha256"] == hashlib.sha256(contents).hexdigest()
        assert item["version"] == catalog["version"]


def test_unknown_skill_does_not_return_an_unrelated_workflow():
    with pytest.raises(ValueError, match="unknown_skill"):
        default_skills("unknown")
