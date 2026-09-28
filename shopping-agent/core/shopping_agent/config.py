# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Per-deployment settings for the shopping agent; per-request values travel in
``ShoppingSessionContext``. Sections continue ``BaseAgentConfig``'s: capabilities, cart
caps, grounding gates."""

from __future__ import annotations

from pydantic import Field

from commerce_common.config import BaseAgentConfig, ThinkingEffort


class ShoppingAgentConfig(BaseAgentConfig):
    assistant_name: str = "the shopping assistant"
    brand_voice: str = "warm, concise, and plain about trade-offs"
    model: str = "claude-sonnet-5"
    thinking_effort: ThinkingEffort | None = "low"

    # -- Capabilities (prompt). `domain_search_notes` is one extra search rule for the
    # domain (a travel deployment names its date filter); `enable_disclosures` registers
    # present_disclosure over StorefrontBackend.get_disclosure.
    domain_search_notes: str = ""
    enable_disclosures: bool = False

    # -- Systems the store has. Search and product details are the floor; each switch
    # below, turned off, removes that system's tools, prompt lines, and grounding rule on
    # every path, for a store that has no such system at all. A system that exists and
    # is not wired yet stays on: its backend method raises and the tool answers that it
    # is unavailable.
    enable_cart: bool = True
    enable_checkout: bool = True
    enable_orders: bool = True
    enable_policies: bool = True
    enable_fulfillment: bool = True

    # -- Cart caps, enforced by the gates on every path.
    max_quantity_per_item: int = Field(default=24, ge=1)
    max_cart_lines: int = Field(default=100, ge=1)

    # -- Grounding gates (the runtimes read them): each forces one read on a turn's first
    # iteration when the message matches. Deployments extend the lexicons with their
    # domain's vocabulary. The id patterns cap at four digits so five-digit order ids
    # ground through orders; "delivered" is absent from the order terms because it
    # occurs in ordinary shopping phrasing.
    policy_grounding_gate: bool = True
    policy_intent_terms: tuple[str, ...] = (
        "return",
        "returns",
        "refund",
        "refunds",
        "exchange",
        "exchanges",
        "warranty",
        "guarantee",
        "cancel",
        "cancellation",
        "restocking",
        "fee",
        "fees",
        "shipping cost",
        "shipping costs",
        "delivery cost",
        "price match",
        "price lock",
        "membership",
        "subscription",
        "contract",
        "policy",
        "policies",
        "terms",
    )
    policy_intent_cues: tuple[str, ...] = (
        "?",
        "how",
        "what",
        "when",
        "can i",
        "could i",
        "do you",
        "does",
        "is there",
        "tell me",
        "explain",
        "how long",
        "how much",
    )
    order_grounding_gate: bool = True
    order_intent_terms: tuple[str, ...] = (
        "order",
        "orders",
        "delivery",
        "delivery address",
        "package",
        "parcel",
        "shipment",
        "tracking",
        "tracking number",
    )
    order_intent_cues: tuple[str, ...] = (
        "?",
        "where",
        "when",
        "status",
        "cancel",
        "change",
        "return",
        "refund",
        "late",
        "arrive",
        "arrived",
        "track",
        "missing",
        "damaged",
        "hasn't",
        "delayed",
    )
    catalog_grounding_gate: bool = True
    product_id_patterns: tuple[str, ...] = (
        r"\b[A-Z]{2,4}-\d{3,4}\b",
        r"\b[A-Z]{2,4}-[A-Z]{2,6}-\d{2,4}(?:-[A-Z0-9]{2,6})?\b",
    )

    def absent_tools(self) -> frozenset[str]:
        """Names `build_tools` leaves out for the systems switched off above."""
        names: set[str] = set()
        if not self.enable_cart:
            names |= {"get_cart", "add_to_cart", "update_cart_item", "remove_from_cart", "checkout"}
        elif not self.enable_checkout:
            names.add("checkout")
        if not self.enable_orders:
            names |= {"get_orders", "get_order_status", "present_order_status"}
        if not self.enable_policies:
            names.add("search_policies")
        if not self.enable_fulfillment:
            names.add("get_fulfillment_options")
        return frozenset(names)
