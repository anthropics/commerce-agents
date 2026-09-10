# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The shopping agent's tools, one handler each, over the shared executor frame. The
Messages API runtime, the SDK toolset, and the MCP server all execute through this
class, so a tool result is the same bytes on every path.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from commerce_common.execution import BaseToolExecutor, Handler, clamp_limit, parse_argument
from commerce_common.memory import MemoryRuntime, memory_fact_payload
from commerce_common.presentation import PresentationExtension
from commerce_common.skills import SkillRegistry
from commerce_common.streaming import ToolOutcome

from .backend import NotOffered, StorefrontBackend, Unavailable
from .config import ShoppingAgentConfig
from .enrichment import PRESENTATION_COMPONENTS
from .fencing import STOREFRONT_FENCE
from .gates import (
    gated_add_to_cart,
    gated_remove_from_cart,
    gated_update_cart_item,
    remember_order_items,
)
from .memory import SHOPPING_MEMORY_EXTRACTION_PROMPT
from .serialization import (
    cart_payload,
    fulfillment_payload,
    order_payload,
    orders_payload,
    policies_payload,
    product_details_payload,
    search_result_text,
)
from .types import CartQuantity, SearchFilters, ShoppingSessionContext, ShoppingSessionState

MAX_ORDERS = 20
MAX_FULFILLMENT_IDS = 20


def build_memory(
    config: ShoppingAgentConfig, store: Any, write_filter: Any = None
) -> MemoryRuntime:
    """The shopping agent's :class:`MemoryRuntime`: the store under this config, keyed
    by user id, extracting under the shopping prompt."""
    return MemoryRuntime.build(
        config,
        store,
        fence=STOREFRONT_FENCE,
        extraction_prompt=SHOPPING_MEMORY_EXTRACTION_PROMPT,
        write_filter=write_filter,
    )


class ShoppingToolExecutor(BaseToolExecutor):
    """``inline_context`` is set by the paths that have no per-request context block
    (SDK, MCP): ``get_preferences`` then also carries the tier-one memory and the
    account context the Messages API runtime injects into the prompt."""

    fence = STOREFRONT_FENCE
    components = PRESENTATION_COMPONENTS
    displayed_text = "Displayed to the customer."
    unavailable_text = (
        "{name} is temporarily unavailable. Work with what you already have or let the "
        "customer know."
    )
    not_offered_text = "{detail} is not something this store offers; say so plainly."
    sold_out_text = (
        "Nothing was added: {detail}. Tell the customer, offer what the message names as "
        "available, and add that only once they choose it."
    )
    absent_text = "{name} is not something this store offers; say so plainly and do not suggest it."

    def __init__(
        self,
        *,
        backend: StorefrontBackend,
        config: ShoppingAgentConfig,
        skills: SkillRegistry,
        session: ShoppingSessionContext,
        state: ShoppingSessionState,
        memory: MemoryRuntime | None = None,
        extensions: Sequence[PresentationExtension] = (),
        inline_context: bool = False,
    ) -> None:
        self._inline_context = inline_context
        super().__init__(
            backend=backend,
            config=config,
            skills=skills,
            session=session,
            state=state,
            memory=memory or build_memory(config, None),
            extensions=extensions,
        )

    @property
    def memory_subject(self) -> str:
        return self._session.user_id

    def domain_error(self, error: Exception) -> ToolOutcome | None:
        # The message is backend text arriving outside the fence: sanitized and capped.
        detail = self._sanitize(str(error), 200)
        if isinstance(error, Unavailable):
            return ToolOutcome.error(self.sold_out_text.format(detail=detail or "unavailable"))
        if isinstance(error, NotOffered):
            return ToolOutcome.error(self.not_offered_text.format(detail=detail or "This"))
        return None

    def handlers(self) -> dict[str, Handler]:
        return {
            "search_products": self._search_products,
            "get_product_details": self._get_product_details,
            "get_cart": self._get_cart,
            "add_to_cart": self._add_to_cart,
            "update_cart_item": self._update_cart_item,
            "remove_from_cart": self._remove_from_cart,
            "get_preferences": self._get_preferences,
            "get_orders": self._get_orders,
            "get_order_status": self._get_order_status,
            "search_policies": self._search_policies,
            "get_fulfillment_options": self._get_fulfillment_options,
        }

    # -- catalog ---------------------------------------------------------------------

    async def _search_products(self, tool_input: dict[str, Any]) -> ToolOutcome:
        query = self._sanitize(tool_input.get("query", ""), 300)
        filters = (
            parse_argument(SearchFilters, tool_input["filters"])
            if tool_input.get("filters")
            else None
        )
        limit = self._search_limit(tool_input.get("limit"))
        products = await self._backend.search_products(self._session, query, filters, limit)
        self._state.remember_products(products)
        return ToolOutcome(search_result_text(query, products, self._config.max_fenced_chars))

    async def _get_product_details(self, tool_input: dict[str, Any]) -> ToolOutcome:
        product_id = str(tool_input.get("product_id", ""))
        details = await self._backend.get_product_details(self._session, product_id)
        if details is None:
            return ToolOutcome.error(f"No product with id {product_id}.")
        # The variants enter provenance with the record, so the cart takes their ids.
        self._state.remember_products([details, *details.variants])
        return self._fenced(product_details_payload(details))

    # -- cart --------------------------------------------------------------------------

    async def _get_cart(self, _: dict[str, Any]) -> ToolOutcome:
        return self._fenced(cart_payload(await self._backend.get_cart(self._session)))

    async def _add_to_cart(self, tool_input: dict[str, Any]) -> ToolOutcome:
        return await gated_add_to_cart(
            backend=self._backend,
            config=self._config,
            session=self._session,
            state=self._state,
            product_id=str(tool_input.get("product_id", "")),
            quantity=self._quantity(tool_input),
        )

    async def _update_cart_item(self, tool_input: dict[str, Any]) -> ToolOutcome:
        return await gated_update_cart_item(
            backend=self._backend,
            config=self._config,
            session=self._session,
            state=self._state,
            product_id=str(tool_input.get("product_id", "")),
            quantity=self._quantity(tool_input),
        )

    @staticmethod
    def _quantity(tool_input: dict[str, Any]) -> int:
        """The model's quantity as a whole number, or an invalid-arguments answer naming
        the field: a string or a list here is the model's mistake, not an outage."""
        parsed = parse_argument(CartQuantity, {"quantity": tool_input.get("quantity")})
        return parsed.quantity if parsed.quantity is not None else 1

    async def _remove_from_cart(self, tool_input: dict[str, Any]) -> ToolOutcome:
        return await gated_remove_from_cart(
            backend=self._backend,
            session=self._session,
            state=self._state,
            product_id=str(tool_input.get("product_id", "")),
        )

    # -- customer context, orders, policies, fulfillment ---------------------------------

    async def _get_preferences(self, _: dict[str, Any]) -> ToolOutcome:
        prefs = await self._backend.get_preferences(self._session)
        payload = prefs.model_dump(exclude_none=True)
        if self._inline_context:
            facts = await self._memory.tier_one(self._session.user_id)
            payload["saved_memory"] = [memory_fact_payload(f) for f in facts] or "none"
            account = await self._backend.get_account_context(self._session)
            if account is not None:
                payload["account"] = account
        return self._fenced(payload)

    async def _get_orders(self, tool_input: dict[str, Any]) -> ToolOutcome:
        limit = clamp_limit(tool_input.get("limit"), 5, MAX_ORDERS)
        orders = await self._backend.get_orders(self._session, limit)
        remember_order_items(self._state, orders)
        return self._fenced(orders_payload(orders))

    async def _get_order_status(self, tool_input: dict[str, Any]) -> ToolOutcome:
        order_id = str(tool_input.get("order_id", ""))
        order = await self._backend.get_order(self._session, order_id)
        if order is None:
            return ToolOutcome.error(f"No order with id {order_id}.")
        remember_order_items(self._state, [order])
        return self._fenced(order_payload(order))

    async def _search_policies(self, tool_input: dict[str, Any]) -> ToolOutcome:
        query = self._sanitize(tool_input.get("query", ""), 200)
        policies = await self._backend.search_policies(self._session, query)
        return self._fenced(policies_payload(policies))

    async def _get_fulfillment_options(self, tool_input: dict[str, Any]) -> ToolOutcome:
        product_ids = [str(pid) for pid in tool_input.get("product_ids") or []][
            :MAX_FULFILLMENT_IDS
        ]
        options = await self._backend.get_fulfillment_options(self._session, product_ids)
        return self._fenced(fulfillment_payload(options))
