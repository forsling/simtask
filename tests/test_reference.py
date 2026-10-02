import hashlib
from importlib.resources import files

from task_mcp.reference import default_skills


def test_catalog_is_exact_canonical_on_disk_content():
    catalog = default_skills()
    assert catalog["version"] == "1.5.0"
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
        assert item["content"].encode() == contents
        assert item["sha256"] == hashlib.sha256(contents).hexdigest()
        assert item["version"] == catalog["version"]
