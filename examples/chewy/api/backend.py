# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Chewy catalog adapter for Anthropic's StorefrontBackend contract."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from shopping_agent import (
    Cart,
    CartItem,
    FulfillmentOption,
    NotOffered,
    Order,
    Policy,
    Product,
    ProductDetails,
    SearchFilters,
    ShoppingSessionContext,
    StorefrontBackend,
    UserPreferences,
)

from .mcp_client import SearchAgentMcpClient
from .ucp_client import UcpOrchestratorClient

ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class UcpClient(Protocol):
    async def create_checkout(self, quantities: dict[str, int]) -> dict[str, Any]: ...

    async def get_checkout(self, checkout_id: str) -> dict[str, Any]: ...

    async def cancel_checkout(self, checkout_id: str) -> dict[str, Any]: ...


@dataclass
class _CartState:
    checkout_id: str
    quantities: dict[str, int] = field(default_factory=dict)


logger = logging.getLogger(__name__)


SEARCH_FIELDS = [
    "partNumber",
    "name",
    "manufacturer",
    "price",
    "averageOverallRating",
    "totalReviewCount",
    "inStock",
    "longDescription",
    "ProductType",
    "PetType",
    "key_benefits",
    "pros",
    "cons",
]


class ChewyStorefrontBackend(StorefrontBackend):
    """Chewy catalog plus a guest cart backed by a UCP checkout session."""

    store_name = "Chewy"

    def __init__(
        self,
        endpoint: str = "http://localhost:8080/mcp",
        *,
        call_tool: ToolCaller | None = None,
        ucp_client: UcpClient | None = None,
        ucp_base_url: str = "",
        ucp_api_key: str = "",
    ) -> None:
        self._client = SearchAgentMcpClient(endpoint)
        self._call_tool = call_tool or self._client.call_tool
        self._ucp = ucp_client or UcpOrchestratorClient(ucp_base_url, ucp_api_key)
        self._carts: dict[str, _CartState] = {}
        self._cart_locks: dict[str, asyncio.Lock] = {}
        # The shared demo host uses this cache for catalog detail routes and UI enrichment.
        self.products: dict[str, ProductDetails] = {}

    async def search_products(
        self,
        session: ShoppingSessionContext,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Product]:
        del session
        arguments: dict[str, Any] = {
            "term": query,
            "limit": limit,
            "include": "items",
            "fields": SEARCH_FIELDS,
            "filter": {"must": [{"field": "omitOutOfStock", "op": "eq", "value": "true"}]},
        }
        if filters:
            clauses = arguments["filter"]["must"]
            if filters.min_price is not None:
                clauses.append({"field": "price", "op": "gte", "value": str(filters.min_price)})
            if filters.max_price is not None:
                clauses.append({"field": "price", "op": "lte", "value": str(filters.max_price)})
            refinements = [filters.category, *filters.attributes.values()]
            refinement = " ".join(value for value in refinements if value)
            if refinement:
                arguments["fieldQueries"] = [{"field": "facets", "text": refinement}]

        payload = await self._call_tool("searchProducts", arguments)
        products = [self._product_from_search(row) for row in payload.get("results", [])]
        products = [product for product in products if product is not None]
        if filters and filters.min_rating is not None:
            products = [
                product
                for product in products
                if product.rating is not None and product.rating >= filters.min_rating
            ]
        if filters:
            products = self._sort(products, filters.sort)

        for product in products:
            details = ProductDetails.model_validate(product.model_dump())
            self.products[product.product_id] = details
        return products[:limit]

    async def get_product_details(
        self, session: ShoppingSessionContext, product_id: str
    ) -> ProductDetails | None:
        del session
        payload = await self._call_tool(
            "productDataTool",
            {"partNumbers": [product_id], "fields": SEARCH_FIELDS},
        )
        rows = payload.get("results", [])
        if not rows or rows[0].get("status") != "retrieved":
            return None
        entry = rows[0].get("productEntry") or {}
        fields = dict(entry.get("fields") or {})
        cached = self.products.get(product_id)
        if cached is not None:
            # Product data is authoritative when present; the grounded search result only
            # fills a field that the details service omitted.
            fields.setdefault("partNumber", cached.product_id)
            fields.setdefault("name", cached.title)
            fields.setdefault("price", cached.price)
        details = self._details_from_fields(fields, fallback_id=product_id)
        if details is None:
            return None
        self.products[details.product_id] = details
        return details

    def product(self, product_id: str) -> ProductDetails | None:
        return self.products.get(product_id)

    def reset_session(self, session_id: str) -> None:
        self._carts.pop(session_id, None)
        self._cart_locks.pop(session_id, None)

    def recent_orders(self, limit: int = 6) -> list[Order]:
        del limit
        return []

    async def get_cart(self, session: ShoppingSessionContext) -> Cart:
        state = self._carts.get(session.session_id)
        if state is None:
            return Cart()
        payload = await self._ucp.get_checkout(state.checkout_id)
        self._sync_state(state, payload)
        return self._cart_from_checkout(payload)

    async def add_to_cart(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        async with self._lock(session.session_id):
            state = self._carts.get(session.session_id)
            if state is None:
                quantities = {product_id: quantity}
                state, payload = await self._create_cart(quantities)
                self._carts[session.session_id] = state
            else:
                quantities = dict(state.quantities)
                quantities[product_id] = quantities.get(product_id, 0) + quantity
                state, payload = await self._replace_cart(session.session_id, state, quantities)
            return self._cart_from_checkout(payload)

    async def update_cart_item(
        self, session: ShoppingSessionContext, product_id: str, quantity: int
    ) -> Cart:
        async with self._lock(session.session_id):
            state = self._carts.get(session.session_id)
            if state is None or product_id not in state.quantities:
                raise NotOffered("That product is not in the cart")
            quantities = dict(state.quantities)
            quantities[product_id] = quantity
            state, payload = await self._replace_cart(session.session_id, state, quantities)
            return self._cart_from_checkout(payload)

    async def remove_from_cart(self, session: ShoppingSessionContext, product_id: str) -> Cart:
        async with self._lock(session.session_id):
            state = self._carts.get(session.session_id)
            if state is None or product_id not in state.quantities:
                raise NotOffered("That product is not in the cart")
            quantities = dict(state.quantities)
            del quantities[product_id]
            if not quantities:
                await self._ucp.cancel_checkout(state.checkout_id)
                self._carts.pop(session.session_id, None)
                return Cart()
            state, payload = await self._replace_cart(session.session_id, state, quantities)
            return self._cart_from_checkout(payload)

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._cart_locks.setdefault(session_id, asyncio.Lock())

    async def _create_cart(self, quantities: dict[str, int]) -> tuple[_CartState, dict[str, Any]]:
        payload = await self._ucp.create_checkout(quantities)
        checkout_id = str(payload.get("id") or "")
        actual = self._quantities_from_checkout(payload)
        if payload.get("status") == "canceled" or not checkout_id or actual != quantities:
            if checkout_id and payload.get("status") != "canceled":
                try:
                    await self._ucp.cancel_checkout(checkout_id)
                except Exception:
                    logger.warning("failed to cancel rejected replacement checkout")
            raise NotOffered("One or more requested products could not be added to the cart")
        return _CartState(checkout_id=checkout_id, quantities=actual), payload

    async def _replace_cart(
        self, session_id: str, old_state: _CartState, quantities: dict[str, int]
    ) -> tuple[_CartState, dict[str, Any]]:
        # QAT's checkout-session PUT recalculates but does not currently mutate lines.
        # Create the complete desired cart first, then swap local state before best-effort
        # cleanup so a cleanup outage cannot lose the customer's new cart.
        new_state, payload = await self._create_cart(quantities)
        self._carts[session_id] = new_state
        try:
            await self._ucp.cancel_checkout(old_state.checkout_id)
        except Exception:
            logger.warning("failed to cancel superseded checkout session")
        return new_state, payload

    @staticmethod
    def _sync_state(state: _CartState, payload: dict[str, Any]) -> None:
        state.quantities = ChewyStorefrontBackend._quantities_from_checkout(payload)

    @staticmethod
    def _quantities_from_checkout(payload: dict[str, Any]) -> dict[str, int]:
        quantities: dict[str, int] = {}
        for line in payload.get("line_items") or []:
            item = line.get("item") or {}
            product_id = str(item.get("id") or "")
            quantity = line.get("quantity")
            if product_id and isinstance(quantity, int) and quantity > 0:
                quantities[product_id] = quantity
        return quantities

    def _cart_from_checkout(self, payload: dict[str, Any]) -> Cart:
        items: list[CartItem] = []
        for line in payload.get("line_items") or []:
            item = line.get("item") or {}
            product_id = str(item.get("id") or "")
            quantity = line.get("quantity")
            price_cents = item.get("price")
            if not product_id or not isinstance(quantity, int) or quantity < 1:
                continue
            cached = self.products.get(product_id)
            title = str(item.get("title") or (cached.title if cached else product_id))
            price = price_cents / 100 if isinstance(price_cents, int | float) else 0.0
            items.append(
                CartItem(
                    product_id=product_id,
                    title=title,
                    price=price,
                    quantity=quantity,
                    image_url=item.get("image_url") or (cached.image_url if cached else None),
                )
            )
        return Cart(items=items, currency=str(payload.get("currency") or "USD"))

    async def get_preferences(self, session: ShoppingSessionContext) -> UserPreferences:
        return UserPreferences(user_id=session.user_id, display_name="Guest")

    async def get_orders(self, session: ShoppingSessionContext, limit: int = 5) -> list[Order]:
        del session, limit
        return []

    async def get_order(self, session: ShoppingSessionContext, order_id: str) -> Order | None:
        del session, order_id
        return None

    async def search_policies(self, session: ShoppingSessionContext, query: str) -> list[Policy]:
        del session, query
        return []

    async def get_fulfillment_options(
        self, session: ShoppingSessionContext, product_ids: list[str]
    ) -> list[FulfillmentOption]:
        del session, product_ids
        return []

    @classmethod
    def _product_from_search(cls, row: dict[str, Any]) -> Product | None:
        fields = (row.get("fields") or {}).get("fields") or {}
        product_id = str(row.get("partNumber") or fields.get("partNumber") or "").strip()
        title = str(row.get("name") or fields.get("name") or "").strip()
        if not product_id or not title or cls._float_or_none(fields.get("price")) is None:
            return None
        return Product.model_validate(cls._common_fields(fields, product_id, title))

    @classmethod
    def _details_from_fields(
        cls, fields: dict[str, Any], fallback_id: str
    ) -> ProductDetails | None:
        product_id = str(fields.get("partNumber") or fallback_id)
        title = str(fields.get("name") or f"Product {product_id}")
        if cls._float_or_none(fields.get("price")) is None:
            return None
        common = cls._common_fields(fields, product_id, title)
        benefits = cls._string_list(fields.get("key_benefits"))
        pros = [f"Positive: {item}" for item in cls._string_list(fields.get("pros"))]
        cons = [f"Trade-off: {item}" for item in cls._string_list(fields.get("cons"))]
        specs = {
            key: cls._display_value(fields[key])
            for key in ("ProductType", "PetType")
            if fields.get(key) is not None
        }
        return ProductDetails.model_validate(
            common
            | {
                "long_description": fields.get("longDescription"),
                "review_highlights": (pros + cons)[:8],
                "specs": specs,
                "attributes": common["attributes"]
                | ({"Key benefits": " • ".join(benefits)} if benefits else {}),
            }
        )

    @classmethod
    def _common_fields(cls, fields: dict[str, Any], product_id: str, title: str) -> dict[str, Any]:
        description = fields.get("longDescription")
        rating = cls._float_or_none(fields.get("averageOverallRating"))
        if rating is not None:
            rating = max(0.0, min(5.0, rating))
        product_types = cls._string_list(fields.get("ProductType"))
        pet_types = cls._string_list(fields.get("PetType"))
        attributes: dict[str, str] = {}
        if pet_types:
            attributes["Pet type"] = ", ".join(pet_types)
        if product_types:
            attributes["Product type"] = ", ".join(product_types)
        return {
            "product_id": product_id,
            "title": title,
            "brand": fields.get("manufacturer"),
            "price": cls._float_or_none(fields.get("price")),
            "currency": "USD",
            "rating": rating,
            "review_count": cls._int_or_none(fields.get("totalReviewCount")),
            "category": product_types[0] if product_types else None,
            "attributes": attributes,
            "in_stock": bool(fields.get("inStock", True)),
            "short_description": cls._short_description(description),
        }

    @staticmethod
    def _sort(products: list[Product], order: str) -> list[Product]:
        if order == "price_asc":
            return sorted(products, key=lambda product: product.price)
        if order == "price_desc":
            return sorted(products, key=lambda product: product.price, reverse=True)
        if order == "rating":
            return sorted(products, key=lambda product: product.rating or 0, reverse=True)
        return products

    @staticmethod
    def _short_description(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        clean = value.strip()
        return clean if len(clean) <= 240 else clean[:237].rstrip() + "..."

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if item is not None]
        return [str(value)] if value is not None else []

    @staticmethod
    def _display_value(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        if isinstance(value, dict):
            return json.dumps(value, sort_keys=True)
        return str(value)

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
