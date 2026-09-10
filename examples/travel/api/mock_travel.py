# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The travel example's ``StorefrontBackend`` over the fixtures in ``data/``. Stays,
flights, and experiences are ordinary products whose availability window lives in their
attributes; a ``travel_date`` attribute filter is enforced as availability, and dated
search results are quotes carrying cancellation, rate-flex, and remaining-room details.
Availability windows and the occupancy calendar move forward by whole weeks from each
fixture's ``dates_anchored_to``, so dated searches stay answerable after authoring."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from demo_common.storefront_fixtures import (
    SessionCarts,
    anchored_shift,
    example_data_dir,
    find_order,
    find_product,
    keyword_score,
    load_catalog,
    load_json,
    load_orders,
    load_policies,
    load_users,
    matches_attribute_filters,
    newest_orders,
    orders_for,
    preferences_of,
    rank_products,
    search_help,
    summary_of,
    within_price_and_rating,
)
from shopping_agent import (
    Cart,
    FulfillmentOption,
    Order,
    Policy,
    Product,
    ProductDetails,
    SearchFilters,
    ShoppingSessionContext,
    StorefrontBackend,
    UserPreferences,
)
from shopping_agent.backend import Unavailable

DATA_DIR = example_data_dir(__file__)

_TRAVEL_DATE = "travel_date"
_SEARCH_WEIGHTS = {
    "title": 3.0,
    "cities": 2.5,
    "brand": 2.0,
    "category": 2.0,
    "attributes": 1.5,
    "description": 1.0,
}
_SYNONYMS: dict[str, list[str]] = {
    "hotel": ["stay", "guesthouse", "inn", "lodge", "riad"],
    "hostel": ["guesthouse", "stay"],
    "accommodation": ["stay", "hotel"],
    "lodging": ["stay", "hotel"],
    "room": ["stay", "suite"],
    "fly": ["flight"],
    "plane": ["flight"],
    "airfare": ["flight", "fare"],
    "tour": ["experience", "walk", "trip"],
    "activity": ["experience", "tour"],
    "excursion": ["experience", "tour", "trip"],
    "cancellation": ["refundable"],
    "cancellable": ["refundable"],
    "aurora": ["northern", "light"],
}

# Rate seasonality by weekday, Monday first. The chosen date's own cell always shows the
# catalog price, so the flex strip never contradicts the card it decorates.
_WEEKDAY_RATE_FACTORS = (0.94, 0.92, 0.93, 0.90, 1.08, 1.12, 0.98)
# The remaining-rooms chip appears only at or below this many rooms.
_SCARCITY_MAX_UNITS = 3


def _travel_date(filters: SearchFilters) -> date | None:
    try:
        raw = filters.attributes.get(_TRAVEL_DATE, "")
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _available_on(product: ProductDetails, travel_date: date) -> bool:
    try:
        first = date.fromisoformat(product.attributes.get("availability_from", ""))
        last = date.fromisoformat(product.attributes.get("availability_to", ""))
    except ValueError:
        return True  # not date-bound
    return first <= travel_date <= last


def _shift_day(day: str, delta: timedelta) -> str:
    return (date.fromisoformat(day) + delta).isoformat()


def shift_availability(products: dict[str, ProductDetails], delta: timedelta) -> None:
    """Move every ``availability_from``/``availability_to`` forward by ``delta``, in place."""
    if not delta:
        return
    for product in products.values():
        for key in ("availability_from", "availability_to"):
            if day := product.attributes.get(key):
                product.attributes[key] = _shift_day(day, delta)


def load_occupancy(data_dir: Path, today: date | None = None) -> dict[str, Any]:
    """``merchant_occupancy.json`` with its window and every ``week_start`` moved forward
    by the whole weeks from its ``dates_anchored_to`` to ``today``; ``{}`` when absent."""
    if not (data_dir / "merchant_occupancy.json").exists():
        return {}
    occupancy = load_json(data_dir, "merchant_occupancy.json")
    delta = anchored_shift(occupancy, today)
    if delta:
        window = occupancy.get("window") or {}
        for key in ("from", "to"):
            if window.get(key):
                window[key] = _shift_day(window[key], delta)
        for listing in occupancy.get("listings", []):
            for week in listing.get("weeks", []):
                if week.get("week_start"):
                    week["week_start"] = _shift_day(week["week_start"], delta)
    return occupancy


def _rate_jitter(product_id: str, night: date) -> float:
    # CRC rather than hash(): the strip must paint the same way on every boot.
    seed = zlib.crc32(f"{product_id}:{night.isoformat()}".encode())
    return ((seed % 61) - 30) / 1000


def cancellation_deadline(category: str | None, start: date) -> str:
    """The free-cancellation cutoff data/policies.json states: 24 hours before a flight,
    48 hours before a stay or experience."""
    return (start - timedelta(days=1 if category == "flights" else 2)).isoformat()


@dataclass
class TripPlan:
    """The night structure the itinerary extension last rendered for a session, so a
    bare "add the stay" books the planned nights rather than a quantity of 1."""

    trip_nights: int | None = None
    stay_nights: dict[str, int] = field(default_factory=dict)


class MockTravel(StorefrontBackend):
    def __init__(self, data_dir: Path = DATA_DIR, today: date | None = None) -> None:
        """``today`` is for a host that runs on its own clock; the default is the real date."""
        self.today: date = today or datetime.now(UTC).date()
        catalog, self.products, self.variants = load_catalog(data_dir)
        self.store_name: str = catalog.get("store_name", "the store")
        delta = anchored_shift(catalog, self.today)
        shift_availability(self.products, delta)
        shift_availability(self.variants, delta)
        self._users = load_users(data_dir)
        self._orders = load_orders(data_dir)
        self._policies = load_policies(data_dir)
        # The supplier's occupancy fixture is also the storefront's remaining-rooms
        # source, so both sides quote one count.
        occupancy = load_occupancy(data_dir, self.today)
        self._occupancy: dict[str, dict[str, Any]] = {
            row["listing_id"]: row for row in occupancy.get("listings", [])
        }
        self._carts = SessionCarts()
        self._trip_plans: dict[str, TripPlan] = {}

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def _searchable_text(self, product: ProductDetails) -> dict[str, str]:
        return {
            "title": product.title,
            "cities": " ".join(
                product.attributes.get(key, "")
                for key in ("city", "origin_city", "destination_city")
            ),
            "brand": product.brand or "",
            "category": product.category or "",
            "attributes": " ".join(f"{k} {v}" for k, v in product.attributes.items()),
            "description": f"{product.short_description or ''} {product.long_description or ''}",
        }

    def _score(self, product: ProductDetails, query_tokens: list[str]) -> float:
        return keyword_score(
            self._searchable_text(product), _SEARCH_WEIGHTS, query_tokens, _SYNONYMS
        )

    @staticmethod
    def _hard_filter(product: ProductDetails, filters: SearchFilters) -> bool:
        if not within_price_and_rating(product, filters):
            return False
        travel_date = _travel_date(filters)
        return travel_date is None or _available_on(product, travel_date)

    @staticmethod
    def _soft_filter(product: ProductDetails, filters: SearchFilters) -> bool:
        return matches_attribute_filters(product, filters, ignore=frozenset({_TRAVEL_DATE}))

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        del session
        ranked = rank_products(
            self.products.values(),
            query,
            filters,
            limit,
            score=self._score,
            hard_filter=self._hard_filter,
            soft_filter=self._soft_filter,
        )
        results = [summary_of(product) for product in ranked]
        travel_date = _travel_date(filters) if filters is not None else None
        if travel_date is not None:
            for product in results:
                self._quote_for_date(product, travel_date)
        return results

    def _quote_for_date(self, product: Product, travel_date: date) -> None:
        """Turn a dated result into a quote: the concrete cancellation cutoff, and for
        stays the rate strip around the date and any tight room count."""
        if product.attributes.get("refundable") == "yes":
            product.attributes["free_cancellation_until"] = cancellation_deadline(
                product.category, travel_date
            )
        flex = self.date_flex_strip(product.product_id, travel_date)
        if flex is not None:
            product.attributes["date_flex"] = flex
        left = self.units_left_on(product.product_id, travel_date)
        if left is not None and 1 <= left <= _SCARCITY_MAX_UNITS:
            product.attributes["units_left_for_dates"] = str(left)

    def date_flex_strip(self, product_id: str, chosen: date) -> str | None:
        """Nightly rates for the three nights either side of ``chosen``, as the string
        the card parses (``2026-10-14:189|2026-10-15*:214|...``, ``*`` marking the chosen
        night); nights outside the availability window are left out."""
        product = self.products.get(product_id)
        if product is None or product.attributes.get("price_unit") != "per_night":
            return None
        chosen_factor = _WEEKDAY_RATE_FACTORS[chosen.weekday()]
        cells: list[str] = []
        for offset in range(-3, 4):
            night = chosen + timedelta(days=offset)
            if not _available_on(product, night):
                continue
            if offset == 0:
                rate = round(product.price)
            else:
                relative = _WEEKDAY_RATE_FACTORS[night.weekday()] / chosen_factor
                rate = round(product.price * relative * (1 + _rate_jitter(product_id, night)))
            cells.append(f"{night.isoformat()}{'*' if offset == 0 else ''}:{rate}")
        return "|".join(cells) if len(cells) >= 3 else None

    def units_left_on(self, product_id: str, night: date) -> int | None:
        """Rooms unbooked on ``night`` per the occupancy fixture, or None when the
        listing has no series. Booked rooms are floored, so the count never understates
        what is left."""
        listing = self._occupancy.get(product_id)
        rooms = int(listing.get("rooms", 0)) if listing else 0
        if rooms <= 0:
            return None
        for week in listing.get("weeks", []):
            try:
                week_start = date.fromisoformat(week["week_start"])
            except (KeyError, ValueError):
                continue
            if week_start <= night < week_start + timedelta(days=7):
                key = (
                    "weekend_occupancy_pct"
                    if night.weekday() in (4, 5)
                    else "midweek_occupancy_pct"
                )
                pct = week.get(key, week.get("occupancy_pct"))
                return None if pct is None else rooms - int(rooms * pct / 100)
        return None

    def product(self, product_id: str) -> ProductDetails | None:
        return find_product(self.products, self.variants, product_id)

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        del session
        return self.product(product_id)

    # ------------------------------------------------------------------
    # Cart
    # ------------------------------------------------------------------

    def note_trip_plan(
        self, session_id: str, *, trip_nights: int | None, stay_nights: dict[str, int]
    ) -> None:
        self._trip_plans[session_id] = TripPlan(trip_nights=trip_nights, stay_nights=stay_nights)

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        return self._carts.cart(session.session_id)

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        product = self.products.get(product_id)
        if product is None:
            raise Unavailable(f"{product_id} is not in the catalog")
        if not product.in_stock:
            raise Unavailable(f"{product_id} is sold out for these dates")
        existing = self._carts.lines(session.session_id).get(product_id)
        plan = self._trip_plans.get(session.session_id)
        # A first, unit-quantity add of a nightly-rated stay books the planned nights;
        # an explicit quantity is taken as given.
        if (
            plan is not None
            and quantity == 1
            and existing is None
            and product.attributes.get("price_unit") == "per_night"
        ):
            quantity = plan.stay_nights.get(product_id) or plan.trip_nights or quantity
        quantity += existing.quantity if existing else 0
        return self._carts.put(session.session_id, product, quantity)

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        return self._carts.set_quantity(session.session_id, product_id, quantity)

    async def remove_from_cart(self, session: ShoppingSessionContext, product_id: str) -> Cart:
        return self._carts.remove(session.session_id, product_id)

    def reset_session(self, session_id: str) -> None:
        self._carts.reset(session_id)
        self._trip_plans.pop(session_id, None)

    # ------------------------------------------------------------------
    # Traveler, bookings, help content, fulfillment
    # ------------------------------------------------------------------

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        return preferences_of(self._users, session.user_id)

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5) -> list[Order]:
        return orders_for(self._orders, session.user_id, limit)

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        return find_order(self._orders, session.user_id, order_id)

    def recent_orders(self, limit: int = 6) -> list[Order]:
        return newest_orders(self._orders, limit)

    async def search_policies(self, session: ShoppingSessionContext, query: str) -> list[Policy]:
        del session
        return search_help(self._policies, query)

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ) -> list[FulfillmentOption]:
        """How the confirmation reaches the traveler; nothing is shipped."""
        del session
        categories = {self.products[pid].category for pid in product_ids if pid in self.products}
        options = [
            FulfillmentOption(
                method="delivery",
                eta="instant e-confirmation; booking details arrive by email within minutes",
                fee=0.0,
            )
        ]
        if "flights" in categories:
            options.append(
                FulfillmentOption(
                    method="delivery",
                    eta="e-tickets issued within 2 hours; check-in opens 24 hours before departure",
                    fee=0.0,
                )
            )
        if "experiences" in categories:
            options.append(
                FulfillmentOption(
                    method="pickup",
                    eta="meeting point pinned in the confirmation email the day before",
                    fee=0.0,
                    location="activity start point",
                )
            )
        return options
