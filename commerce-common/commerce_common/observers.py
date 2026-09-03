# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Payments observability sink: the optional hook a host installs to emit an agent's
cart, checkout handoff, and applied merchant change to a downstream monitor. The default
is a no-op; a deployment provides its own implementation. The observer receives
non-PII summaries only (ids, hashes, amounts, currency) and never receives the model's
text or a customer record. Wiring into the executor is a separate concern: a host can
call the observer from its backend methods, or a role's executor subclass may forward
its own events to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CartUpdateEvent:
    """The cart's shape after a shopping-agent turn wrote it. ``cart_hash`` is a stable
    hash of the line ids and quantities, so a host correlates a repeated cart across
    turns without carrying its contents."""

    session_id: str
    cart_hash: str
    item_count: int
    total_amount: float
    currency: str


@dataclass(frozen=True)
class CheckoutHandoffEvent:
    """Emitted when the shopping agent's ``checkout`` tool renders the cart for the
    host to complete. ``intent_id`` is a stable id the runtime mints for this handoff;
    the host is expected to forward it (with ``session_id``) into the resulting payment
    provider's metadata so downstream reconciliation can attribute the charge to this
    session. Nothing here charges."""

    session_id: str
    intent_id: str
    cart_hash: str
    total_amount: float
    currency: str


@dataclass(frozen=True)
class MerchantChangeAppliedEvent:
    """Emitted when a staged merchant change moves from ``staged`` to ``applied`` on
    the host's approval surface. ``kind`` names the change class (``price``,
    ``restock``, ``promotion``, ``content``, ``pause``); ``item_count`` is the number
    of variant lines the change wrote."""

    session_id: str
    change_id: str
    kind: str
    item_count: int


class PaymentsObserver(Protocol):
    """The three moments a payments monitor cares about. Implementations run
    fire-and-forget: they must not raise; a host that needs retries or batching
    handles both inside the implementation. The default ``NullPaymentsObserver``
    discards every event and is safe to install everywhere."""

    async def on_cart_update(self, event: CartUpdateEvent) -> None: ...

    async def on_checkout_handoff(self, event: CheckoutHandoffEvent) -> None: ...

    async def on_merchant_change_applied(self, event: MerchantChangeAppliedEvent) -> None: ...


class NullPaymentsObserver:
    """The default. Every method returns without side effects. A deployment that does
    not run a payments monitor installs this (or nothing) and pays no cost."""

    async def on_cart_update(self, event: CartUpdateEvent) -> None:
        return None

    async def on_checkout_handoff(self, event: CheckoutHandoffEvent) -> None:
        return None

    async def on_merchant_change_applied(self, event: MerchantChangeAppliedEvent) -> None:
        return None
