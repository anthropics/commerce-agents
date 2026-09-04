# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Merchant domain types shared by the orchestrator, the MerchantBackend interface, and
the examples: listings, metrics, analysis results, inventory and order health, pricing,
campaigns, staged changes, and session state. Adopters map their own systems onto these
models in their MerchantBackend implementation.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from commerce_common.types import ClockContext, remember

# ---------------------------------------------------------------------------
# Listings (the merchant's view of the catalog)
# ---------------------------------------------------------------------------


class Listing(BaseModel):
    """A catalog item as the operator manages it, in the same three shapes as the
    storefront's ``Product``: plain; a family carrying ``options``, whose ``price`` is its
    lowest variant's, in stock or not, and whose ``stock`` is the sum; a variant in its
    family's ``ListingDetails.variants`` with its own id, price, stock, and status, its
    ``option_values``, and the family's id in ``variant_of``. Price and stock are read and
    written per variant, so a variant's id goes wherever a ``listing_id`` goes; the write
    rules are on ``MerchantBackend`` and the mapping guide is ``docs/backends.md``."""

    listing_id: str
    title: str
    status: Literal["active", "paused", "draft", "out_of_stock"] = "active"
    price: float
    currency: str = "USD"
    stock: int = 0
    category: str | None = None
    content_quality: Literal["good", "needs_work", "poor"] | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    image_url: str | None = None
    short_description: str | None = None
    options: dict[str, list[str]] = Field(default_factory=dict)
    option_values: dict[str, str] = Field(default_factory=dict)
    variant_of: str | None = None

    @property
    def has_options(self) -> bool:
        """True for a family: price and stock writes name one of its variants."""
        return bool(self.options)


class ListingDetails(Listing):
    """Full listing record for editing and audits. ``review_snippets`` is buyer-authored
    text. ``variants`` is empty for a plain listing."""

    long_description: str | None = None
    review_snippets: list[str] = Field(default_factory=list)
    sales_last_30d: int | None = None
    return_rate_pct: float | None = None
    missing_attributes: list[str] = Field(default_factory=list)
    variants: list[Listing] = Field(default_factory=list)


class ListingFilters(BaseModel):
    status: Literal["active", "paused", "draft", "out_of_stock"] | None = None
    category: str | None = None
    max_stock: int | None = None
    content_quality: Literal["good", "needs_work", "poor"] | None = None
    sort: Literal["relevance", "sales_desc", "stock_asc", "price_desc", "price_asc"] = "relevance"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class AlertCounts(BaseModel):
    """``out_of_stock`` is the subset of ``low_stock`` that is at zero. It is here
    because it is the figure an operator acts on first, and a model that counts it
    itself from an alert list is quoting a number no tool returned. None, not zero,
    when the backend does not separate the two."""

    low_stock: int = 0
    slow_movers: int = 0
    order_issues: int = 0
    pending_changes: int = 0
    out_of_stock: int | None = None


class BusinessSnapshot(BaseModel):
    """Headline numbers for one reporting period, with comparison deltas and alert counts.
    A figure the store's systems cannot supply (for example traffic without an analytics
    scope) is None, never a stand-in zero, and ``note`` says why in a clause."""

    period: str
    compare_to: str | None = None
    sales: float
    orders: int
    traffic: int | None = None
    conversion_rate: float | None = None
    average_order_value: float | None = None
    sales_change_pct: float | None = None
    orders_change_pct: float | None = None
    traffic_change_pct: float | None = None
    conversion_change_pct: float | None = None
    currency: str = "USD"
    alerts: AlertCounts = Field(default_factory=AlertCounts)
    note: str | None = Field(default=None, max_length=140)


class MetricPoint(BaseModel):
    date: str
    value: float


class MetricSeries(BaseModel):
    """One metric over time, optionally for a single segment (a category, a listing).
    ``note`` says in a clause what limits the series (a capped history, a source the
    store cannot read); empty ``points`` with a note means the metric is unavailable."""

    metric: str
    unit: str | None = None
    granularity: Literal["day", "week", "month"] = "day"
    period: str | None = None
    segment: str | None = None
    points: list[MetricPoint] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=140)


# ---------------------------------------------------------------------------
# Analysis (the run_analysis delegate's data shapes)
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    """Clip ``text`` to ``limit`` characters, ending in an ellipsis when it was clipped."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


# The caps the analysis validators below enforce, matching the submit tool's input_schema
# in analysis.py. They are applied in validators rather than as Field(max_length=...)
# because pydantic checks field constraints before validators run and would reject the
# submission instead of clipping it.
_FIGURE_TEXT_CAPS = {"label": 80, "unit": 16, "note": 140}
_RESULT_TEXT_CAPS = {"question": 300, "headline": 200, "method_note": 300}
_RESULT_LIST_CAPS = {"findings": 8, "figures": 8, "derived_series": 4, "caveats": 4}
_RESULT_LINE_MAX_CHARS = 300
_RESULT_SERIES_MAX_POINTS = 40


class AnalysisFigure(BaseModel):
    """One computed number (a share, a delta, a correlation) rendered as a metric tile.
    ``value`` is numeric; ``unit`` and ``note`` carry the qualifiers."""

    label: str
    value: float
    unit: str | None = None
    change_pct: float | None = None
    note: str | None = None

    @field_validator("label", "unit", "note")
    @classmethod
    def _bounded_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        # Text is clipped (see AnalysisResult); a non-numeric ``value`` still fails.
        if value is None:
            return None
        return _truncate(value, _FIGURE_TEXT_CAPS[info.field_name])


class AnalysisResult(BaseModel):
    """What the analysis delegate submits; the metrics card renders its figures and
    series from this record.

    ``analysis_id`` is assigned server-side by ``MerchantSessionState.remember_analysis``;
    the delegate leaves it unset. The validators clip oversize text, lists, and series
    instead of rejecting them, because a rejected submission costs the delegate another
    full generation and none of the size caps marks a wrong answer."""

    analysis_id: str | None = None
    question: str
    headline: str
    findings: list[str] = Field(default_factory=list)
    figures: list[AnalysisFigure] = Field(default_factory=list)
    derived_series: list[MetricSeries] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    method_note: str | None = None

    @field_validator("findings", "figures", "derived_series", "caveats")
    @classmethod
    def _clipped_lists(cls, value: list[Any], info: ValidationInfo) -> list[Any]:
        # The delegate is told to lead with the load-bearing entries, so keep the head.
        return value[: _RESULT_LIST_CAPS[info.field_name]]

    @field_validator("question", "headline", "method_note")
    @classmethod
    def _bounded_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _truncate(value, _RESULT_TEXT_CAPS[info.field_name])

    @field_validator("findings", "caveats")
    @classmethod
    def _bounded_lines(cls, value: list[str]) -> list[str]:
        return [_truncate(line, _RESULT_LINE_MAX_CHARS) for line in value]

    @field_validator("derived_series")
    @classmethod
    def _downsampled_series(cls, value: list[MetricSeries]) -> list[MetricSeries]:
        # Keeps the first and last points and an even stride between them.
        for series in value:
            points = series.points
            if len(points) > _RESULT_SERIES_MAX_POINTS:
                stride = (len(points) - 1) / (_RESULT_SERIES_MAX_POINTS - 1)
                kept = [points[round(i * stride)] for i in range(_RESULT_SERIES_MAX_POINTS - 1)]
                kept.append(points[-1])
                series.points = kept
        return value


class AnalysisTable(BaseModel):
    """A row- and size-capped result from ``MerchantBackend.execute_analysis_query``.
    ``truncated`` is set whenever the backend or the runner dropped rows."""

    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    note: str | None = None


# ---------------------------------------------------------------------------
# Inventory and order health
# ---------------------------------------------------------------------------


class InventoryAlert(BaseModel):
    """An alert on one variant of a family carries that variant's id as ``listing_id``,
    its ``option_values``, and the family's id in ``variant_of``."""

    listing_id: str
    title: str
    kind: Literal["low_stock", "slow_mover"]
    option_values: dict[str, str] = Field(default_factory=dict)
    variant_of: str | None = None
    stock: int
    threshold: int | None = None
    days_of_cover: float | None = None
    sales_last_30d: int | None = None
    # False for paused and out-of-stock listings, None when the backend does not track
    # it. Portal copy that describes what the storefront shows gates on this field.
    storefront_visible: bool | None = None


class OrderIssue(BaseModel):
    """An order exception for the operator's attention. ``buyer_message_excerpt`` is
    customer-authored text."""

    issue_id: str
    order_id: str
    kind: Literal["delayed", "return_spike", "buyer_message", "damaged"]
    summary: str
    listing_id: str | None = None
    buyer_message_excerpt: str | None = None
    opened_at: datetime | None = None


# ---------------------------------------------------------------------------
# Pricing and campaigns
# ---------------------------------------------------------------------------


class PricingContext(BaseModel):
    """Server-computed context for judging a price move on one listing or one variant.
    ``max_price_delta_pct`` and ``max_promotion_discount_pct`` repeat the deployment's
    movement caps (MerchantAgentConfig) so the agent can state them before it stages a
    move. For a family listing, ``current_price`` is the lowest variant's and
    ``variants`` carries one context per variant."""

    listing_id: str
    # The listing's own title, so a price answer can name the item rather than its id.
    # None when the backend does not carry it on this record.
    title: str | None = None
    current_price: float
    currency: str = "USD"
    unit_cost: float | None = None
    margin_pct: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    max_price_delta_pct: float | None = None
    max_promotion_discount_pct: float | None = None
    # What ``min_price`` rests on: computed from the item's cost, or a store rule that
    # holds whatever the cost. None when the backend does not say.
    min_price_basis: Literal["cost", "policy"] | None = None
    demand_signal: Literal["rising", "steady", "falling"] | None = None
    last_changed: str | None = None
    option_values: dict[str, str] = Field(default_factory=dict)
    variants: list[PricingContext] = Field(default_factory=list)


class Campaign(BaseModel):
    """``spend`` and ``revenue`` are None when the channel does not report them; a zero
    means the channel reported zero."""

    campaign_id: str
    name: str
    status: Literal["draft", "active", "paused", "ended"]
    objective: str | None = None
    channel: str | None = None
    budget: float
    spend: float | None = None
    revenue: float | None = None
    currency: str = "USD"
    starts: str | None = None
    ends: str | None = None


class DataLimitation(BaseModel):
    """One thing the store's systems cannot supply to this deployment, for the
    ``limitations`` list in ``MerchantBackend.get_merchant_context``: an order history
    that goes back only so far, a traffic source the store's plan does not include,
    campaigns another tool created and this one cannot read."""

    source: str = Field(max_length=40)
    note: str = Field(max_length=140)


# ---------------------------------------------------------------------------
# Staged-change inputs (what the agent supplies to the stage_* tools)
# ---------------------------------------------------------------------------


class PriceUpdateItem(BaseModel):
    """``listing_id`` is a plain listing's id or a variant's; a family is repriced one
    variant per item."""

    listing_id: str
    new_price: float = Field(gt=0)


class InventoryActionItem(BaseModel):
    """A restock names a plain listing or a variant; a pause or reactivation may also
    name a family, which takes every variant off sale or back on."""

    listing_id: str
    action: Literal["restock", "pause", "activate"]
    quantity: int | None = Field(default=None, ge=0)


class PromotionDraft(BaseModel):
    """A date-bound price move. A positive ``discount_pct`` lowers the price and a
    negative one raises it for the window. ``nights`` limits the move to nights of the
    week; backends without a per-night dimension ignore it."""

    name: str = Field(max_length=80)
    listing_ids: list[str] = Field(min_length=1)
    discount_pct: float = Field(ge=-90, le=90)
    starts: str
    ends: str
    nights: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] | None = None


class CampaignDraft(BaseModel):
    """A campaign to create, or a budget/copy change to an existing one (``campaign_id``)."""

    campaign_id: str | None = None
    name: str = Field(max_length=80)
    objective: str | None = Field(default=None, max_length=200)
    audience: str | None = Field(default=None, max_length=300)
    budget: float | None = Field(default=None, ge=0)
    copy_text: str | None = Field(default=None, max_length=600)
    starts: str | None = None
    ends: str | None = None


# ---------------------------------------------------------------------------
# Staged changes (the propose → preview → approve → apply gate)
# ---------------------------------------------------------------------------


class ChangeKind(StrEnum):
    LISTING_UPDATE = "listing_update"
    PRICE_UPDATE = "price_update"
    INVENTORY_ACTION = "inventory_action"
    PROMOTION = "promotion"
    CAMPAIGN = "campaign"


class ChangeStatus(StrEnum):
    STAGED = "staged"
    APPLIED = "applied"
    DISCARDED = "discarded"


class ActorKind(StrEnum):
    """Who drove an action: the operator directly, or the assistant on the operator's
    behalf. The principal recorded next to it is the operator either way."""

    OPERATOR = "operator"
    AGENT = "agent"


class ChangeItem(BaseModel):
    """One field-level diff inside a staged change. ``target`` identifies the affected
    record (a listing id, a campaign id, a promotion name)."""

    target: str
    field: str
    before: Any = None
    after: Any = None


class StagedChange(BaseModel):
    """A proposed write awaiting approval of its ``change_id``. The actor stamps are
    the audit trail: ``created_by`` and ``discarded_by`` name the operator principal
    and the ``*_kind`` fields record whether the assistant acted on the operator's
    behalf; ``applied_by`` has no assistant variant because approval is always the
    operator's.

    The money fields are backend-computed. ``currency`` applies to every amount in
    ``items`` and the margin fields; ``margin_before_pct`` / ``margin_after_pct`` are set
    for single-listing price moves, and multi-item changes carry per-item margin lines
    in ``guardrail_notes`` instead. A margin figure is None when an item's cost is
    unknown; it is never computed from an assumed cost."""

    change_id: str
    kind: ChangeKind
    status: ChangeStatus = ChangeStatus.STAGED
    summary: str = Field(max_length=200)
    items: list[ChangeItem] = Field(default_factory=list)
    created_at: datetime
    created_by: str
    created_by_kind: ActorKind = ActorKind.OPERATOR
    applied_at: datetime | None = None
    applied_by: str | None = None
    discarded_at: datetime | None = None
    discarded_by: str | None = None
    discarded_by_kind: ActorKind | None = None
    guardrail_notes: list[str] = Field(default_factory=list)
    currency: str | None = None
    margin_impact: float | None = None
    margin_before_pct: float | None = None
    margin_after_pct: float | None = None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class MerchantSessionContext(ClockContext):
    """Per-request context supplied by the host. ``operator`` is stamped onto staged and
    applied changes, so the host derives it from its own authentication. A host that
    scopes an operator to certain stores or actions subclasses this context to carry
    that scope, and its backend enforces it on every method."""

    session_id: str
    merchant_id: str
    operator: str


class MerchantSessionState(BaseModel):
    """Per-session state owned by the host and updated by the orchestrator. The
    ``seen_*`` maps and ``latest_snapshot`` are the provenance record: staging accepts
    only listing ids that tools returned this session, apply and discard accept only
    change ids that tools returned, and presentation payloads are enriched from these
    records rather than from tool arguments.
    """

    seen_listings: dict[str, Listing] = Field(default_factory=dict)
    # Ids whose full record get_listing returned this session. stage_listing_update
    # requires this in addition to seen_listings, because a content edit is staged
    # against the full record and search rows carry only part of it.
    read_listings: set[str] = Field(default_factory=set)
    seen_changes: dict[str, StagedChange] = Field(default_factory=dict)
    latest_snapshot: BusinessSnapshot | None = None
    seen_series: dict[str, MetricSeries] = Field(default_factory=dict)
    seen_campaigns: dict[str, Campaign] = Field(default_factory=dict)
    # Keyed by the server-assigned analysis id (see remember_analysis).
    seen_analyses: dict[str, AnalysisResult] = Field(default_factory=dict)
    # Numbers the ids; a dropped record's id is not reused.
    analyses_run: int = 0
    # Change ids the host has recorded an approval for (a portal button, a CLI
    # confirmation). Consulted only when config.require_host_approval is on; apply_change
    # then refuses any id not in this set.
    approved_change_ids: set[str] = Field(default_factory=set)
    # Change ids whose discard the operator triggered on a host surface. The host adds
    # the id before routing the action through the executor, which stamps
    # ``discarded_by_kind`` as the operator. Written only by the host, so the model
    # cannot attribute its own discards to the operator.
    host_action_change_ids: set[str] = Field(default_factory=set)

    def remember_listings(self, listings: list[Listing]) -> None:
        for listing in listings:
            remember(self.seen_listings, listing.listing_id, listing)

    def remember_listing_record(self, listing: Listing) -> None:
        """Record a full get_listing read, which is what stage_listing_update requires.
        A family's variants (on a ``ListingDetails``) enter provenance with it, so
        price and stock writes can name them."""
        remember(self.seen_listings, listing.listing_id, listing)
        self.read_listings.add(listing.listing_id)
        if isinstance(listing, ListingDetails):
            self.remember_listings(listing.variants)

    def remember_change(self, change: StagedChange) -> None:
        remember(self.seen_changes, change.change_id, change)

    def remember_snapshot(self, snapshot: BusinessSnapshot) -> None:
        self.latest_snapshot = snapshot

    def remember_series(self, series: MetricSeries) -> None:
        key = f"{series.metric}:{series.segment}" if series.segment else series.metric
        remember(self.seen_series, key, series)

    def remember_campaigns(self, campaigns: list[Campaign]) -> None:
        for campaign in campaigns:
            remember(self.seen_campaigns, campaign.campaign_id, campaign)

    def remember_analysis(self, result: AnalysisResult) -> str:
        """Record a completed analysis and return the id assigned to it. Ids are assigned
        here rather than by the model, so an id the model invents has no record."""
        self.analyses_run += 1
        analysis_id = f"AN-{self.analyses_run}"
        result.analysis_id = analysis_id
        remember(self.seen_analyses, analysis_id, result)
        return analysis_id
