# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
from datetime import datetime

import pytest

from commerce_common.types import MemoryCategory, MemoryFact
from demo_common.storefront_fixtures import load_json
from retail.api.mock_retail import (
    DATA_DIR,
    FREE_SHIPPING_OVER,
    FREIGHT_SHIPPING,
    STANDARD_SHIPPING,
    MockRetail,
)
from shopping_agent import SearchFilters


def test_catalog_loads_and_validates(backend):
    assert len(backend.products) >= 50
    assert backend.store_name == "ACME"
    sample = backend.products["AR-1201"]
    assert sample.brand == "ACME Basecamp"
    assert sample.long_description  # hero products carry a long description


async def test_search_relevance(backend, session):
    tents = await backend.search_products(session, "tent for two people")
    assert tents and tents[0].product_id in {"AR-1201", "AR-1202"}

    gift_toys = await backend.search_products(session, "wooden block set for a 9 year old")
    assert gift_toys and gift_toys[0].product_id == "AR-1401"

    luggage = await backend.search_products(session, "carry-on luggage")
    assert any(p.product_id == "AR-1801" for p in luggage[:3])

    nothing = await backend.search_products(session, "zzzqqq")
    assert nothing == []


async def test_out_of_stock_items_are_searchable(backend, session):
    packs = await backend.search_products(session, "hiking backpack for overnight trips")
    assert any(p.product_id == "AR-1207" and p.in_stock is False for p in packs)


async def test_delivery_promises_stamped(backend):
    for product in backend.products.values():
        promise = product.attributes.get("delivery")
        if product.in_stock:
            assert promise is not None and promise.startswith("Get it by ")
        else:
            assert promise is None

    # The promise is kept out of search scoring.
    sample = next(p for p in backend.products.values() if p.in_stock)
    assert "Get it by" not in backend._searchable_text(sample)["attributes"]


def test_memory_seed_is_schema_valid():
    seed = load_json(DATA_DIR, "memory-seed.json")
    assert "demo-user" in seed
    for facts in seed.values():
        for raw in facts:
            fact = MemoryFact(
                key=raw["key"], value=raw["value"], category=MemoryCategory(raw["category"])
            )
            assert fact.key and fact.value


async def test_search_filters_and_sort(backend, session):
    cheap_coffee = await backend.search_products(session, "coffee", SearchFilters(max_price=100))
    assert all(p.price <= 100 for p in cheap_coffee)
    assert all(p.product_id != "AR-1002" for p in cheap_coffee)

    outdoor_only = await backend.search_products(
        session, "tent stove cooler", SearchFilters(category="outdoor-camping"), limit=20
    )
    assert outdoor_only and all(p.category == "outdoor-camping" for p in outdoor_only)

    by_price = await backend.search_products(
        session, "fitness", SearchFilters(sort="price_asc"), limit=20
    )
    prices = [p.price for p in by_price]
    assert prices == sorted(prices)


async def test_policy_search(backend, session):
    returns = await backend.search_policies(session, "how do refunds and returns work")
    assert returns and returns[0].policy_id == "returns"

    membership = await backend.search_policies(session, "how much does membership cost per year")
    assert any(p.policy_id == "membership" for p in membership)


async def test_fulfillment_options_follow_the_shipping_policy(backend, session):
    options = await backend.get_fulfillment_options(session, ["AR-1201"])
    assert [o.method for o in options] == ["delivery", "delivery", "pickup"]
    standard, express, _pickup = options
    assert backend.products["AR-1201"].price > FREE_SHIPPING_OVER and standard.fee == 0.0
    cheapest = min(backend.products.values(), key=lambda p: p.price)
    (paid, *_rest) = await backend.get_fulfillment_options(session, [cheapest.product_id])
    assert paid.fee == STANDARD_SHIPPING.fee

    freight = await backend.get_fulfillment_options(session, ["AR-1307"])
    assert freight[-1] == FREIGHT_SHIPPING

    shipping = next(p for p in backend._policies if p.policy_id == "shipping").content
    for term in (
        f"${STANDARD_SHIPPING.fee}",
        f"free over ${FREE_SHIPPING_OVER}",
        standard.eta.split(" (")[0],
        express.eta.split(" (")[0],
        f"${express.fee}",
        "freight",
    ):
        assert term in shipping, term


def test_pickup_eta_stays_inside_store_hours():
    eta = MockRetail._pickup_eta
    # Two hours of prep, rounded up to the hour.
    assert eta(datetime(2026, 7, 14, 13, 0)) == "today by 3 PM"
    assert eta(datetime(2026, 7, 14, 13, 20)) == "today by 4 PM"
    # Before opening, the two hours count from the 9 AM open.
    assert eta(datetime(2026, 7, 14, 6, 30)) == "today by 11 AM"
    # 19:00 plus two hours lands on the 9 PM close.
    assert eta(datetime(2026, 7, 14, 19, 0)) == "today by 9 PM"
    assert eta(datetime(2026, 7, 14, 19, 30)) == "tomorrow morning"
    assert eta(datetime(2026, 7, 14, 23, 0)) == "tomorrow morning"


async def test_a_family_is_found_by_its_option_values_and_its_variants_stay_out_of_listings(
    backend, session
):
    assert "AR-1902" in backend.products and "AR-1902-KING" not in backend.products
    assert backend.variants["AR-1902-KING"].variant_of == "AR-1902"
    hits = await backend.search_products(session, "king mattress")
    assert hits and hits[0].product_id == "AR-1902"
    assert hits[0].options == {"size": ["twin", "full", "queen", "king"]}
    assert all(hit.variant_of is None for hit in hits)


async def test_details_resolve_a_family_and_a_variant(backend, session):
    family = await backend.get_product_details(session, "AR-1606")
    assert family is not None and set(family.options) == {"size", "color"}
    assert len(family.variants) == 8
    assert all(set(v.option_values) == {"size", "color"} for v in family.variants)
    variant = await backend.get_product_details(session, "ar-1606-king-blush")
    assert variant is not None and variant.variant_of == "AR-1606"
    assert variant.in_stock is False and variant.price == 37.0
    assert backend.listing_of("AR-1606-KING-BLUSH") is family


async def test_an_order_line_for_a_variant_names_its_choice(backend, session):
    orders = await backend.get_orders(session)
    lines = [item for order in orders for item in order.items if item.variant_of]
    assert [(i.product_id, i.option_values, i.variant_of) for i in lines] == [
        ("AR-1902-QUEEN", {"size": "queen"}, "AR-1902")
    ]


async def test_cart_refuses_unknown_ids_and_families_in_kind(backend, session):
    """A backend called past the executor's gates (another host, a site button, a test)
    still answers in the contract's terms: ``Unavailable`` naming ids, never a KeyError."""
    from shopping_agent.backend import Unavailable

    with pytest.raises(Unavailable):
        await backend.add_to_cart(session, "nope-does-not-exist", 1)
    with pytest.raises(Unavailable) as raised:
        await backend.add_to_cart(session, "AR-1008", 1)  # a family with options
    assert "AR-1008-12LB" in str(raised.value)
    assert (await backend.get_cart(session)).items == []


async def test_a_quantity_under_one_removes_the_line(backend, session):
    await backend.add_to_cart(session, "AR-1001", 2)
    cart = await backend.update_cart_item(session, "AR-1001", 0)
    assert cart.items == []
    await backend.add_to_cart(session, "AR-1001", 1)
    cart = await backend.update_cart_item(session, "AR-1001", -3)
    assert cart.items == []
