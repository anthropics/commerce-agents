# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The retail example's ``MerchantBackend``: the merchant fixtures in ``data/`` overlaid
on the ``MockRetail`` the storefront serves, applied changes written back to that
catalog, and a read-only SQL view of the same state for the analysis delegate."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from demo_common.merchant_fixtures import (
    FAMILY_CONTENT_FIELDS,
    alert_counts,
    apply_campaign_item,
    change_pct,
    family_pricing_context,
    filter_listings,
    is_browse,
    load_campaigns,
    load_issues,
    margin_pct,
    metric_window,
    named_ids,
    promotion_targets,
    rebase_daily,
    refuse_outside_range,
    refuse_shared_content,
    share_content,
    snapshot_of,
    stage_campaign,
)
from demo_common.storefront_fixtures import load_json, refresh_family
from merchant_agent import (
    ActorKind,
    AlertCounts,
    AnalysisTable,
    BusinessSnapshot,
    Campaign,
    CampaignDraft,
    ChangeItem,
    ChangeKind,
    ChangeLedger,
    ChangeNotApplicable,
    DataLimitation,
    GuardrailViolation,
    InventoryActionItem,
    InventoryAlert,
    Listing,
    ListingDetails,
    ListingFilters,
    MerchantAgentConfig,
    MerchantBackend,
    MerchantSessionContext,
    MetricPoint,
    MetricSeries,
    OrderIssue,
    PriceUpdateItem,
    PricingContext,
    PromotionDraft,
    StagedChange,
    check_analysis_sql,
)
from shopping_agent import ProductDetails, SearchFilters, ShoppingSessionContext

from .mock_retail import DATA_DIR, DELIVERY_ATTRIBUTE, LOW_STOCK_ATTRIBUTE, MockRetail

KNOWN_METRICS = frozenset(
    {
        "sales",
        "revenue",
        "orders",
        "traffic",
        "conversion",
        "conversion_rate",
        "average_order_value",
        "aov",
    }
)
KNOWN_SEGMENTS = frozenset({"kids-room", "kids_room"})


class MockRetailMerchant(MerchantBackend):
    def __init__(
        self,
        storefront: MockRetail,
        config: MerchantAgentConfig | None = None,
        data_dir: Path = DATA_DIR,
        merchant_id: str = "acme-retail",
    ) -> None:
        self.storefront = storefront
        self.config = config or MerchantAgentConfig(brand_name=storefront.store_name)
        self.ledger = ChangeLedger(self.config)
        # Analysis queries are refused for sessions scoped to any other merchant.
        self.merchant_id = merchant_id
        metrics = load_json(data_dir, "merchant_metrics.json")
        self._daily = rebase_daily(metrics["daily"])
        self._currency: str = metrics.get("currency", "USD")
        inventory = load_json(data_dir, "merchant_inventory.json")
        self._default_stock: int = inventory.get("default_stock", 40)
        self._default_threshold: int = inventory.get("default_threshold", 8)
        self._inventory: dict[str, dict[str, Any]] = {
            row["product_id"]: dict(row) for row in inventory["inventory"]
        }
        self._assert_storefront_consistency()
        self._campaigns = load_campaigns(data_dir)
        self._issues = load_issues(data_dir)

    # ------------------------------------------------------------------
    # Listings
    # ------------------------------------------------------------------

    def _assert_storefront_consistency(self) -> None:
        """The catalog's in_stock flags and the inventory overlay describe one stock, so
        a disagreement between the fixtures fails at boot instead of in a demo."""
        records = {**self.storefront.products, **self.storefront.variants}
        for product_id, product in records.items():
            if product.has_options:
                continue  # a family's flag is derived from its variants
            row = self._inventory.get(product_id, {})
            stock = int(row.get("stock", self._default_stock))
            status = row.get("status") or ("out_of_stock" if stock == 0 else "active")
            sellable = status == "active" and stock > 0
            if product.in_stock != sellable:
                raise ValueError(
                    f"fixture mismatch on {product_id}: catalog in_stock="
                    f"{product.in_stock} but merchant overlay has status={status!r}, "
                    f"stock={stock}"
                )

    def _product(self, listing_id: str) -> ProductDetails | None:
        """The storefront record behind a listing id: a plain product, a family, or a variant."""
        return self.storefront.product(listing_id)

    def _state_row(self, product_id: str) -> dict[str, Any]:
        """The inventory row for a plain listing or a variant, defaulted on first touch. A
        family's row holds only its status and content flags: stock, sales, and cost
        live on its variants' rows."""
        product = self._product(product_id)
        key = product.product_id if product else product_id
        if key not in self._inventory:
            plain = product is not None and not product.has_options
            self._inventory[key] = {
                "product_id": key,
                "stock": self._default_stock if plain else 0,
                "threshold": self._default_threshold,
                "sales_last_30d": 8 if plain else None,
                "unit_cost": round(product.price * 0.55, 2) if plain else None,
            }
        return self._inventory[key]

    def _sales_30d(self, product: ProductDetails) -> int | None:
        """Units sold in 30 days; a family's is the sum over the variants that report it."""
        rows = [self._state_row(v.product_id) for v in product.variants] or [
            self._state_row(product.product_id)
        ]
        sales = [row["sales_last_30d"] for row in rows if row.get("sales_last_30d") is not None]
        return sum(sales) if sales else None

    def _listing(self, product_id: str) -> Listing | None:
        """One listing row. A family's price is its lowest variant's, its stock the sum of
        its variants' rows, and it is active while any variant is; a variant carries its
        choices and its family's id."""
        product = self._product(product_id)
        if product is None:
            return None
        row = self._state_row(product.product_id)
        if product.has_options:
            variants = [
                v for variant in product.variants if (v := self._listing(variant.product_id))
            ]
            stock = sum(v.stock for v in variants)
            active = any(v.status == "active" for v in variants)
            status = row.get("status") or ("active" if active else "out_of_stock")
            price = min((v.price for v in variants), default=product.price)
        else:
            stock = int(row.get("stock", self._default_stock))
            status = row.get("status") or ("out_of_stock" if stock == 0 else "active")
            price = product.price
        return Listing(
            listing_id=product.product_id,
            title=product.title,
            status=status,
            price=price,
            currency=product.currency,
            stock=stock,
            category=product.category,
            content_quality=row.get("content_quality", "good"),
            # The delivery promise is fulfillment metadata, not listing content.
            attributes={k: v for k, v in product.attributes.items() if k != DELIVERY_ATTRIBUTE},
            image_url=product.image_url,
            short_description=product.short_description,
            options=product.options,
            option_values=product.option_values,
            variant_of=product.variant_of,
        )

    def all_listings(self) -> list[Listing]:
        listings = [
            listing
            for product_id in self.storefront.products
            if (listing := self._listing(product_id)) is not None
        ]
        listings.sort(key=lambda listing: (listing.category or "", listing.listing_id))
        return listings

    # ------------------------------------------------------------------
    # Home-page reads for the portal
    # ------------------------------------------------------------------

    def kpi_trends(self, days: int = 7, periods_back: int = 0) -> dict[str, list[dict[str, Any]]]:
        """The daily points behind the portal home page's sparklines; ``periods_back=1``
        returns the preceding window of the same length."""
        end = max(0, len(self._daily) - days * periods_back)
        rows = self._daily[max(0, end - days) : end]
        return {
            "sales": [{"date": r["date"], "value": round(r["sales"], 2)} for r in rows],
            "orders": [{"date": r["date"], "value": float(r["orders"])} for r in rows],
            "conversion": [
                {
                    "date": r["date"],
                    "value": round(r["orders"] / r["traffic"] * 100, 2) if r["traffic"] else 0.0,
                }
                for r in rows
            ],
            "average_order_value": [
                {
                    "date": r["date"],
                    "value": round(r["sales"] / r["orders"], 2) if r["orders"] else 0.0,
                }
                for r in rows
            ],
        }

    def home_insights(self, limit: int = 3) -> list[dict[str, Any]]:
        """Observations for the portal home page, computed from the fixtures, each with a
        question the portal offers to prefill into the assistant."""
        insights: list[dict[str, Any]] = []

        # The highest return rate in the overlay.
        worst: tuple[Any, float, dict[str, Any]] | None = None
        for product_id, row in self._inventory.items():
            rate = row.get("return_rate_pct")
            product = self.storefront.products.get(product_id)
            if rate is None or product is None:
                continue
            if worst is None or rate > worst[1]:
                worst = (product, float(rate), row)
        if worst is not None:
            product, rate, row = worst
            sold = row.get("sales_last_30d")
            pace = f" on {sold} sales in 30 days" if sold else ""
            insights.append(
                {
                    "insight_id": f"return-rate-{product.product_id}",
                    "kind": "returns",
                    "headline": f"Returns on {product.title} are running at {rate:.0f}%",
                    "detail": f"{rate:.0f}% of units come back{pace}; "
                    "worth a look at the listing content before restocking.",
                    "prompt": f"Returns on {product.title} ({product.product_id}) are at "
                    f"{rate:.0f}%. What's the likely cause and what would you change?",
                }
            )

        # Segment sales week over week.
        rows = self._daily
        if len(rows) >= 14:
            current = sum(r.get("kids_room_sales", 0.0) for r in rows[-7:])
            prior = sum(r.get("kids_room_sales", 0.0) for r in rows[-14:-7])
            change = change_pct(current, prior)
            if change is not None and abs(change) >= 5:
                direction = "up" if change >= 0 else "down"
                insights.append(
                    {
                        "insight_id": "segment-trend-kids-room",
                        "kind": "trend",
                        "headline": f"Kids' room sales are {direction} "
                        f"{abs(change):.0f}% week-over-week",
                        "detail": f"${current:,.0f} this week vs ${prior:,.0f} last week "
                        "for the kids-room segment.",
                        "prompt": f"Kids' room sales are {direction} {abs(change):.0f}% "
                        "week-over-week. What's driving it, and is anything at risk of "
                        "stocking out?",
                    }
                )

        # Return per dollar across active campaigns that report revenue, when there are
        # two to compare; a campaign whose channel reports no revenue is left out.
        active = [
            c
            for c in self._campaigns.values()
            if c.status == "active" and c.spend and c.revenue is not None
        ]
        if len(active) >= 2:
            ranked = sorted(active, key=lambda c: c.revenue / c.spend, reverse=True)
            best, worst_campaign = ranked[0], ranked[-1]
            best_roas = best.revenue / best.spend
            worst_roas = worst_campaign.revenue / worst_campaign.spend
            if worst_roas > 0 and best_roas / worst_roas >= 1.5:
                insights.append(
                    {
                        "insight_id": f"campaign-spread-{best.campaign_id}",
                        "kind": "campaign",
                        "headline": "Your active campaigns are earning very different returns",
                        "detail": f"{best.name} returns ${best_roas:.2f} per $1 spent; "
                        f"{worst_campaign.name} returns ${worst_roas:.2f}.",
                        "prompt": f"{best.name} is returning ${best_roas:.2f} per $1 vs "
                        f"${worst_roas:.2f} for {worst_campaign.name}. Should budget "
                        "move between them?",
                    }
                )

        return insights[:limit]

    # ------------------------------------------------------------------
    # Performance
    # ------------------------------------------------------------------

    def _alert_counts(self) -> AlertCounts:
        return alert_counts(self._compute_alerts(), self._issues, self.ledger)

    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot:
        del session
        return snapshot_of(
            self._daily, period, currency=self._currency, alerts=self._alert_counts()
        )

    async def query_metrics(
        self,
        session: MerchantSessionContext,
        metric: str,
        period: str | None = None,
        granularity: str = "day",
        segment: str | None = None,
    ) -> MetricSeries:
        del session
        current, _, label = metric_window(self._daily, period or "last_30_days")
        cleaned = metric.strip().lower().replace(" ", "_")
        segment_cleaned = (segment or "").strip().lower().replace(" ", "-") or None
        # A metric or a segment the fixtures do not carry comes back empty with a note,
        # never as another series wearing the requested name.
        if cleaned not in KNOWN_METRICS:
            return MetricSeries(
                metric=cleaned,
                period=label,
                segment=segment_cleaned,
                note=f"no metric '{cleaned}'; available: {', '.join(sorted(KNOWN_METRICS))}",
            )
        if segment_cleaned and segment_cleaned not in KNOWN_SEGMENTS:
            return MetricSeries(
                metric=cleaned,
                period=label,
                segment=segment_cleaned,
                note=f"no segment '{segment_cleaned}'; only kids-room is broken out",
            )

        def value_for(rows: list[dict[str, Any]]) -> float:
            # Ratio metrics are recomputed from the bucket's totals.
            sales = sum(r["sales"] for r in rows)
            orders = sum(r["orders"] for r in rows)
            traffic = sum(r["traffic"] for r in rows)
            if segment_cleaned in {"kids-room", "kids_room"} and cleaned in {"sales", "revenue"}:
                return round(sum(r["kids_room_sales"] for r in rows), 2)
            if cleaned in {"sales", "revenue"}:
                return round(sales, 2)
            if cleaned == "orders":
                return float(orders)
            if cleaned == "traffic":
                return float(traffic)
            if cleaned in {"conversion", "conversion_rate"}:
                return round(orders / traffic * 100, 2) if traffic else 0.0
            if cleaned in {"average_order_value", "aov"}:
                return round(sales / orders, 2) if orders else 0.0
            return round(sales, 2)

        if granularity == "week":
            points = [
                MetricPoint(
                    date=current[start]["date"], value=value_for(current[start : start + 7])
                )
                for start in range(0, len(current), 7)
            ]
        else:
            points = [MetricPoint(date=row["date"], value=value_for([row])) for row in current]
        unit = (
            self._currency
            if cleaned in {"sales", "revenue", "average_order_value", "aov"}
            else None
        )
        return MetricSeries(
            metric=cleaned,
            unit=unit,
            granularity="week" if granularity == "week" else "day",
            period=label,
            segment=segment_cleaned,
            points=points,
        )

    async def get_campaign_performance(
        self, session: MerchantSessionContext, campaign_id: str | None = None
    ) -> list[Campaign]:
        del session
        campaigns = list(self._campaigns.values())
        if campaign_id:
            campaigns = [c for c in campaigns if c.campaign_id == campaign_id]
        return campaigns

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    async def search_listings(
        self,
        session: MerchantSessionContext,
        query: str,
        filters: ListingFilters | None = None,
        limit: int = 8,
    ) -> list[Listing]:
        if ids := named_ids(query, [*self.storefront.products, *self.storefront.variants]):
            listings = [listing for pid in ids if (listing := self._listing(pid))]
        elif is_browse(query):
            listings = [
                listing for pid in self.storefront.products if (listing := self._listing(pid))
            ]
        else:
            shopper = ShoppingSessionContext(
                session_id=session.session_id, user_id="merchant-portal"
            )
            products = await self.storefront.search_products(
                shopper, query, SearchFilters(), limit=max(limit, 8)
            )
            listings = [listing for p in products if (listing := self._listing(p.product_id))]
        return filter_listings(
            listings,
            filters,
            limit,
            sales_of=lambda listing_id: self._state_row(listing_id).get("sales_last_30d") or 0,
        )

    async def get_listing(
        self, session: MerchantSessionContext, listing_id: str
    ) -> ListingDetails | None:
        del session
        product = self._product(listing_id)
        listing = self._listing(listing_id) if product else None
        if product is None or listing is None:
            return None
        row = self._state_row(product.product_id)
        variants = [v for variant in product.variants if (v := self._listing(variant.product_id))]
        return ListingDetails(
            **listing.model_dump(),
            long_description=product.long_description,
            review_snippets=product.review_highlights,
            sales_last_30d=self._sales_30d(product),
            return_rate_pct=row.get("return_rate_pct"),
            missing_attributes=row.get("missing_attributes") or [],
            variants=variants,
        )

    # ------------------------------------------------------------------
    # Inventory and order health
    # ------------------------------------------------------------------

    def _compute_alerts(self) -> list[InventoryAlert]:
        alerts: list[InventoryAlert] = []
        for product_id, row in self._inventory.items():
            product = self._product(product_id)
            if product is None or product.has_options:
                continue  # a family's stock lives on its variants' rows
            stock = int(row.get("stock", self._default_stock))
            threshold = int(row.get("threshold", self._default_threshold))
            sales_30d = row.get("sales_last_30d")
            daily_pace = (sales_30d or 0) / 30
            # A paused listing still alerts but shows no chip to shoppers.
            listing = self._listing(product_id)
            visible = listing is not None and listing.status == "active" and stock > 0
            if stock <= threshold:
                alerts.append(
                    InventoryAlert(
                        listing_id=product_id,
                        title=product.title,
                        kind="low_stock",
                        option_values=product.option_values,
                        variant_of=product.variant_of,
                        stock=stock,
                        threshold=threshold,
                        days_of_cover=round(stock / daily_pace, 1) if daily_pace else None,
                        sales_last_30d=sales_30d,
                        storefront_visible=visible,
                    )
                )
            elif row.get("slow_mover"):
                alerts.append(
                    InventoryAlert(
                        listing_id=product_id,
                        title=product.title,
                        kind="slow_mover",
                        option_values=product.option_values,
                        variant_of=product.variant_of,
                        stock=stock,
                        threshold=threshold,
                        days_of_cover=round(stock / daily_pace, 1) if daily_pace else None,
                        sales_last_30d=sales_30d,
                        storefront_visible=visible,
                    )
                )
        alerts.sort(key=lambda alert: (alert.kind != "low_stock", -(alert.sales_last_30d or 0)))
        return alerts

    async def get_inventory_alerts(self, session: MerchantSessionContext) -> list[InventoryAlert]:
        del session
        return self._compute_alerts()

    async def get_order_issues(self, session: MerchantSessionContext) -> list[OrderIssue]:
        del session
        return list(self._issues)

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------

    async def get_pricing_context(
        self, session: MerchantSessionContext, listing_id: str
    ) -> PricingContext | None:
        del session
        product = self._product(listing_id)
        if product is None:
            return None
        if product.has_options:
            return family_pricing_context(
                product,
                [self._pricing_context(variant) for variant in product.variants],
                self.config,
            )
        return self._pricing_context(product)

    def _pricing_context(self, product: ProductDetails) -> PricingContext:
        row = self._state_row(product.product_id)
        unit_cost = row.get("unit_cost")
        sales_30d = row.get("sales_last_30d") or 0
        demand = "rising" if sales_30d >= 35 else "falling" if sales_30d <= 5 else "steady"
        margin = round((product.price - unit_cost) / product.price * 100, 1) if unit_cost else None
        return PricingContext(
            listing_id=product.product_id,
            current_price=product.price,
            currency=product.currency,
            unit_cost=unit_cost,
            margin_pct=margin,
            min_price=round(unit_cost * 1.15, 2) if unit_cost else None,
            min_price_basis="cost" if unit_cost else None,
            max_price=round(product.price * 1.35, 2),
            max_price_delta_pct=self.config.max_price_delta_pct,
            max_promotion_discount_pct=self.config.max_promotion_discount_pct,
            demand_signal=demand,
            last_changed=row.get("last_price_change"),
            option_values=product.option_values,
        )

    # ------------------------------------------------------------------
    # Staged writes
    # ------------------------------------------------------------------

    async def stage_listing_update(
        self,
        session: MerchantSessionContext,
        listing_id: str,
        fields: dict[str, Any],
        note: str | None = None,
    ) -> StagedChange:
        listing = await self.get_listing(session, listing_id)
        if listing is None:
            raise ValueError(f"no listing {listing_id}")
        refuse_shared_content(listing, fields)
        items = [
            ChangeItem(
                target=listing.listing_id,
                field=name,
                before=getattr(listing, name, listing.attributes.get(name)),
                after=value,
            )
            for name, value in fields.items()
        ]
        # Everything staged here is proposed by the assistant on the operator's behalf;
        # applying is the operator's own act.
        return self.ledger.stage(
            kind=ChangeKind.LISTING_UPDATE,
            summary=note or f"Update listing content on {listing.listing_id}",
            items=items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
        )

    async def stage_price_update(
        self,
        session: MerchantSessionContext,
        items: list[PriceUpdateItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items = []
        margin_impact = 0.0
        costed = True
        # The staged record is the only source of margin figures the agent quotes.
        margins: list[tuple[float, float]] = []
        margin_notes: list[str] = []
        currency: str | None = None
        for item in items:
            # An id that resolves to nothing is refused: staged, it would preview cleanly
            # and then apply as a silent no-op.
            product = self._product(item.listing_id)
            if product is None:
                raise ValueError(f"no listing {item.listing_id}")
            if product.has_options:
                # The executor holds this first; a price lives on the variants.
                raise ValueError(f"{product.product_id} is priced per variant")
            resolved = product.product_id
            refuse_outside_range(resolved, item.new_price, self._pricing_context(product))
            before = product.price
            if currency is None:
                currency = product.currency
            row = self._state_row(resolved)
            unit_cost = row.get("unit_cost")
            pace = (row.get("sales_last_30d") or 0) / 30
            if unit_cost is None:
                costed = False  # a margin figure is never computed from an assumed cost
            else:
                margin_impact += (item.new_price - before) * pace * 7
                margin_before = margin_pct(before, unit_cost)
                margin_after = margin_pct(item.new_price, unit_cost)
                margins.append((margin_before, margin_after))
                margin_notes.append(
                    f"{resolved} margin: {margin_before}% → {margin_after}% "
                    f"({margin_after - margin_before:+.1f} pts)"
                )
            change_items.append(
                ChangeItem(target=resolved, field="price", before=before, after=item.new_price)
            )
        return self.ledger.stage(
            kind=ChangeKind.PRICE_UPDATE,
            summary=note or f"Price update for {len(items)} listing(s)",
            items=change_items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
            currency=currency,
            margin_impact=round(margin_impact, 2) if costed else None,
            margin_before_pct=margins[0][0] if len(margins) == 1 else None,
            margin_after_pct=margins[0][1] if len(margins) == 1 else None,
            guardrail_notes=margin_notes if len(margins) > 1 else None,
        )

    async def stage_inventory_action(
        self,
        session: MerchantSessionContext,
        items: list[InventoryActionItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items = []
        for item in items:
            product = self._product(item.listing_id)
            if product is None:
                raise ValueError(f"no listing {item.listing_id}")
            resolved = product.product_id
            row = self._state_row(resolved)
            current: Any = int(row.get("stock", self._default_stock))
            if item.action == "restock":
                if product.has_options:
                    raise ValueError(f"{resolved} is restocked per variant")
                if not item.quantity:
                    raise ValueError(f"a restock of {resolved} needs a quantity above zero")
                after: Any = current + item.quantity
                field = "stock"
            else:
                after = "paused" if item.action == "pause" else "active"
                field = "status"
                listing = self._listing(resolved)
                current = listing.status if listing else None
            change_items.append(
                ChangeItem(target=resolved, field=field, before=current, after=after)
            )
        return self.ledger.stage(
            kind=ChangeKind.INVENTORY_ACTION,
            summary=note or f"Inventory action for {len(items)} listing(s)",
            items=change_items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
        )

    async def stage_promotion(
        self, session: MerchantSessionContext, promotion: PromotionDraft
    ) -> StagedChange:
        if promotion.ends < promotion.starts:  # ISO dates compare as text
            raise ValueError(
                f"the promotion ends ({promotion.ends}) before it starts ({promotion.starts})"
            )
        items = []
        margin_impact = 0.0
        margins: list[tuple[float, float]] = []
        margin_notes: list[str] = []
        currency: str | None = None
        requested = {
            requested_id: self._product(requested_id) for requested_id in promotion.listing_ids
        }
        if missing := [requested_id for requested_id, found in requested.items() if found is None]:
            raise ValueError(f"no listing {missing[0]}")
        # A promotion on a family is a promotion on each of its variants.
        targets = promotion_targets(requested.values())
        for product in targets:
            listing_id = product.product_id
            if currency is None:
                currency = product.currency
            row = self._state_row(listing_id)
            pace = (row.get("sales_last_30d") or 0) / 30
            discount_value = product.price * promotion.discount_pct / 100
            margin_impact -= discount_value * pace * 7
            promo_price = round(product.price * (1 - promotion.discount_pct / 100), 2)
            unit_cost = row.get("unit_cost") or 0.0
            # The pricing context states a floor (cost plus 15%); a promotion is a price
            # move like any other and may not take the listing under it. The discount cap
            # alone does not protect a listing whose margin is thinner than the cap.
            floor = round(unit_cost * 1.15, 2) if unit_cost else None
            if floor is not None and promo_price < floor:
                raise GuardrailViolation(
                    [
                        f"{listing_id} at {promo_price:.2f} would be under its floor of "
                        f"{floor:.2f}; the most it can take is "
                        f"{(1 - floor / product.price) * 100:.0f}%"
                    ]
                )
            if unit_cost and promo_price > 0:
                margin_before = margin_pct(product.price, unit_cost)
                margin_after = margin_pct(promo_price, unit_cost)
                margins.append((margin_before, margin_after))
                margin_notes.append(
                    f"{listing_id} margin: {margin_before}% → {margin_after}% "
                    f"({margin_after - margin_before:+.1f} pts) for the window"
                )
            items.append(
                ChangeItem(
                    target=listing_id,
                    field="promotion_price",
                    before=product.price,
                    after=promo_price,
                )
            )
        return self.ledger.stage(
            kind=ChangeKind.PROMOTION,
            summary=f"{promotion.name} ({promotion.discount_pct:.0f}% off, "
            f"{promotion.starts} to {promotion.ends})",
            items=items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
            currency=currency,
            margin_impact=round(margin_impact, 2),
            margin_before_pct=margins[0][0] if len(margins) == 1 else None,
            margin_after_pct=margins[0][1] if len(margins) == 1 else None,
            guardrail_notes=margin_notes if len(margins) > 1 else None,
        )

    async def stage_campaign(
        self, session: MerchantSessionContext, campaign: CampaignDraft
    ) -> StagedChange:
        return stage_campaign(
            self.ledger, self._campaigns, campaign, actor=session.operator, currency=self._currency
        )

    def _own(self, session: MerchantSessionContext) -> None:
        """This backend fronts one merchant; a session for another changes nothing. The
        host binds the merchant at session start, so this is defence in depth for a
        backend called directly."""
        if session.merchant_id != self.merchant_id:
            raise ChangeNotApplicable(
                f"session is for merchant {session.merchant_id}; this store is {self.merchant_id}"
            )

    async def get_pending_changes(self, session: MerchantSessionContext) -> list[StagedChange]:
        if session.merchant_id != self.merchant_id:
            return []
        return self.ledger.pending()

    async def apply_change(self, session: MerchantSessionContext, change_id: str) -> StagedChange:
        self._own(session)
        applied = self.ledger.apply(change_id, actor=session.operator)
        self._apply_to_live_state(applied)
        return applied

    async def discard_change(
        self,
        session: MerchantSessionContext,
        change_id: str,
        actor_kind: ActorKind = ActorKind.OPERATOR,
    ) -> StagedChange:
        self._own(session)
        return self.ledger.discard(change_id, actor=session.operator, actor_kind=actor_kind)

    def _apply_to_live_state(self, change: StagedChange) -> None:
        """Write an applied change through to the shared catalog. An applied promotion is
        recorded on the product as a ``promotion`` attribute the storefront and portal show;
        the standing price stays, since the discount applies at checkout in this store."""
        for item in change.items:
            product = self._product(item.target)
            if product is not None and change.kind is ChangeKind.PROMOTION:
                product.attributes["promotion"] = f"{change.summary}: {float(item.after):.2f}"
            if product is not None and change.kind in {
                ChangeKind.PRICE_UPDATE,
                ChangeKind.INVENTORY_ACTION,
                ChangeKind.LISTING_UPDATE,
            }:
                row = self._state_row(item.target)
                family = self._product(product.variant_of) if product.variant_of else None
                if change.kind is ChangeKind.PRICE_UPDATE:
                    product.price = float(item.after)
                    row["last_price_change"] = datetime.now(UTC).date().isoformat()
                elif change.kind is ChangeKind.INVENTORY_ACTION:
                    if item.field == "stock":
                        # Apply the staged delta rather than the staged total, so two
                        # restocks staged against the same starting stock both count.
                        row["stock"] = int(row.get("stock", self._default_stock)) + (
                            int(item.after) - int(item.before or 0)
                        )
                        # A restock never overrides an explicit pause.
                        product.in_stock = row["stock"] > 0 and row.get("status") != "paused"
                    elif item.field == "status":
                        row["status"] = "paused" if item.after == "paused" else "active"
                        # Pausing a family takes every variant off sale and reactivating it
                        # returns each variant to what its own stock says; a variant or a
                        # plain listing follows its own row.
                        for variant in product.variants or [product]:
                            variant_row = self._state_row(variant.product_id)
                            if product.has_options:
                                if item.after == "paused":
                                    variant_row["status"] = "paused"
                                else:
                                    variant_row.pop("status", None)
                            variant.in_stock = (
                                item.after != "paused" and int(variant_row.get("stock", 1)) > 0
                            )
                    # Re-stamp the storefront's scarcity chip from the moved number.
                    stock = int(row.get("stock", self._default_stock))
                    threshold = int(row.get("threshold", self._default_threshold))
                    if product.in_stock and not product.has_options and 0 < stock <= threshold:
                        product.attributes[LOW_STOCK_ATTRIBUTE] = str(stock)
                    else:
                        product.attributes.pop(LOW_STOCK_ATTRIBUTE, None)
                refresh_family(family or product)
                if change.kind is ChangeKind.LISTING_UPDATE:
                    if item.field in FAMILY_CONTENT_FIELDS:
                        share_content(product, item.field, item.after)
                    elif item.field == "content_quality":
                        row["content_quality"] = item.after
                    else:
                        product.attributes[item.field] = str(item.after)
                        row.setdefault("missing_attributes", [])
                        if item.field in row["missing_attributes"]:
                            row["missing_attributes"].remove(item.field)
                    if row.get("content_quality") == "needs_work" and item.field in {
                        "short_description",
                        "long_description",
                    }:
                        row["content_quality"] = "good"
            elif change.kind is ChangeKind.CAMPAIGN:
                apply_campaign_item(self._campaigns, item)

    # ------------------------------------------------------------------
    # Analysis queries
    # ------------------------------------------------------------------

    # What the analysis delegate is told it can query.
    ANALYSIS_SCHEMA_NOTES = (
        "daily_metrics(date, sales, orders, traffic, kids_room_sales) — one row per day, "
        "90 days ending yesterday. "
        "listings(listing_id, title, category, status, price, stock, content_quality, "
        "sales_last_30d, return_rate_pct, unit_cost). "
        "campaigns(campaign_id, name, status, channel, budget, spend, revenue, starts, ends). "
        "SQLite dialect; single SELECT statements only."
    )

    async def get_analysis_schema(self, session: MerchantSessionContext) -> str | None:
        if session.merchant_id != self.merchant_id:
            return None
        return self.ANALYSIS_SCHEMA_NOTES

    def _analysis_connection(self) -> sqlite3.Connection:
        """An in-memory database built from the current state, so queries see what the
        read tools see; an authorizer then allows reads only and a progress handler
        bounds runaway queries."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE daily_metrics "
            "(date TEXT, sales REAL, orders INTEGER, traffic INTEGER, kids_room_sales REAL)"
        )
        conn.executemany(
            "INSERT INTO daily_metrics VALUES (?, ?, ?, ?, ?)",
            [
                (r["date"], r["sales"], r["orders"], r["traffic"], r.get("kids_room_sales"))
                for r in self._daily
            ],
        )
        conn.execute(
            "CREATE TABLE listings (listing_id TEXT, title TEXT, category TEXT, status TEXT, "
            "price REAL, stock INTEGER, content_quality TEXT, sales_last_30d INTEGER, "
            "return_rate_pct REAL, unit_cost REAL)"
        )
        conn.executemany(
            "INSERT INTO listings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    listing.listing_id,
                    listing.title,
                    listing.category,
                    listing.status,
                    listing.price,
                    listing.stock,
                    listing.content_quality,
                    self._state_row(listing.listing_id).get("sales_last_30d"),
                    self._state_row(listing.listing_id).get("return_rate_pct"),
                    self._state_row(listing.listing_id).get("unit_cost"),
                )
                for listing in self.all_listings()
            ],
        )
        conn.execute(
            "CREATE TABLE campaigns (campaign_id TEXT, name TEXT, status TEXT, channel TEXT, "
            "budget REAL, spend REAL, revenue REAL, starts TEXT, ends TEXT)"
        )
        conn.executemany(
            "INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    c.campaign_id,
                    c.name,
                    c.status,
                    c.channel,
                    c.budget,
                    c.spend,
                    c.revenue,
                    c.starts,
                    c.ends,
                )
                for c in self._campaigns.values()
            ],
        )
        conn.commit()

        read_actions = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
        recursive = getattr(sqlite3, "SQLITE_RECURSIVE", None)
        if recursive is not None:
            read_actions.add(recursive)

        def read_only(action: int, *args: object) -> int:
            return sqlite3.SQLITE_OK if action in read_actions else sqlite3.SQLITE_DENY

        conn.set_authorizer(read_only)
        conn.set_progress_handler(lambda: 1, 5_000_000)
        return conn

    async def execute_analysis_query(
        self, session: MerchantSessionContext, sql: str
    ) -> AnalysisTable | None:
        if session.merchant_id != self.merchant_id:
            raise PermissionError("analysis queries are scoped to this store's own sessions")
        # Checked here as well as in the runtime, so the backend refuses on its own.
        if reason := check_analysis_sql(sql):
            raise ValueError(f"query refused: {reason}")

        def _run() -> tuple[list[str], list[Any]]:
            # In a thread, so the runtime's timeout can preempt a slow query.
            conn = self._analysis_connection()
            try:
                cursor = conn.execute(sql)
                columns = [description[0] for description in cursor.description or []]
                fetched = cursor.fetchmany(self.config.max_analysis_rows + 1)
                return columns, fetched
            finally:
                conn.close()

        try:
            columns, fetched = await asyncio.to_thread(_run)
        except sqlite3.Error as error:
            # The engine's message is what lets the delegate correct its query.
            raise ValueError(str(error)) from error
        truncated = len(fetched) > self.config.max_analysis_rows
        rows = [list(row) for row in fetched[: self.config.max_analysis_rows]]
        return AnalysisTable(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            note="row-capped" if truncated else None,
        )

    # ------------------------------------------------------------------
    # Merchant context
    # ------------------------------------------------------------------

    async def get_merchant_context(self, session: MerchantSessionContext) -> dict[str, Any] | None:
        counts = self._alert_counts()
        latest = self._daily[-1]["date"]
        week_start = (date.fromisoformat(latest) - timedelta(days=6)).isoformat()
        return {
            "store": self.storefront.store_name,
            "operator": session.operator,
            "current_period": f"{week_start}/{latest}",
            "catalog_size": len(self.storefront.products),
            # What this store's systems cannot supply, so the assistant says so instead
            # of reporting a zero.
            "limitations": [
                DataLimitation(
                    source="campaigns",
                    note="email sends report spend only; their attributed revenue is not available",
                ).model_dump(),
                DataLimitation(
                    source="orders", note="order and sales history covers the last 90 days"
                ).model_dump(),
            ],
            "alerts": {
                "low_stock": counts.low_stock,
                "slow_movers": counts.slow_movers,
                "order_issues": counts.order_issues,
                "pending_changes": counts.pending_changes,
            },
        }
