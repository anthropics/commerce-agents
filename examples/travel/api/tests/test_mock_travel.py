# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
from datetime import date, timedelta

import pytest

from shopping_agent import SearchFilters
from travel.api.mock_travel import MockTravel


def test_catalog_loads_and_validates(backend):
    assert len(backend.products) >= 50
    assert backend.store_name == "ACME Travel"
    sample = backend.products["AL-STAY-101"]
    assert sample.brand == "ACME Guesthouses"
    assert sample.long_description  # hero stays carry a long description
    assert sample.attributes["price_unit"] == "per_night"
    assert sample.attributes["refundable"] == "no"
    assert "Free cancellation" not in sample.labels


async def test_search_matches_cities_and_titles(backend, session):
    stays = await backend.search_products(session, "Alfama stay in Lisbon")
    assert stays and stays[0].product_id == "AL-STAY-101"

    flights = await backend.search_products(session, "flight from New York to Lisbon")
    assert flights and flights[0].product_id in {"AL-FLT-201", "AL-FLT-202"}

    kyoto = await backend.search_products(session, "Kyoto")
    assert kyoto
    for product in kyoto:
        cities = (product.attributes.get("city"), product.attributes.get("destination_city"))
        assert "Kyoto" in cities

    nothing = await backend.search_products(session, "zzzqqq")
    assert nothing == []


async def test_travel_date_is_a_hard_availability_filter(backend, session):
    in_august = await backend.search_products(
        session,
        "Reykjavik northern lights",
        SearchFilters(attributes={"travel_date": "2026-08-10"}),
        limit=20,
    )
    assert in_august  # other Reykjavik items are available in August
    # AL-EXP-311's availability window opens 2026-09-15.
    assert all(p.product_id != "AL-EXP-311" for p in in_august)

    in_october = await backend.search_products(
        session,
        "Reykjavik northern lights",
        SearchFilters(attributes={"travel_date": "2026-10-10"}),
        limit=20,
    )
    assert any(p.product_id == "AL-EXP-311" for p in in_october)


def test_availability_and_occupancy_move_with_the_clock(backend):
    later = MockTravel(today=backend.today + timedelta(weeks=3, days=2))
    # AL-STAY-101 opens 2026-07-01 as authored; three whole weeks on, it opens 2026-07-22.
    assert backend.products["AL-STAY-101"].attributes["availability_from"] == "2026-07-01"
    assert later.products["AL-STAY-101"].attributes["availability_from"] == "2026-07-22"
    assert later.products["AL-STAY-101"].attributes["availability_to"] == "2027-01-10"
    # The occupancy weeks move with it, so the same weekday three weeks on reads the same row.
    thursday = date(2026, 10, 15)
    assert later.units_left_on("AL-STAY-104", thursday + timedelta(weeks=3)) == (
        backend.units_left_on("AL-STAY-104", thursday)
    )


async def test_price_constraints_stay_hard(backend, session):
    affordable = await backend.search_products(
        session, "Kyoto stay", SearchFilters(max_price=300), limit=20
    )
    assert affordable
    assert all(p.price <= 300 for p in affordable)
    assert all(p.product_id != "AL-STAY-108" for p in affordable)  # 358/night


async def test_refundable_filter_and_sort(backend, session):
    refundable = await backend.search_products(
        session, "Lisbon stay", SearchFilters(attributes={"refundable": "yes"}), limit=20
    )
    assert refundable
    assert all(p.attributes["refundable"] == "yes" for p in refundable)
    # AL-STAY-101 and AL-STAY-103 are non-refundable rates.
    assert all(p.product_id not in {"AL-STAY-101", "AL-STAY-103"} for p in refundable)

    by_price = await backend.search_products(
        session, "Kyoto", SearchFilters(sort="price_asc"), limit=20
    )
    prices = [p.price for p in by_price]
    assert prices == sorted(prices)


async def test_category_filter_and_relaxation(backend, session):
    only_experiences = await backend.search_products(
        session, "Kyoto", SearchFilters(category="experiences"), limit=20
    )
    assert only_experiences
    assert all(p.category == "experiences" for p in only_experiences)

    relaxed = await backend.search_products(
        session, "Kyoto", SearchFilters(category="rail-passes"), limit=20
    )
    assert relaxed


async def test_dated_search_stamps_cancellation_deadlines(backend, session):
    dated = await backend.search_products(
        session,
        "Lisbon stay",
        SearchFilters(attributes={"travel_date": "2026-10-15"}),
        limit=20,
    )
    assert dated
    for product in dated:
        if product.attributes["refundable"] != "yes":
            assert "free_cancellation_until" not in product.attributes
        elif product.category == "flights":
            assert product.attributes["free_cancellation_until"] == "2026-10-14"
        else:
            # Stays and experiences cut off 48 hours before the date.
            assert product.attributes["free_cancellation_until"] == "2026-10-13"

    flights = await backend.search_products(
        session,
        "flight to Lisbon",
        SearchFilters(attributes={"travel_date": "2026-10-15"}),
        limit=20,
    )
    refundable_flights = [
        f for f in flights if f.category == "flights" and f.attributes["refundable"] == "yes"
    ]
    assert refundable_flights
    # Flights cut off 24 hours before departure.
    assert all(f.attributes["free_cancellation_until"] == "2026-10-14" for f in refundable_flights)

    undated = await backend.search_products(session, "Lisbon stay", limit=20)
    assert all("free_cancellation_until" not in p.attributes for p in undated)


async def test_dated_search_stamps_date_flex_on_stays_only(backend, session):
    dated = await backend.search_products(
        session,
        "Lisbon stay",
        SearchFilters(attributes={"travel_date": "2026-10-15"}),
        limit=20,
    )
    stays = [p for p in dated if p.attributes.get("price_unit") == "per_night"]
    assert stays
    for product in stays:
        strip = product.attributes["date_flex"]
        cells = strip.split("|")
        # Three days either side of a mid-window date gives seven nights in the window.
        assert len(cells) == 7
        chosen = [c for c in cells if "*" in c]
        assert len(chosen) == 1
        chosen_date, chosen_rate = chosen[0].replace("*", "").split(":")
        assert chosen_date == "2026-10-15"
        assert int(chosen_rate) == round(product.price)
    for product in dated:
        if product.attributes.get("price_unit") != "per_night":
            assert "date_flex" not in product.attributes

    undated = await backend.search_products(session, "Lisbon stay", limit=20)
    assert all("date_flex" not in p.attributes for p in undated)


async def test_date_flex_is_deterministic_and_window_clamped(backend):
    first = backend.date_flex_strip("AL-STAY-101", date(2026, 10, 15))
    second = backend.date_flex_strip("AL-STAY-101", date(2026, 10, 15))
    assert first == second
    # AL-STAY-101's availability window opens 2026-07-01.
    window_edge = backend.date_flex_strip("AL-STAY-101", date(2026, 7, 2))
    assert window_edge is not None
    assert "2026-06-30" not in window_edge and "2026-07-01" in window_edge
    assert backend.date_flex_strip("AL-FLT-201", date(2026, 10, 15)) is None


async def test_scarcity_stamp_matches_supplier_occupancy(backend, session):
    # 2026-10-15 is a Thursday; AL-STAY-104's 6 rooms at 80% midweek occupancy leave 2.
    assert backend.units_left_on("AL-STAY-104", date(2026, 10, 15)) == 2
    # AL-STAY-108 has no occupancy series.
    assert backend.units_left_on("AL-STAY-108", date(2026, 10, 15)) is None

    dated = await backend.search_products(
        session,
        "Lisbon stay",
        SearchFilters(attributes={"travel_date": "2026-10-15"}),
        limit=20,
    )
    by_id = {p.product_id: p for p in dated}
    assert by_id["AL-STAY-104"].attributes["units_left_for_dates"] == "2"
    # The stamp appears only at 3 units or fewer.
    for product in dated:
        left = backend.units_left_on(product.product_id, date(2026, 10, 15))
        if left is None or left > 3:
            assert "units_left_for_dates" not in product.attributes

    undated = await backend.search_products(session, "Lisbon stay", limit=20)
    assert all("units_left_for_dates" not in p.attributes for p in undated)


async def test_stay_catalog_carries_typical_rate_bands(backend):
    stays = [p for p in backend.products.values() if p.category == "stays"]
    assert stays
    for stay in stays:
        band = stay.attributes["typical_rate_band"]
        low, high = (int(part) for part in band.split("-"))
        assert 0 < low < high
        # Non-refundable rates are priced below their band; refundable rates fall inside it.
        if stay.attributes.get("refundable") == "no":
            assert stay.price < low
        else:
            assert low <= stay.price <= high


async def test_add_to_cart_books_planned_nights_for_stays(backend, session):
    backend.note_trip_plan(session.session_id, trip_nights=3, stay_nights={"AL-STAY-101": 3})

    # A quantity-1 add books the planned nights.
    cart = await backend.add_to_cart(session, "AL-STAY-101", 1)
    stay = next(i for i in cart.items if i.product_id == "AL-STAY-101")
    assert stay.quantity == 3
    assert stay.line_total == 3 * 214.0

    # A stay the plan did not name falls back to the trip's night count.
    cart = await backend.add_to_cart(session, "AL-STAY-104", 1)
    assert next(i for i in cart.items if i.product_id == "AL-STAY-104").quantity == 3

    # Quantity updates and non-stay adds are taken as given.
    cart = await backend.update_cart_item(session, "AL-STAY-101", 2)
    assert next(i for i in cart.items if i.product_id == "AL-STAY-101").quantity == 2
    cart = await backend.add_to_cart(session, "AL-EXP-301", 1)
    assert next(i for i in cart.items if i.product_id == "AL-EXP-301").quantity == 1

    backend.reset_session(session.session_id)
    assert (await backend.get_cart(session)).items == []
    # reset_session clears the plan as well as the cart.
    cart = await backend.add_to_cart(session, "AL-STAY-101", 1)
    assert next(i for i in cart.items if i.product_id == "AL-STAY-101").quantity == 1
    backend.reset_session(session.session_id)


async def test_policy_search(backend, session):
    pricing = await backend.search_policies(session, "why did the price change at checkout")
    assert any(p.policy_id == "price-revalidation" for p in pricing)

    cancellations = await backend.search_policies(
        session, "cancel a refundable booking for a refund"
    )
    assert cancellations and cancellations[0].category == "cancellations"


async def test_fulfillment_is_booking_confirmation(backend, session):
    options = await backend.get_fulfillment_options(session, ["AL-STAY-101"])
    assert options and options[0].method == "delivery"
    assert "e-confirmation" in options[0].eta
    assert all(o.method != "pickup" for o in options)

    trip = await backend.get_fulfillment_options(session, ["AL-FLT-201", "AL-EXP-301"])
    assert any(o.method == "delivery" and "e-ticket" in o.eta for o in trip)
    assert any(o.method == "pickup" for o in trip)  # the experience's meeting point


async def test_a_sold_out_stay_cannot_be_added(backend, session):
    from shopping_agent.backend import Unavailable

    sold_out = next(p for p in backend.products.values() if not p.in_stock)
    with pytest.raises(Unavailable) as raised:
        await backend.add_to_cart(session, sold_out.product_id, 1)
    assert sold_out.product_id in str(raised.value)
    with pytest.raises(Unavailable):
        await backend.add_to_cart(session, "nope-does-not-exist", 1)
    assert (await backend.get_cart(session)).items == []
