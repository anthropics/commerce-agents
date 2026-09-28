# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime

import pytest

from chewy.api.agent_config import build_shopping_config
from chewy.api.backend import ChewyStorefrontBackend
from shopping_agent import SearchFilters, ShoppingSessionContext


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


def test_agent_exposes_only_read_only_catalog_capabilities() -> None:
    absent = build_shopping_config().absent_tools()

    assert {
        "get_cart",
        "add_to_cart",
        "update_cart_item",
        "remove_from_cart",
        "checkout",
        "get_orders",
        "get_order_status",
        "search_policies",
        "get_fulfillment_options",
    } <= absent


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
