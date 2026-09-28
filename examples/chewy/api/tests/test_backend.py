# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime

import pytest

from chewy.api.agent_config import build_shopping_config
from chewy.api.backend import ChewyStorefrontBackend
from shopping_agent import NotOffered, SearchFilters, ShoppingSessionContext


class FakeUcpClient:
    def __init__(self) -> None:
        self.checkout_id = "checkout-1"
        self.quantities: dict[str, int] = {}
        self.calls: list[tuple[str, object]] = []
        self.cancel_create = False

    async def create_checkout(self, quantities: dict[str, int]) -> dict:
        self.calls.append(("create", quantities))
        self.quantities = dict(quantities)
        return self.payload(status="canceled" if self.cancel_create else "incomplete")

    async def get_checkout(self, checkout_id: str) -> dict:
        self.calls.append(("get", checkout_id))
        return self.payload()

    async def cancel_checkout(self, checkout_id: str) -> dict:
        self.calls.append(("cancel", checkout_id))
        return self.payload(status="canceled")

    def payload(self, status: str = "incomplete") -> dict:
        return {
            "id": self.checkout_id,
            "status": status,
            "currency": "USD",
            "line_items": [
                {
                    "id": str(index),
                    "item": {
                        "id": product_id,
                        "title": f"Product {product_id}",
                        "price": 2949,
                    },
                    "quantity": quantity,
                }
                for index, (product_id, quantity) in enumerate(self.quantities.items(), 1)
            ],
        }


def session() -> ShoppingSessionContext:
    return ShoppingSessionContext(
        session_id="session-1", user_id="guest", now=datetime(2026, 9, 27)
    )


@pytest.mark.asyncio
async def test_search_products_maps_search_agent_results_and_filters_rating() -> None:
    calls: list[tuple[str, dict]] = []

    async def call_tool(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return {
            "query": "dog food",
            "resultCount": 2,
            "results": [
                search_row("767758", "Dog Chow", 29.49, 4.4),
                search_row("91837", "Purina ONE", 17.78, 3.0),
            ],
        }

    backend = ChewyStorefrontBackend(call_tool=call_tool)
    products = await backend.search_products(
        session(),
        "dog food",
        SearchFilters(max_price=30, min_rating=4, sort="price_asc"),
        limit=3,
    )

    assert [product.product_id for product in products] == ["767758"]
    assert products[0].brand == "Dog Chow"
    assert products[0].price == 29.49
    assert backend.product("767758") is not None
    assert calls[0][0] == "searchProducts"
    assert {"field": "price", "op": "lte", "value": "30.0"} in calls[0][1]["filter"]["must"]


@pytest.mark.asyncio
async def test_product_details_maps_description_and_review_summaries() -> None:
    async def call_tool(name: str, arguments: dict) -> dict:
        assert name == "productDataTool"
        assert arguments["partNumbers"] == ["767758"]
        return {
            "results": [
                {
                    "status": "retrieved",
                    "productEntry": {
                        "fields": {
                            "partNumber": "767758",
                            "name": "Dog Chow Complete",
                            "manufacturer": "Dog Chow",
                            "price": 29.49,
                            "inStock": True,
                            "longDescription": "Complete adult dry dog food.",
                            "ProductType": ["Dry Food"],
                            "PetType": ["Dog"],
                            "key_benefits": ["Complete and balanced"],
                            "pros": ["Good value"],
                            "cons": ["Some dogs dislike the taste"],
                        }
                    },
                }
            ]
        }

    backend = ChewyStorefrontBackend(call_tool=call_tool)
    product = await backend.get_product_details(session(), "767758")

    assert product is not None
    assert product.long_description == "Complete adult dry dog food."
    assert product.attributes["Key benefits"] == "Complete and balanced"
    assert product.review_highlights == [
        "Positive: Good value",
        "Trade-off: Some dogs dislike the taste",
    ]


@pytest.mark.asyncio
async def test_cart_uses_guest_checkout_session_for_add_update_and_remove() -> None:
    ucp = FakeUcpClient()
    backend = ChewyStorefrontBackend(call_tool=unused_tool, ucp_client=ucp)

    added = await backend.add_to_cart(session(), "767758", 1)
    added_second = await backend.add_to_cart(session(), "91837", 2)
    updated = await backend.update_cart_item(session(), "767758", 3)
    one_left = await backend.remove_from_cart(session(), "91837")
    fetched = await backend.get_cart(session())
    removed = await backend.remove_from_cart(session(), "767758")

    assert added.items[0].price == 29.49
    assert {item.product_id for item in added_second.items} == {"767758", "91837"}
    assert updated.items[0].quantity == 3
    assert [item.product_id for item in one_left.items] == ["767758"]
    assert fetched.items[0].product_id == "767758"
    assert removed.items == []
    assert ucp.calls == [
        ("create", {"767758": 1}),
        ("create", {"767758": 1, "91837": 2}),
        ("cancel", "checkout-1"),
        ("create", {"767758": 3, "91837": 2}),
        ("cancel", "checkout-1"),
        ("create", {"767758": 3}),
        ("cancel", "checkout-1"),
        ("get", "checkout-1"),
        ("cancel", "checkout-1"),
    ]


@pytest.mark.asyncio
async def test_canceled_ucp_create_is_not_accepted_as_a_cart() -> None:
    ucp = FakeUcpClient()
    ucp.cancel_create = True
    backend = ChewyStorefrontBackend(call_tool=unused_tool, ucp_client=ucp)

    with pytest.raises(NotOffered, match="could not be added"):
        await backend.add_to_cart(session(), "767758", 1)

    assert session().session_id not in backend._carts


@pytest.mark.asyncio
async def test_replacement_create_failure_preserves_existing_cart() -> None:
    ucp = FakeUcpClient()
    backend = ChewyStorefrontBackend(call_tool=unused_tool, ucp_client=ucp)
    await backend.add_to_cart(session(), "767758", 1)
    ucp.cancel_create = True

    with pytest.raises(NotOffered, match="could not be added"):
        await backend.add_to_cart(session(), "91837", 1)

    assert backend._carts[session().session_id].quantities == {"767758": 1}
    assert ("cancel", "checkout-1") not in ucp.calls


@pytest.mark.asyncio
async def test_failed_cart_update_does_not_change_local_cart_state() -> None:
    ucp = FakeUcpClient()
    backend = ChewyStorefrontBackend(call_tool=unused_tool, ucp_client=ucp)
    await backend.add_to_cart(session(), "767758", 1)
    ucp.cancel_create = True

    with pytest.raises(NotOffered, match="could not be added"):
        await backend.update_cart_item(session(), "767758", 2)

    assert backend._carts[session().session_id].quantities == {"767758": 1}


@pytest.mark.asyncio
async def test_cart_rejects_update_for_product_not_in_cart() -> None:
    backend = ChewyStorefrontBackend(call_tool=unused_tool, ucp_client=FakeUcpClient())

    with pytest.raises(NotOffered, match="not in the cart"):
        await backend.update_cart_item(session(), "unknown", 2)


def test_agent_exposes_cart_but_not_checkout_or_account_capabilities() -> None:
    absent = build_shopping_config().absent_tools()

    assert not {"get_cart", "add_to_cart", "update_cart_item", "remove_from_cart"} & absent
    assert {
        "checkout",
        "get_orders",
        "get_order_status",
        "search_policies",
        "get_fulfillment_options",
    } <= absent


async def unused_tool(name: str, arguments: dict) -> dict:
    raise AssertionError(f"unexpected Search Agent call: {name} {arguments}")


def search_row(product_id: str, brand: str, price: float, rating: float) -> dict:
    return {
        "partNumber": product_id,
        "name": f"{brand} dry dog food",
        "fields": {
            "fields": {
                "partNumber": product_id,
                "name": f"{brand} dry dog food",
                "manufacturer": brand,
                "price": price,
                "averageOverallRating": rating,
                "totalReviewCount": 100,
                "inStock": True,
                "longDescription": "A dry dog food.",
                "ProductType": ["Dry Food"],
                "PetType": ["Dog"],
            }
        },
    }
