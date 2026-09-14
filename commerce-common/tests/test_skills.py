# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import pytest

from commerce_common.skills import SkillLoadError, SkillRegistry, load_skill_dir, parse_skill_md

SKILL_MD = """---
name: search-discovery
description: Finding and choosing products across multi-constraint requests.
---

# Search & discovery

Ground every pick in search results.
"""


def test_parse_skill_md_extracts_frontmatter_and_body():
    skill = parse_skill_md(SKILL_MD)
    assert skill.name == "search-discovery"
    assert skill.description.startswith("Finding and choosing")
    assert skill.body.startswith("# Search & discovery")


def test_parse_skill_md_requires_frontmatter():
    with pytest.raises(SkillLoadError):
        parse_skill_md("# no frontmatter here")
    with pytest.raises(SkillLoadError):
        parse_skill_md("---\nname: only-name\n---\nbody")


def test_load_skill_dir_serves_the_body_through_the_registry(tmp_path):
    skill_dir = tmp_path / "gift-finding"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: gift-finding\ndescription: Finding gifts for a recipient.\n---\nBody here."
    )
    registry = SkillRegistry([load_skill_dir(skill_dir)])
    assert registry.get_instructions("gift-finding") == "Body here."


def test_registry_index_is_sorted_and_stable(skills):
    index_a = skills.index_block()
    index_b = skills.index_block()
    assert index_a == index_b
    assert index_a.index("planning-goals") < index_a.index("search-discovery")
    assert skills.get_instructions("nope") is None


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_frontmatter_delimiters_in_yaml_and_body(newline):
    text = newline.join(
        ["---", "name: example", 'description: "before --- after"', "---", "Body", "---", "Tail"]
    )
    skill = parse_skill_md(text)
    assert skill.description == "before --- after"
    assert skill.body == newline.join(["Body", "---", "Tail"])


@pytest.mark.parametrize(
    "text",
    [
        "---oops\nname: example\ndescription: Example\n---\nBody",
        "---\nname: example\ndescription: Example --- Body",
        "---\n- a\n- b\n---\nBody",
        "---\n42\n---\nBody",
        "---\nname: [broken\n---\nBody",
    ],
)
def test_invalid_frontmatter_raises_skill_load_error(text):
    with pytest.raises(SkillLoadError):
        parse_skill_md(text)
