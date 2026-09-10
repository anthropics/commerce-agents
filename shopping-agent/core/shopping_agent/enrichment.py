# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The built-in components: each payload joined to the session's catalog records, the
cart, or an order before it reaches the host. Ids without session provenance are
dropped and reported; a component left with nothing canonical to show is refused.
"""

from __future__ import annotations

from typing import Any

from commerce_common.presentation import (
    CHIPS_COMPONENT,
    CHIPS_TOOL,
    EnrichmentContext,
    PresentationComponent,
    PresentationRefused,
    PresentSuggestionsPayload,
)

from .fencing import STOREFRONT_FENCE
from .gates import PROVENANCE_GATE
from .serialization import cart_payload
from .tools.presentation import (
    CheckoutPayload,
    PresentComparisonPayload,
    PresentDisclosurePayload,
    PresentGuidePayload,
    PresentOrderStatusPayload,
    PresentPlanPayload,
    PresentProductsPayload,
)
from .types import Product, ShoppingSessionState


def _record(product: Product) -> dict[str, Any]:
    return product.model_dump(exclude_none=True)


def _resolve(
    state: ShoppingSessionState, product_ids: list[str], dropped: list[str]
) -> list[dict[str, Any]]:
    records = []
    for product_id in product_ids:
        product = state.seen_products.get(product_id)
        (records.append(_record(product)) if product else dropped.append(product_id))
    return records


def _note_dropped(context: EnrichmentContext, dropped: list[str]) -> None:
    if dropped:
        context.notes.append(
            f"Skipped unknown product_ids not seen in this session: {', '.join(dropped)}."
        )


def comparison_price_delta(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The spread between the cheapest and dearest enriched entries; None when fewer
    than two carry a price, the spread is zero, or the currencies differ."""
    if len({entry.get("product", {}).get("currency", "USD") for entry in entries}) > 1:
        return None
    priced = sorted(
        (entry["product"]["price"], entry["product_id"])
        for entry in entries
        if entry.get("product", {}).get("price") is not None
    )
    if len(priced) < 2:
        return None
    (low_price, low_id), (high_price, high_id) = priced[0], priced[-1]
    amount = round(high_price - low_price, 2)
    if amount <= 0:
        return None
    return {
        "amount": amount,
        "low_product_id": low_id,
        "low_price": low_price,
        "high_product_id": high_id,
        "high_price": high_price,
    }


def _clean(text: str | None) -> str | None:
    """Model-authored card text goes to the host's renderer through the same payload as
    third-party records, so it gets the same treatment: no fence markers, no invisible
    characters, no forged turn indicators."""
    return None if text is None else STOREFRONT_FENCE.sanitize_text(text, 200)


async def enrich_products(
    payload: PresentProductsPayload, context: EnrichmentContext
) -> dict[str, Any]:
    dropped: list[str] = []
    items = []
    for pick in payload.picks:
        product = context.state.seen_products.get(pick.product_id)
        if product is None:
            dropped.append(pick.product_id)
            continue
        items.append({"product": _record(product), "reason": _clean(pick.reason)})
    if not items:
        raise PresentationRefused(
            "None of those product_ids came from this session's catalog results. "
            "Search first and pick from the results.",
            PROVENANCE_GATE,
        )
    _note_dropped(context, dropped)
    enriched = payload.model_dump(exclude_none=True, exclude={"picks"})
    enriched["items"] = items
    return enriched


async def enrich_comparison(
    payload: PresentComparisonPayload, context: EnrichmentContext
) -> dict[str, Any]:
    dropped: list[str] = []
    entries = []
    for entry in payload.entries:
        product = context.state.seen_products.get(entry.product_id)
        if product is None:
            dropped.append(entry.product_id)
            continue
        entries.append(entry.model_dump(exclude_none=True) | {"product": _record(product)})
    if len(entries) < 2:
        raise PresentationRefused(
            "A comparison needs at least 2 products whose product_ids came from this "
            "session's catalog results.",
            PROVENANCE_GATE,
        )
    _note_dropped(context, dropped)
    enriched = payload.model_dump(exclude_none=True)
    enriched["entries"] = entries
    if (delta := comparison_price_delta(entries)) is not None:
        enriched["price_delta"] = delta
    return enriched


async def enrich_plan(payload: PresentPlanPayload, context: EnrichmentContext) -> dict[str, Any]:
    dropped: list[str] = []
    enriched = payload.model_dump(exclude_none=True)
    enriched["steps"] = [
        {
            "label": step.label,
            "detail": step.detail,
            "products": _resolve(context.state, step.product_ids, dropped),
        }
        for step in payload.steps
    ]
    _note_dropped(context, dropped)
    return enriched


async def enrich_guide(payload: PresentGuidePayload, context: EnrichmentContext) -> dict[str, Any]:
    dropped: list[str] = []
    enriched = payload.model_dump(exclude_none=True, exclude={"related_product_ids"})
    enriched["related_products"] = _resolve(context.state, payload.related_product_ids, dropped)
    _note_dropped(context, dropped)
    return enriched


async def enrich_order_status(
    payload: PresentOrderStatusPayload, context: EnrichmentContext
) -> dict[str, Any]:
    order = await context.backend.get_order(context.session, payload.order_id)
    if order is None:
        raise PresentationRefused(f"No order with id {payload.order_id} — look it up first.")
    enriched = payload.model_dump(exclude_none=True)
    enriched["order"] = order.model_dump(mode="json", exclude_none=True)
    return enriched


async def enrich_checkout(payload: CheckoutPayload, context: EnrichmentContext) -> dict[str, Any]:
    cart = await context.backend.get_cart(context.session)
    if not cart.items:
        raise PresentationRefused("The cart is empty — nothing to check out.")
    enriched = payload.model_dump(exclude_none=True)
    enriched["cart"] = cart_payload(cart)
    handoffs = await context.backend.checkout_handoff(context.session, cart)
    if handoffs:
        enriched["handoffs"] = [h.model_dump(exclude_none=True) for h in handoffs]
    return enriched


async def enrich_disclosure(
    payload: PresentDisclosurePayload, context: EnrichmentContext
) -> dict[str, Any]:
    if payload.product_id not in context.state.seen_products:
        raise PresentationRefused(
            "That product_id didn't come from this session's catalog results. Search or "
            "look it up first, then disclose it.",
            PROVENANCE_GATE,
        )
    disclosure = await context.backend.get_disclosure(context.session, payload.product_id)
    if disclosure is None:
        raise PresentationRefused(
            "No disclosure exists for that product — answer from search_policies content instead."
        )
    return disclosure.model_dump(exclude_none=True)


# -- Partial payloads while a call is still streaming ---------------------------------


def _seen(state: ShoppingSessionState, product_id: Any) -> dict[str, Any] | None:
    product = state.seen_products.get(product_id) if isinstance(product_id, str) else None
    return None if product is None else _record(product)


def _seen_all(state: ShoppingSessionState, product_ids: Any) -> list[dict[str, Any]]:
    if not isinstance(product_ids, list):
        return []
    return [record for pid in product_ids if (record := _seen(state, pid)) is not None]


def partial_products(data: dict[str, Any], state: ShoppingSessionState) -> dict[str, Any]:
    items = []
    for pick in data.get("picks") or []:
        if isinstance(pick, dict) and (record := _seen(state, pick.get("product_id"))):
            item: dict[str, Any] = {"product": record}
            if pick.get("reason"):
                item["reason"] = _clean(str(pick["reason"]))
            items.append(item)
    payload: dict[str, Any] = {"items": items}
    for key in ("title", "layout"):
        if data.get(key):
            payload[key] = data[key]
    return payload


def partial_plan(data: dict[str, Any], state: ShoppingSessionState) -> dict[str, Any]:
    steps = [
        {
            "label": step["label"],
            "detail": step.get("detail"),
            "products": _seen_all(state, step.get("product_ids")),
        }
        for step in data.get("steps") or []
        if isinstance(step, dict) and step.get("label")
    ]
    payload: dict[str, Any] = {"title": data.get("title") or "", "steps": steps}
    if data.get("intro"):
        payload["intro"] = data["intro"]
    return payload


def partial_comparison(data: dict[str, Any], state: ShoppingSessionState) -> dict[str, Any]:
    entries = []
    for entry in data.get("entries") or []:
        if isinstance(entry, dict) and (record := _seen(state, entry.get("product_id"))):
            entries.append(
                {
                    "product_id": entry.get("product_id"),
                    "pros": entry.get("pros") or [],
                    "cons": entry.get("cons") or [],
                    "best_for": entry.get("best_for"),
                    "product": record,
                }
            )
    payload: dict[str, Any] = {"entries": entries, "dimensions": data.get("dimensions") or []}
    for key in ("title", "recommended_product_id"):
        if data.get(key):
            payload[key] = data[key]
    return payload


def partial_guide(data: dict[str, Any], state: ShoppingSessionState) -> dict[str, Any] | None:
    del state  # a guide's sections are the model's; nothing joins from the session
    sections = [
        {"heading": section["heading"], "body": section["body"]}
        for section in data.get("sections") or []
        if isinstance(section, dict) and section.get("heading") and section.get("body")
    ]
    if not data.get("title") and not sections:
        return None
    return {"title": data.get("title") or "", "sections": sections}


def _component(name: str, component: str, model: type, enrich: Any = None, partial: Any = None):
    return PresentationComponent(
        name=name, component=component, payload_model=model, enrich=enrich, enrich_partial=partial
    )


PRESENTATION_COMPONENTS: dict[str, PresentationComponent] = {
    spec.name: spec
    for spec in (
        _component(
            "present_products",
            "products",
            PresentProductsPayload,
            enrich_products,
            partial_products,
        ),
        _component(
            "present_comparison",
            "comparison",
            PresentComparisonPayload,
            enrich_comparison,
            partial_comparison,
        ),
        _component("present_plan", "plan", PresentPlanPayload, enrich_plan, partial_plan),
        _component("present_guide", "guide", PresentGuidePayload, enrich_guide, partial_guide),
        _component(
            "present_order_status", "order_status", PresentOrderStatusPayload, enrich_order_status
        ),
        _component("checkout", "checkout", CheckoutPayload, enrich_checkout),
        _component(CHIPS_TOOL, CHIPS_COMPONENT, PresentSuggestionsPayload),
        _component("present_disclosure", "disclosure", PresentDisclosurePayload, enrich_disclosure),
    )
}
