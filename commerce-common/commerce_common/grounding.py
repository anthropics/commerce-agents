# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Grounding rules: a rule reads the user's message and names one read tool the turn
must start with, so an answer of that shape begins from a tool result. Each role lists
its rules in precedence order; the runtimes force the first rule that fires, and hosts
without tool choice prefetch it. The lexicons are config; this module only matches.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

# A concrete money or percent figure counts as a change term on its own.
_MONEY_LITERAL = re.compile(r"\$\s?\d")
_PERCENT_LITERAL = re.compile(r"\d+\s?%")


def matches_any(text: str, needles: Sequence[str]) -> bool:
    """Case-insensitive whole-word (or whole-phrase) match; ``?`` matches literally."""
    lowered = text.lower()
    for needle in needles:
        cleaned = needle.lower().strip()
        if not cleaned:
            continue
        if cleaned == "?":
            if "?" in lowered:
                return True
        elif re.search(rf"\b{re.escape(cleaned)}\b", lowered):
            return True
    return False


def matches_terms_and_cues(
    text: str, terms: Sequence[str], cues: Sequence[str], *, numeric_literals: bool = False
) -> bool:
    """True when the text carries a term and a cue. With ``numeric_literals`` a money or
    percent figure also counts as a term. Empty text or an empty lexicon never fires."""
    if not text or not terms or not cues or not matches_any(text, cues):
        return False
    if matches_any(text, terms):
        return True
    return numeric_literals and bool(_MONEY_LITERAL.search(text) or _PERCENT_LITERAL.search(text))


def find_token(text: str, patterns: Sequence[str]) -> str | None:
    """The longest match of any pattern in the text (case-insensitive), or None."""
    token: str | None = None
    for pattern in patterns if text else ():
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if token is None or len(match.group(0)) > len(token):
                token = match.group(0)
    return token


FiresFn = Callable[[Any, str, Any], "dict[str, Any] | None"]


@dataclass(frozen=True)
class GroundingRule:
    """``fires(config, text, state)`` returns the input for ``tool`` when the rule
    applies, else None. ``prefetch_intro`` renders the line a prefetching host puts
    above the tool result; a rule without one is honored only where the runtime can
    force the tool, because its input is the model's to write."""

    name: str
    tool: str
    fires: FiresFn
    prefetch_intro: Callable[[dict[str, Any]], str] | None = None


def first_forced_tool(
    rules: Sequence[GroundingRule], config: Any, text: str, state: Any
) -> str | None:
    """The tool the turn's first iteration is pinned to, by rule precedence."""
    for rule in rules:
        if rule.fires(config, text, state) is not None:
            return rule.tool
    return None
