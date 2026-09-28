# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Capabilities enabled for the Chewy product-search and guest-cart POC."""

from shopping_agent import ShoppingAgentConfig


def build_shopping_config() -> ShoppingAgentConfig:
    return ShoppingAgentConfig(
        brand_name="Chewy",
        assistant_name="Chewy Shopping Assistant",
        brand_voice="friendly, concise, practical, and specific about product trade-offs",
        domain_search_notes=(
            "This catalog contains pet products. Ask for the pet type and important constraints "
            "when the request is ambiguous; use price and rating filters when the shopper states them."
        ),
        enable_cart=True,
        enable_checkout=False,
        enable_orders=False,
        enable_policies=False,
        enable_fulfillment=False,
        enable_memory=False,
    )
