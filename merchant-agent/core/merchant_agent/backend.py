# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The MerchantBackend interface: the one integration surface an adopter implements,
mapping each method onto their analytics, catalog, inventory, order, pricing, and
campaign systems. Everything these methods return reaches the model as fenced data
(fencing.py). ``examples/retail/api/mock_merchant.py`` is a complete in-memory
implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .types import (
    ActorKind,
    AnalysisTable,
    BusinessSnapshot,
    Campaign,
    CampaignDraft,
    InventoryActionItem,
    InventoryAlert,
    Listing,
    ListingDetails,
    ListingFilters,
    MerchantSessionContext,
    MetricSeries,
    OrderIssue,
    PriceUpdateItem,
    PricingContext,
    PromotionDraft,
    StagedChange,
)


class MerchantBackend(ABC):
    """Reads are free to call; ``stage_*`` methods record a proposal without touching live
    state; only ``apply_change`` mutates anything: it performs the platform write, and
    only for a change that is currently staged. Every method calls the merchant's
    systems server-side with the credential the host holds for the session; the model sees
    results, never a token. A backend that fronts more than one merchant scopes every read and
    every change to ``session.merchant_id``: a session for another merchant lists nothing
    and its writes raise :class:`~merchant_agent.changes.ChangeNotApplicable`. The backend enforces the business rules (the guardrails in
    changes.py plus its own) and stamps the session's operator on every change. A method for a system
    the deployment does not have raises :class:`~merchant_agent.changes.ChangeNotApplicable`
    naming what is unmanaged, which the executor relays; any other exception is reported
    as a temporary failure.

    Listings with options (``Listing.options``). Search returns the family, never its
    variants. ``get_listing`` and ``get_pricing_context`` on the family return a row per
    variant, and on a variant's id return that variant. A price update or a restock names
    a variant and is refused for the family; the executor holds those before they reach
    the backend. A pause, a reactivation, or a promotion may name the family and then
    covers every variant, each counting toward ``max_items_per_change``. A content edit
    may name either, and a backend whose variants share the family's content fields
    refuses those fields on a variant.
    """

    # -- Performance -----------------------------------------------------------

    @abstractmethod
    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot:
        """Headline numbers for ``period``, defaulting to the current reporting period. A
        figure the store cannot supply is None with a ``note``; totals here and the series
        ``query_metrics`` returns for the same period should reconcile, since both are
        quoted."""

    @abstractmethod
    async def query_metrics(
        self,
        session: MerchantSessionContext,
        metric: str,
        period: str | None = None,
        granularity: str = "day",
        segment: str | None = None,
    ) -> MetricSeries:
        """One metric over time, optionally narrowed to a segment such as a category. A
        metric or a stretch of history the store cannot supply comes back with no points
        and a ``note`` saying why."""

    @abstractmethod
    async def get_campaign_performance(
        self, session: MerchantSessionContext, campaign_id: str | None = None
    ) -> list[Campaign]:
        """All campaigns, or the one named by ``campaign_id``. A channel that does not
        report spend or revenue leaves them None; campaigns this backend cannot see at
        all are a ``limitations`` entry in :meth:`get_merchant_context`."""

    # -- Catalog ----------------------------------------------------------------

    @abstractmethod
    async def search_listings(
        self,
        session: MerchantSessionContext,
        query: str,
        filters: ListingFilters | None = None,
        limit: int = 8,
    ) -> list[Listing]:
        """Search the store's listings by text and structured filters; a family is one
        result."""

    @abstractmethod
    async def get_listing(
        self, session: MerchantSessionContext, listing_id: str
    ) -> ListingDetails | None:
        """Full record for one listing, or None when the id is unknown."""

    # -- Inventory and order health ----------------------------------------------

    @abstractmethod
    async def get_inventory_alerts(self, session: MerchantSessionContext) -> list[InventoryAlert]:
        """Current low-stock and slow-mover alerts. A platform with no alert object
        derives them from stock and sales and returns the kinds it can compute; an empty
        list means nothing is flagged."""

    @abstractmethod
    async def get_order_issues(self, session: MerchantSessionContext) -> list[OrderIssue]:
        """Open order exceptions. A platform with no issue object derives them from its
        orders and returns only the kinds it can compute; an empty list means nothing is
        open."""

    # -- Pricing ------------------------------------------------------------------

    @abstractmethod
    async def get_pricing_context(
        self, session: MerchantSessionContext, listing_id: str
    ) -> PricingContext | None:
        """Pricing context for one listing or variant, or None when the id is unknown."""

    # -- Staged writes (propose → preview → approve → apply) ----------------------

    @abstractmethod
    async def stage_listing_update(
        self,
        session: MerchantSessionContext,
        listing_id: str,
        fields: dict[str, Any],
        note: str | None = None,
    ) -> StagedChange:
        """Stage content and attribute edits to one listing. A backend maps these fields
        onto its own and may refuse a combination it stores as one field (raise
        :class:`~merchant_agent.changes.ChangeNotApplicable` naming it)."""

    @abstractmethod
    async def stage_price_update(
        self,
        session: MerchantSessionContext,
        items: list[PriceUpdateItem],
        note: str | None = None,
    ) -> StagedChange:
        """Stage price changes for one or more listings or variants."""

    @abstractmethod
    async def stage_inventory_action(
        self,
        session: MerchantSessionContext,
        items: list[InventoryActionItem],
        note: str | None = None,
    ) -> StagedChange:
        """Stage restocks or availability changes for one or more listings."""

    @abstractmethod
    async def stage_promotion(
        self, session: MerchantSessionContext, promotion: PromotionDraft
    ) -> StagedChange:
        """Stage a promotion."""

    @abstractmethod
    async def stage_campaign(
        self, session: MerchantSessionContext, campaign: CampaignDraft
    ) -> StagedChange:
        """Stage a new campaign, or changes to the one named by ``campaign.campaign_id``."""

    @abstractmethod
    async def get_pending_changes(self, session: MerchantSessionContext) -> list[StagedChange]:
        """Changes staged but not yet applied or discarded."""

    @abstractmethod
    async def apply_change(self, session: MerchantSessionContext, change_id: str) -> StagedChange:
        """Apply an approved change by writing it to the live system this backend fronts,
        then mark it applied and stamp who applied it and when. This method is the platform
        write. A backend that only updates its ledger has changed nothing the store can see.
        Refuse a change that is not currently staged, and raise on a failed write so the
        change stays staged."""

    @abstractmethod
    async def discard_change(
        self,
        session: MerchantSessionContext,
        change_id: str,
        actor_kind: ActorKind = ActorKind.OPERATOR,
    ) -> StagedChange:
        """Discard a staged change. ``actor_kind`` records whether the operator or the
        assistant drove the discard; ``discarded_by`` names the operator either way."""

    # -- Analysis (optional read-only queries) ---------------------------------------

    async def execute_analysis_query(
        self, session: MerchantSessionContext, sql: str
    ) -> AnalysisTable | None:
        """Optional: run one read-only query for the analysis delegate and return a
        capped table. The default returns None, meaning SQL analysis is unsupported.

        The implementation owns the enforcement: a read-only replica or role, SELECT-only
        statements (``analysis.check_analysis_sql`` runs first, but the engine is the
        check that holds), row and byte caps, a timeout, and scoping every table to
        ``session.merchant_id``."""
        return None

    async def get_analysis_schema(self, session: MerchantSessionContext) -> str | None:
        """Optional: a short description of the queryable tables and columns, given to
        the analysis delegate as fenced reference data. None when SQL analysis is
        unsupported."""
        return None

    # -- Merchant context ----------------------------------------------------------

    async def get_merchant_context(self, session: MerchantSessionContext) -> dict[str, Any] | None:
        """Optional store context (profile, reporting period, alert counts) for the
        dynamic prompt block. It is sent on every request, so keep it small; the default
        returns None. A ``limitations`` key holds :class:`~merchant_agent.types.DataLimitation`
        entries for what the store's systems cannot supply to this deployment; the
        assistant states the one that bears on an answer instead of reporting a zero."""
        return None
