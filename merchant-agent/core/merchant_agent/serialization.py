# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The search_listings result, built once so every path returns the same bytes. The
header is the one runtime-authored line outside the fence: the result count and a fixed
sentence on how to read the results as text matches (the zero-result form adds that ids
resolve through get_listing). Only the count varies."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from commerce_common.fencing import MAX_FENCED_CHARS

from .fencing import MERCHANT_FENCE
from .types import InventoryAlert, Listing, ListingDetails, PricingContext

_VARIANT_ALWAYS = ("listing_id", "option_values", "price", "stock", "status")
_PRICING_ALWAYS = ("listing_id", "option_values", "current_price", "unit_cost", "margin_pct")


def listing_record(listing: Listing) -> dict[str, Any]:
    """A listing as the model reads it; a plain listing carries no option keys."""
    row = listing.model_dump(mode="json", exclude_none=True)
    for key in ("options", "option_values", "variants"):
        if not row.get(key):
            row.pop(key, None)
    return row


def variant_row(variant: Listing, family: dict[str, Any]) -> dict[str, Any]:
    """A variant inside its family's record: its id, option values, price, stock,
    status, and only the fields and attributes where it differs from the family."""
    row = listing_record(variant)
    for key in ("variant_of", "options", "variants"):
        row.pop(key, None)
    attributes = {
        k: v for k, v in variant.attributes.items() if (family.get("attributes") or {}).get(k) != v
    }
    kept = {
        k: v
        for k, v in row.items()
        if k in _VARIANT_ALWAYS or (k != "attributes" and family.get(k) != v)
    }
    lead = {"listing_id": kept.pop("listing_id"), "option_values": kept.pop("option_values", {})}
    return lead | kept | ({"attributes": attributes} if attributes else {})


def listing_details_payload(details: ListingDetails) -> dict[str, Any]:
    """The get_listing result: the record, with a family's variants as compact rows."""
    family = listing_record(details)
    family.pop("variants", None)
    rows = [variant_row(v, family) for v in details.variants]
    return family | ({"variants": rows} if rows else {})


SEARCH_EMPTY_HEADER = (
    "Search returned 0 results: no listing in this store matched the query. Report "
    "that plainly — do not describe a listing that was not returned. Note: search "
    "matches listing text, not ids; resolve a listing id with get_listing."
)


def search_result_header(count: int) -> str:
    if count == 0:
        return SEARCH_EMPTY_HEADER
    return (
        f"Search returned {count} result(s): closest text matches — confirm a listing "
        "is the one intended before reporting on or staging against it."
    )


def search_result_text(
    query: str, listings: Sequence[Listing], max_chars: int = MAX_FENCED_CHARS
) -> str:
    """The whole search_listings result: the header line, then the fenced payload."""
    payload = {
        "query": query,
        "result_count": len(listings),
        "results": [listing_record(listing) for listing in listings],
    }
    header = search_result_header(len(listings))
    return header + "\n" + MERCHANT_FENCE.fence_payload(payload, max_chars)


def pricing_context_payload(context: PricingContext) -> dict[str, Any]:
    """The get_pricing_context result; a family's per-variant contexts drop the caps and
    currency they share with the family row."""
    payload = context.model_dump(exclude_none=True, exclude={"variants"})
    if not payload.get("option_values"):
        payload.pop("option_values", None)
    rows = []
    for variant in context.variants:
        row = variant.model_dump(exclude_none=True, exclude={"variants"})
        rows.append({k: v for k, v in row.items() if k in _PRICING_ALWAYS or payload.get(k) != v})
    return payload | ({"variants": rows} if rows else {})


def alerts_payload(alerts: Sequence[InventoryAlert]) -> dict[str, Any]:
    """The get_inventory_alerts result. The counts lead because they are what an answer
    quotes: a bare list leaves the assistant to tally its own totals, and a total it
    derived is a figure no tool returned."""
    low = [alert for alert in alerts if alert.kind == "low_stock"]
    return {
        "alert_count": len(alerts),
        "low_stock_count": len(low),
        "out_of_stock_count": sum(1 for alert in low if alert.stock == 0),
        "slow_mover_count": sum(1 for alert in alerts if alert.kind == "slow_mover"),
        "alerts": [alert_record(alert) for alert in alerts],
    }


def alert_record(alert: InventoryAlert) -> dict[str, Any]:
    row = alert.model_dump(exclude_none=True)
    if not row.get("option_values"):
        row.pop("option_values", None)
    return row
