"""Read the canonical, packaged reference workflow files without writing clients."""

import hashlib
from importlib.resources import files

REFERENCE_VERSION = "1.14.0"
SKILLS = ("init", "feature-capture", "feature-design", "proposal-review", "superdevloop", "signoff")


def default_skills(name=None):
    if name is not None and name not in SKILLS:
        raise ValueError("unknown_skill: use a name from the skill index")
    root = files("task_mcp").joinpath("reference_skills")
    items = []
    for skill_name in SKILLS:
        if name is not None and skill_name != name:
            continue
        content = root.joinpath(skill_name, "SKILL.md").read_text(encoding="utf-8")
        items.append(
            {
                "name": skill_name,
                "path": f"{skill_name}/SKILL.md",
                "version": REFERENCE_VERSION,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "description": next(
                    line.removeprefix("description: ")
                    for line in content.splitlines()
                    if line.startswith("description: ")
                ),
                **({"content": content} if name is not None else {}),
            }
        )
    return {"version": REFERENCE_VERSION, "items": items}
