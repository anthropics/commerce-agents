# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Skill loading. A skill is a directory holding ``SKILL.md``: YAML frontmatter with
``name`` and ``description``, then the instructions. The static prompt carries the index
and ``load_skill`` returns a body on demand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


class SkillLoadError(ValueError):
    pass


def parse_skill_md(text: str, path: Path | None = None) -> Skill:
    fence = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)
    opening = fence.match(text)
    if opening is None:
        raise SkillLoadError(f"{path or 'SKILL.md'}: missing YAML frontmatter")
    closing = fence.search(text, opening.end())
    if closing is None:
        raise SkillLoadError(f"{path or 'SKILL.md'}: malformed frontmatter fences")
    frontmatter = text[opening.end() : closing.start()]
    body = text[closing.end() :]
    try:
        meta = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"{path or 'SKILL.md'}: invalid YAML frontmatter") from exc
    if not isinstance(meta, dict):
        raise SkillLoadError(f"{path or 'SKILL.md'}: frontmatter must be a mapping")
    name = meta.get("name")
    description = meta.get("description")
    if not name or not description:
        raise SkillLoadError(f"{path or 'SKILL.md'}: frontmatter needs `name` and `description`")
    return Skill(name=str(name), description=str(description).strip(), body=body.strip())


def load_skill_dir(skill_dir: Path) -> Skill:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise SkillLoadError(f"{skill_dir}: no SKILL.md found")
    return parse_skill_md(skill_md.read_text(encoding="utf-8"), path=skill_md)


def load_skills(skills_root: Path) -> list[Skill]:
    """Every skill directory under ``skills_root``; names must be unique."""
    skills = [
        load_skill_dir(child)
        for child in sorted(skills_root.iterdir())
        if child.is_dir() and (child / "SKILL.md").exists()
    ]
    names = [skill.name for skill in skills]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise SkillLoadError(f"duplicate skill names: {sorted(duplicates)}")
    return skills


class SkillRegistry:
    """The loaded skills, sorted by name so the index renders the same bytes every time."""

    def __init__(self, skills: list[Skill]):
        self._skills = sorted(skills, key=lambda skill: skill.name)
        self._by_name = {skill.name: skill for skill in self._skills}

    @classmethod
    def from_dir(cls, skills_root: Path) -> SkillRegistry:
        return cls(load_skills(skills_root))

    @property
    def names(self) -> list[str]:
        return [skill.name for skill in self._skills]

    def index_block(self) -> str:
        if not self._skills:
            return "(no skills installed)"
        return "\n".join(f"- `{skill.name}` — {skill.description}" for skill in self._skills)

    def get_instructions(self, name: str) -> str | None:
        skill = self._by_name.get(name)
        return None if skill is None else skill.body
