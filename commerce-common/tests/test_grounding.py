# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The matching behind the grounding rules, over stand-in lexicons."""

import pytest

from commerce_common.grounding import (
    GroundingRule,
    find_token,
    first_forced_tool,
    matches_terms_and_cues,
)

TERMS = ("returns", "fee", "terms")
CUES = ("?", "how", "tell me")
PATTERNS = (r"\bSKU-\d{3}\b", r"\bSKU-[A-Z]{2,4}-\d{3}(?:-[A-Z]{2})?\b")


@pytest.mark.parametrize(
    ("text", "fires"),
    [
        ("how do returns work", True),
        ("fee?", True),
        ("returns", False),  # a term without a cue
        ("how are you", False),  # a cue without a term
        ("how does this coffee taste", False),  # "fee" only inside a word
        ("what determines the price?", False),  # "terms" only inside a word
        ("", False),
    ],
)
def test_a_whole_word_term_and_a_cue_must_both_appear(text, fires):
    assert matches_terms_and_cues(text, TERMS, CUES) is fires


def test_an_empty_lexicon_never_fires():
    assert not matches_terms_and_cues("how do returns work?", (), CUES)
    assert not matches_terms_and_cues("how do returns work?", TERMS, ())


@pytest.mark.parametrize(
    ("text", "fires"),
    [
        ("take it from $29 down to $26", True),
        ("cut it by 15 % this weekend", True),
        ("it is $29 right now", False),  # an amount without a cue
        ("take the afternoon off", False),  # a cue without a term or an amount
    ],
)
def test_money_and_percent_figures_stand_in_for_a_term_when_asked(text, fires):
    assert matches_terms_and_cues(text, ("price",), ("take", "cut"), numeric_literals=True) is fires
    assert not matches_terms_and_cues(text, ("price",), ("take", "cut"))


@pytest.mark.parametrize(
    ("text", "token"),
    [
        ("add SKU-102 please", "SKU-102"),
        ("is sku-102 in stock", "sku-102"),
        ("transfer SKU-TIX-104-GA to me", "SKU-TIX-104-GA"),  # the longest match wins
        ("order SKU-10234 arrived", None),  # a longer number is a different kind of id
        ("does it charge over USB-C?", None),
        ("", None),
    ],
)
def test_find_token_returns_the_longest_case_insensitive_match(text, token):
    assert find_token(text, PATTERNS) == token


def test_find_token_without_patterns_returns_none():
    assert find_token("add SKU-102 please", ()) is None


def test_first_forced_tool_follows_rule_order_and_skips_rules_that_do_not_fire():
    rules = (
        GroundingRule(
            "terms", "read_terms", lambda config, text, state: {} if "terms" in text else None
        ),
        GroundingRule(
            "ids", "read_id", lambda config, text, state: {"id": text} if "SKU" in text else None
        ),
    )
    assert first_forced_tool(rules, None, "terms for SKU-1?", None) == "read_terms"
    assert first_forced_tool(rules, None, "SKU-1?", None) == "read_id"
    assert first_forced_tool(rules, None, "hello", None) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("compare SKU-AA-101 with SKU-LONG-102-ZZ", "SKU-LONG-102-ZZ"),
        ("compare SKU-LONG-102-ZZ with SKU-AA-101", "SKU-LONG-102-ZZ"),
        ("compare sku-aa-101 with sku-long-102-zz", "sku-long-102-zz"),
        ("compare SKU-AA-101 with SKU-BB-102", "SKU-AA-101"),
    ],
)
def test_find_token_checks_later_occurrences_of_the_same_pattern(text, expected):
    assert find_token(text, PATTERNS) == expected
