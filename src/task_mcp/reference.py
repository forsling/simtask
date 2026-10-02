"""Read the canonical, packaged reference workflow files without writing clients."""

import hashlib
from importlib.resources import files

REFERENCE_VERSION = "1.5.0"
SKILLS = ("init", "feature-capture", "feature-design", "proposal-review", "superdevloop", "signoff")


def default_skills():
    root = files("task_mcp").joinpath("reference_skills")
    items = []
    for name in SKILLS:
        content = root.joinpath(name, "SKILL.md").read_text(encoding="utf-8")
        items.append(
            {
                "name": name,
                "path": f"{name}/SKILL.md",
                "version": REFERENCE_VERSION,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "content": content,
            }
        )
    return {"version": REFERENCE_VERSION, "items": items}
