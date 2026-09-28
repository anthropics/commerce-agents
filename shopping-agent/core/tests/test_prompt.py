# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime

from shopping_agent import Cart, CartItem, PageContext, UserPreferences
from shopping_agent.prompt import build_dynamic_context, build_static_system


def test_static_system_mentions_brand_and_skills(config, skills):
    text = build_static_system(config, skills)
    assert "ACME" in text
    assert "search-discovery" in text
    assert "storefront_data" in text  # the fence notice is present


def test_domain_search_notes_render_only_when_configured(config, skills):
    baseline = build_static_system(config, skills)
    assert "travel_date" not in baseline

    note = "Stays are date-bound: pass the travel date as filters.attributes['travel_date']."
    dated = config.model_copy(update={"domain_search_notes": note})
    text = build_static_system(dated, skills)
    assert f"\n- {note}" in text
    assert text.replace(f"\n- {note}", "") == baseline


def test_checkout_can_be_disabled_while_cart_remains_available(config, skills):
    cart_only = config.model_copy(update={"enable_cart": True, "enable_checkout": False})
    text = build_static_system(cart_only, skills)

    assert "checkout" in cart_only.absent_tools()
    assert "add_to_cart" not in cart_only.absent_tools()
    assert "checkout stages a summary" not in text
    assert "store has no checkout here" in text


def test_dynamic_context_is_fenced_and_contains_session_data():
    prefs = UserPreferences(user_id="u-1", display_name="Priya", preferences={"budget": "low"})
    cart = Cart(items=[CartItem(product_id="p-100", title="Tent", price=149.0, quantity=1)])
    block = build_dynamic_context(
        preferences=prefs,
        memory_facts=[],
        cart=cart,
        page=PageContext(page_type="product", product_id="p-100"),
        now=datetime(2026, 5, 30, 10, 37),
    )
    assert block.startswith("# Session context")
    assert "<storefront_data>" in block
    assert "Priya" in block
    assert "p-100" in block
    assert "2026-05-30T10:00" in block
    without_clock = build_dynamic_context(
        preferences=prefs, memory_facts=[], cart=cart, page=None, now=None
    )
    assert "local_time" not in without_clock


def test_dynamic_context_account_block():
    prefs = UserPreferences(user_id="u-1", display_name="Riley")
    account = {
        "current_plan": "Essential 5GB",
        "contract_end": "2026-08-01",
        "upgrade_eligibility": {"eligible": True, "reason": "month 22 of 24"},
    }
    block = build_dynamic_context(
        preferences=prefs, memory_facts=[], cart=None, page=None, account=account
    )
    assert '"account"' in block
    assert "Essential 5GB" in block
    assert "month 22 of 24" in block
    assert block.index('"customer"') < block.index('"account"')


def test_dynamic_context_without_account_has_no_account_key():
    block = build_dynamic_context(
        preferences=None, memory_facts=[], cart=None, page=None, account=None
    )
    assert '"account"' not in block


def test_dynamic_context_oversize_account_collapses_to_note():
    huge = {"history": "x" * 5000}
    block = build_dynamic_context(
        preferences=None,
        memory_facts=[],
        cart=None,
        page=None,
        account=huge,
        account_max_chars=2000,
    )
    assert "account context omitted (too large)" in block
    assert "xxxx" not in block


def test_static_system_states_account_trust_rule(config, skills):
    text = build_static_system(config, skills)
    assert "computed by the store's systems" in text
