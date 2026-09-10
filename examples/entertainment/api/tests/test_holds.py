# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Holds are created by adds, expired by the clock, capped per event, and read live."""

import pytest

from entertainment.api.ticketing import (
    HOLD_TTL_S,
    MAX_TICKETS_PER_EVENT,
    HoldLimitError,
    SoldOutError,
    StateError,
)

PIT = "AT-TIX-101-PIT"  # capacity 350, sold 344, 6 open


async def test_add_to_cart_creates_a_timed_hold(backend, session, clock):
    await backend.add_to_cart(session, PIT, 2)
    holds = backend.engine.holds_for_session(session.session_id)
    assert len(holds) == 1
    assert holds[0].quantity == 2
    assert holds[0].user_id == "demo-user"
    assert (holds[0].expires_at - clock()).total_seconds() == HOLD_TTL_S
    cart = await backend.get_cart(session)
    assert cart.item_count == 2
    assert cart.items[0].price == 112.0


async def test_hold_expiry_drops_the_cart_line_and_frees_inventory(backend, session, clock):
    await backend.add_to_cart(session, PIT, 2)
    assert backend.engine.remaining(PIT) == 4
    clock.advance(HOLD_TTL_S + 1)
    cart = await backend.get_cart(session)
    assert cart.items == []
    assert backend.engine.remaining(PIT) == 6


async def test_growing_a_hold_resets_the_timer(backend, session, clock):
    await backend.add_to_cart(session, PIT, 1)
    clock.advance(HOLD_TTL_S - 10)
    await backend.add_to_cart(session, PIT, 1)
    clock.advance(HOLD_TTL_S - 5)  # would have expired on the original timer
    holds = backend.engine.holds_for_session(session.session_id)
    assert len(holds) == 1 and holds[0].quantity == 2


async def test_sold_out_tier_cannot_be_held(backend, session):
    with pytest.raises(SoldOutError, match="waitlist"):
        await backend.add_to_cart(session, "AT-TIX-103-PIT", 1)


async def test_hold_cannot_exceed_open_inventory(backend, session):
    with pytest.raises(SoldOutError, match="only 6 left"):
        await backend.add_to_cart(session, PIT, 7)


async def test_sold_together_pair_cannot_be_split(backend, session):
    # AT-RSL-203 is a resale pair (sold_together=2).
    with pytest.raises(StateError, match="sets of 2"):
        await backend.add_to_cart(session, "AT-RSL-203", 1)
    await backend.add_to_cart(session, "AT-RSL-203", 2)
    holds = backend.engine.holds_for_session(session.session_id)
    assert len(holds) == 1 and holds[0].quantity == 2
    with pytest.raises(StateError, match="sets of 2"):
        await backend.update_cart_item(session, "AT-RSL-203", 1)
    cart = await backend.update_cart_item(session, "AT-RSL-203", 0)
    assert cart.items == []


async def test_per_event_cap_spans_tiers(backend, session):
    await backend.add_to_cart(session, "AT-TIX-101-LOW", 5)
    await backend.add_to_cart(session, PIT, 3)  # 8 for AT-EVT-101, which is the cap
    with pytest.raises(HoldLimitError, match=str(MAX_TICKETS_PER_EVENT)):
        await backend.add_to_cart(session, "AT-TIX-101-TER", 1)
    await backend.add_to_cart(session, "AT-TIX-105-ORC", 2)


async def test_per_event_cap_holds_on_the_update_path_too(backend, session):
    await backend.add_to_cart(session, "AT-TIX-101-LOW", 5)
    await backend.add_to_cart(session, PIT, 3)  # 8 for AT-EVT-101, which is the cap
    with pytest.raises(HoldLimitError, match=str(MAX_TICKETS_PER_EVENT)):
        await backend.update_cart_item(session, PIT, 4)  # would make 9 across the two tiers
    holds = {h.product_id: h.quantity for h in backend.engine.holds_for_session(session.session_id)}
    assert holds == {"AT-TIX-101-LOW": 5, PIT: 3}
    cart = await backend.update_cart_item(session, PIT, 2)  # shrinking is always allowed
    assert cart.item_count == 7


async def test_update_and_remove_adjust_the_hold(backend, session):
    await backend.add_to_cart(session, PIT, 2)
    cart = await backend.update_cart_item(session, PIT, 1)
    assert cart.item_count == 1
    assert backend.engine.remaining(PIT) == 5
    cart = await backend.remove_from_cart(session, PIT)
    assert cart.items == []
    assert backend.engine.remaining(PIT) == 6


async def test_update_to_zero_releases_the_hold(backend, session):
    await backend.add_to_cart(session, PIT, 2)
    await backend.update_cart_item(session, PIT, 0)
    assert backend.engine.holds_for_session(session.session_id) == []


async def test_reset_session_releases_only_that_sessions_holds(backend, session, other_session):
    await backend.add_to_cart(session, PIT, 2)
    await backend.add_to_cart(other_session, PIT, 1)
    backend.reset_session(session.session_id)
    assert backend.engine.holds_for_session(session.session_id) == []
    assert len(backend.engine.holds_for_session(other_session.session_id)) == 1
    assert backend.engine.remaining(PIT) == 5


async def test_the_agent_hears_the_rule_when_a_hold_is_refused(main, backend, session):
    """The example's executor relays the engine's message instead of a system failure."""
    from commerce_common.skills import SkillRegistry
    from entertainment.api.mock_ticketing import TicketingToolExecutor
    from shopping_agent import ShoppingSessionState

    executor = TicketingToolExecutor(
        backend=backend,
        config=main.agent.config,
        skills=SkillRegistry([]),
        session=session,
        state=ShoppingSessionState(),
    )
    await executor.execute("get_product_details", {"product_id": "AT-TIX-101-LOW"})
    await executor.execute("get_product_details", {"product_id": "AT-TIX-101-TER"})
    await executor.execute("add_to_cart", {"product_id": "AT-TIX-101-LOW", "quantity": 6})
    # Six held plus three more on another tier of the same event crosses the per-event cap.
    result = await executor.execute("add_to_cart", {"product_id": "AT-TIX-101-TER", "quantity": 3})
    assert result.is_error and "Nothing changed" in result.result_text
    assert str(MAX_TICKETS_PER_EVENT) in result.result_text
    assert "temporarily unavailable" not in result.result_text


def test_sold_out_is_the_contracts_unavailable():
    """The executor relays ``Unavailable`` to the model with ids; any other exception is
    reported as an outage. A tier with too few seats left is the former."""
    from entertainment.api.ticketing import SoldOutError
    from shopping_agent.backend import Unavailable

    assert issubclass(SoldOutError, Unavailable)
