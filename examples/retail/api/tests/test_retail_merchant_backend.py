# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import pytest

from merchant_agent import (
    InventoryActionItem,
    ListingFilters,
    MerchantAgentConfig,
    PriceUpdateItem,
)
from retail.api.mock_merchant import MockRetailMerchant


async def test_snapshot_counts_the_fixture_alerts(merchant, operator_session):
    snapshot = await merchant.get_business_snapshot(operator_session)
    assert snapshot.alerts.low_stock >= 2
    assert snapshot.alerts.order_issues >= 3


async def test_query_metrics_supports_the_kids_room_segment(merchant, operator_session):
    overall = await merchant.query_metrics(operator_session, "sales", "last_7_days")
    kids = await merchant.query_metrics(
        operator_session, "sales", "last_7_days", segment="kids-room"
    )
    assert len(overall.points) == 7
    assert len(kids.points) == 7
    assert sum(p.value for p in kids.points) < sum(p.value for p in overall.points)
    assert kids.segment == "kids-room"


async def test_weekly_granularity_recomputes_ratio_metrics(merchant, operator_session):
    daily = await merchant.query_metrics(operator_session, "conversion_rate", "last_30_days")
    weekly = await merchant.query_metrics(
        operator_session, "conversion_rate", "last_30_days", granularity="week"
    )
    daily_avg = sum(p.value for p in daily.points) / len(daily.points)
    # Seven daily rates summed would be about seven times the daily average.
    assert all(p.value < daily_avg * 2 for p in weekly.points)
    weekly_aov = await merchant.query_metrics(
        operator_session, "average_order_value", "last_30_days", granularity="week"
    )
    assert all(20 < p.value < 100 for p in weekly_aov.points)


async def test_alerts_cover_the_demo_listings(merchant, operator_session):
    alerts = await merchant.get_inventory_alerts(operator_session)
    by_id = {alert.listing_id: alert for alert in alerts}
    assert by_id["AR-2102"].kind == "low_stock"
    assert by_id["AR-2106"].kind == "low_stock"
    assert by_id["AR-1207"].kind == "slow_mover"
    # 64 units on hand over 3 sold in the trailing 30 days.
    assert by_id["AR-1207"].days_of_cover == round(64 / (3 / 30), 1)


async def test_storefront_oos_products_are_not_active_in_the_portal(merchant, operator_session):
    # AR-1207 is paused with 64 units held; AR-1002 and AR-1508 are out of stock in the overlay too.
    paused = await merchant.get_listing(operator_session, "AR-1207")
    assert paused.status == "paused"
    assert paused.stock == 64
    for product_id in ("AR-1002", "AR-1508"):
        listing = await merchant.get_listing(operator_session, product_id)
        assert listing.status == "out_of_stock"
        assert listing.stock == 0


def test_boot_rejects_a_catalog_vs_overlay_stock_contradiction(backend):
    with pytest.raises(ValueError, match="AR-1207"):
        broken = MockRetailMerchant(backend, MerchantAgentConfig(brand_name="ACME"))
        broken._inventory["AR-1207"].pop("status")
        broken._assert_storefront_consistency()


async def test_listing_details_carry_quality_and_review_data(merchant, operator_session):
    listing = await merchant.get_listing(operator_session, "AR-2102")
    assert listing is not None
    assert listing.content_quality == "needs_work"
    assert listing.stock == 3
    assert listing.missing_attributes
    assert listing.sales_last_30d


async def test_order_issues_load_from_the_messages_fixture(merchant, operator_session):
    issues = await merchant.get_order_issues(operator_session)
    kinds = {issue.kind for issue in issues}
    assert "return_spike" in kinds
    assert any(issue.listing_id == "AR-1804" for issue in issues)


async def test_applied_restock_is_visible_to_the_storefront(merchant, backend, operator_session):
    before = (await merchant.get_listing(operator_session, "AR-2102")).stock
    change = await merchant.stage_inventory_action(
        operator_session, [InventoryActionItem(listing_id="AR-2102", action="restock", quantity=24)]
    )
    assert (await merchant.get_listing(operator_session, "AR-2102")).stock == before  # staged only

    await merchant.apply_change(operator_session, change.change_id)
    after = await merchant.get_listing(operator_session, "AR-2102")
    assert after.stock == before + 24
    assert backend.products["AR-2102"].in_stock is True
    assert (await merchant.get_business_snapshot(operator_session)).alerts.low_stock >= 1


async def test_applied_price_change_updates_the_shared_catalog(merchant, backend, operator_session):
    pricing = await merchant.get_pricing_context(operator_session, "AR-1207")
    new_price = round(pricing.current_price * 0.9, 2)
    change = await merchant.stage_price_update(
        operator_session, [PriceUpdateItem(listing_id="AR-1207", new_price=new_price)]
    )
    assert change.margin_impact is not None
    assert backend.products["AR-1207"].price == pricing.current_price

    await merchant.apply_change(operator_session, change.change_id)
    assert backend.products["AR-1207"].price == new_price


async def test_listing_update_apply_fixes_content_quality(merchant, backend, operator_session):
    change = await merchant.stage_listing_update(
        operator_session,
        "AR-2102",
        {"short_description": "36 peel-and-stick ocean decals that remove cleanly."},
        note="Refresh the decals listing copy",
    )
    await merchant.apply_change(operator_session, change.change_id)
    listing = await merchant.get_listing(operator_session, "AR-2102")
    assert listing.short_description.startswith("36 peel-and-stick")
    assert listing.content_quality == "good"
    assert backend.products["AR-2102"].short_description.startswith("36 peel-and-stick")


async def test_merchant_context_reports_alert_counts(merchant, operator_session):
    context = await merchant.get_merchant_context(operator_session)
    assert context["store"] == "ACME"
    assert context["alerts"]["low_stock"] >= 2


async def test_browse_filters_narrow_the_whole_catalog(merchant, operator_session):
    flagged = await merchant.search_listings(
        operator_session, "", ListingFilters(content_quality="needs_work"), limit=25
    )
    assert {listing.listing_id for listing in flagged} == {"AR-2102", "AR-1804"}
    low_stock = await merchant.search_listings(
        operator_session, "all", ListingFilters(max_stock=3), limit=25
    )
    assert any(listing.listing_id == "AR-2102" for listing in low_stock)


async def test_staged_price_cut_carries_fixture_true_margins(merchant, operator_session):
    # AR-2102 costs $10.50: a 56.2% margin at $24.00 and 53.9% at $22.80.
    change = await merchant.stage_price_update(
        operator_session, [PriceUpdateItem(listing_id="AR-2102", new_price=22.80)]
    )
    assert change.currency == "USD"
    assert change.margin_before_pct == 56.2
    assert change.margin_after_pct == 53.9


async def test_multi_item_price_update_carries_per_item_margin_notes(merchant, operator_session):
    change = await merchant.stage_price_update(
        operator_session,
        [
            PriceUpdateItem(listing_id="AR-2102", new_price=22.80),
            PriceUpdateItem(listing_id="AR-1207", new_price=149.00),
        ],
    )
    # Change-level margins are set for single-listing moves only; multi-item changes get a note per listing.
    assert change.margin_before_pct is None and change.margin_after_pct is None
    assert len([note for note in change.guardrail_notes if "margin" in note]) == 2


def test_kpi_trends_match_the_snapshot_window(merchant):
    """Seven daily points per KPI, ratios recomputed per day."""
    trends = merchant.kpi_trends()
    assert set(trends) == {"sales", "orders", "conversion", "average_order_value"}
    for points in trends.values():
        assert len(points) == 7
    day = trends["sales"][0]
    matching = next(p for p in trends["orders"] if p["date"] == day["date"])
    aov = next(p for p in trends["average_order_value"] if p["date"] == day["date"])
    assert aov["value"] == round(day["value"] / matching["value"], 2)


def test_home_insights_are_deterministic_and_fixture_grounded(merchant):
    first = merchant.home_insights()
    assert first == merchant.home_insights()
    assert 1 <= len(first) <= 3
    for insight in first:
        assert insight["headline"]
        assert insight["prompt"]
    # The fixtures give AR-1804 an 11% return rate and kids-room a week-over-week move.
    ids = {insight["insight_id"] for insight in first}
    assert "return-rate-AR-1804" in ids
    assert "segment-trend-kids-room" in ids


# -- listings with options ------------------------------------------------------------


async def test_a_family_listing_reads_as_from_price_summed_stock_and_variant_rows(
    merchant, operator_session
):
    [family] = await merchant.search_listings(operator_session, "hybrid mattress")
    assert family.listing_id == "AR-1902" and family.options == {
        "size": ["twin", "full", "queen", "king"]
    }
    details = await merchant.get_listing(operator_session, "AR-1902")
    by_id = {v.listing_id: v for v in details.variants}
    assert set(by_id) == {"AR-1902-TWIN", "AR-1902-FULL", "AR-1902-QUEEN", "AR-1902-KING"}
    assert family.price == min(v.price for v in details.variants if v.status == "active")
    assert family.stock == sum(v.stock for v in details.variants)
    assert by_id["AR-1902-FULL"].status == "out_of_stock" and by_id["AR-1902-FULL"].stock == 0
    assert by_id["AR-1902-KING"].option_values == {"size": "king"}
    assert all(v.variant_of == "AR-1902" for v in details.variants)
    variant = await merchant.get_listing(operator_session, "ar-1902-king")
    assert (
        variant.listing_id == "AR-1902-KING" and variant.price == 699.0 and variant.variants == []
    )


async def test_pricing_context_prices_a_family_per_variant(merchant, operator_session):
    family = await merchant.get_pricing_context(operator_session, "AR-1902")
    assert family.unit_cost is None and family.margin_pct is None  # a family has no cost
    assert family.current_price == 349.0
    assert {v.listing_id for v in family.variants} == {
        "AR-1902-TWIN",
        "AR-1902-FULL",
        "AR-1902-QUEEN",
        "AR-1902-KING",
    }
    king = next(v for v in family.variants if v.listing_id == "AR-1902-KING")
    assert king.current_price == 699.0 and king.unit_cost == 384.0 and king.margin_pct
    alone = await merchant.get_pricing_context(operator_session, "AR-1902-KING")
    assert alone.option_values == {"size": "king"} and alone.variants == []


async def test_price_and_restock_writes_name_a_variant_and_refresh_the_family(
    merchant, backend, operator_session
):
    with pytest.raises(ValueError, match="per variant"):
        await merchant.stage_price_update(
            operator_session, [PriceUpdateItem(listing_id="AR-1902", new_price=599)]
        )
    with pytest.raises(ValueError, match="per variant"):
        await merchant.stage_inventory_action(
            operator_session,
            [InventoryActionItem(listing_id="AR-1902", action="restock", quantity=5)],
        )

    change = await merchant.stage_price_update(
        operator_session, [PriceUpdateItem(listing_id="AR-1902-TWIN", new_price=329)]
    )
    assert [(i.target, i.before, i.after) for i in change.items] == [("AR-1902-TWIN", 349.0, 329.0)]
    assert change.margin_before_pct is not None  # a single variant carries its margins
    await merchant.apply_change(operator_session, change.change_id)
    assert backend.variants["AR-1902-TWIN"].price == 329.0
    # The family's own price follows its lowest in-stock variant.
    assert backend.products["AR-1902"].price == 329.0
    assert (await merchant.get_listing(operator_session, "AR-1902")).price == 329.0

    restock = await merchant.stage_inventory_action(
        operator_session,
        [InventoryActionItem(listing_id="AR-1902-FULL", action="restock", quantity=8)],
    )
    await merchant.apply_change(operator_session, restock.change_id)
    assert backend.variants["AR-1902-FULL"].in_stock is True
    full = await merchant.get_listing(operator_session, "AR-1902-FULL")
    assert full.stock == 8 and full.status == "active"


async def test_pausing_a_family_takes_every_variant_off_sale_and_back(
    merchant, backend, operator_session
):
    pause = await merchant.stage_inventory_action(
        operator_session, [InventoryActionItem(listing_id="AR-1606", action="pause")]
    )
    await merchant.apply_change(operator_session, pause.change_id)
    assert backend.products["AR-1606"].in_stock is False
    assert not any(v.in_stock for v in backend.products["AR-1606"].variants)
    assert (await merchant.get_listing(operator_session, "AR-1606")).status == "paused"

    resume = await merchant.stage_inventory_action(
        operator_session, [InventoryActionItem(listing_id="AR-1606", action="activate")]
    )
    await merchant.apply_change(operator_session, resume.change_id)
    assert backend.products["AR-1606"].in_stock is True
    # The variant with no stock stays off sale.
    assert backend.variants["AR-1606-KING-BLUSH"].in_stock is False


async def test_a_promotion_on_a_family_is_a_promotion_on_each_variant(merchant, operator_session):
    from merchant_agent import PromotionDraft

    change = await merchant.stage_promotion(
        operator_session,
        PromotionDraft(
            name="Sleep week",
            listing_ids=["AR-1902"],
            discount_pct=10,
            starts="2026-09-01",
            ends="2026-09-07",
        ),
    )
    assert [i.target for i in change.items] == [
        "AR-1902-TWIN",
        "AR-1902-FULL",
        "AR-1902-QUEEN",
        "AR-1902-KING",
    ]
    assert change.items[-1].after == round(699.0 * 0.9, 2)


async def test_variant_alerts_say_which_variant(merchant, operator_session):
    alerts = await merchant.get_inventory_alerts(operator_session)
    king = next(a for a in alerts if a.listing_id == "AR-1902-KING")
    assert king.kind == "low_stock" and king.option_values == {"size": "king"}
    assert not any(a.listing_id == "AR-1902" for a in alerts)  # the family itself never alerts


async def test_a_content_edit_on_a_variant_names_the_family_in_this_catalog(
    merchant, operator_session
):
    from merchant_agent import ChangeNotApplicable

    with pytest.raises(ChangeNotApplicable, match="AR-1902"):
        await merchant.stage_listing_update(
            operator_session, "AR-1902-KING", {"short_description": "Firmer feel."}
        )
    # An attribute the family does not own passes through to the variant.
    change = await merchant.stage_listing_update(operator_session, "AR-1902-KING", {"sku": "MAT-K"})
    assert [(i.target, i.field, i.after) for i in change.items] == [
        ("AR-1902-KING", "sku", "MAT-K")
    ]


async def test_the_store_says_what_it_cannot_supply(merchant, operator_session):
    context = await merchant.get_merchant_context(operator_session)
    assert {entry["source"] for entry in context["limitations"]} == {"campaigns", "orders"}
    campaigns = await merchant.get_campaign_performance(operator_session)
    email = next(c for c in campaigns if c.campaign_id == "C-203")
    # The email channel reports spend and no revenue: None, never a stand-in zero.
    assert email.spend == 400.0 and email.revenue is None


async def test_a_price_below_the_reported_floor_is_refused(merchant, operator_session):
    from merchant_agent import ChangeNotApplicable, PriceUpdateItem

    context = await merchant.get_pricing_context(operator_session, "AR-1902-KING")
    assert context.min_price is not None
    with pytest.raises(ChangeNotApplicable, match="below the floor"):
        await merchant.stage_price_update(
            operator_session,
            [PriceUpdateItem(listing_id="AR-1902-KING", new_price=context.min_price - 1)],
        )


async def test_unknown_metric_or_segment_has_no_points_and_a_note(merchant):
    from merchant_agent import MerchantSessionContext

    session = MerchantSessionContext(
        session_id="m-lab", merchant_id=merchant.merchant_id, operator="demo-operator"
    )
    """The contract: a metric or segment the store cannot supply comes back empty with a
    note, never another metric's series wearing the requested name."""
    unknown = await merchant.query_metrics(session, "zqxjv_metric")
    assert unknown.points == [] and unknown.note
    segment = await merchant.query_metrics(session, "sales", segment="garden-furniture")
    assert segment.points == [] and segment.note
    known = await merchant.query_metrics(session, "sales", segment="kids-room")
    assert known.points and not known.note


async def test_restock_needs_a_quantity(merchant):
    from merchant_agent import MerchantSessionContext

    session = MerchantSessionContext(
        session_id="m-lab", merchant_id=merchant.merchant_id, operator="demo-operator"
    )
    from merchant_agent import InventoryActionItem

    for quantity in (None, 0):
        with pytest.raises(ValueError):
            await merchant.stage_inventory_action(
                session,
                [InventoryActionItem(listing_id="AR-1001", action="restock", quantity=quantity)],
            )
    assert await merchant.get_pending_changes(session) == []


async def test_a_promotion_that_ends_before_it_starts_is_refused(merchant):
    from merchant_agent import MerchantSessionContext

    session = MerchantSessionContext(
        session_id="m-lab", merchant_id=merchant.merchant_id, operator="demo-operator"
    )
    from merchant_agent import PromotionDraft

    backwards = PromotionDraft(
        name="Backwards",
        listing_ids=["AR-1001"],
        discount_pct=10,
        starts="2026-09-10",
        ends="2026-09-07",
    )
    with pytest.raises(ValueError):
        await merchant.stage_promotion(session, backwards)
    assert await merchant.get_pending_changes(session) == []


async def test_another_merchants_session_sees_and_changes_nothing(merchant):
    from merchant_agent import MerchantSessionContext

    session = MerchantSessionContext(
        session_id="m-lab", merchant_id=merchant.merchant_id, operator="demo-operator"
    )
    from merchant_agent import InventoryActionItem, MerchantSessionContext
    from merchant_agent.changes import ChangeNotApplicable

    change = await merchant.stage_inventory_action(
        session, [InventoryActionItem(listing_id="AR-1001", action="restock", quantity=1)]
    )
    other = MerchantSessionContext(session_id="x", merchant_id="someone-else", operator="eve")
    assert await merchant.get_pending_changes(other) == []
    with pytest.raises(ChangeNotApplicable):
        await merchant.apply_change(other, change.change_id)
    with pytest.raises(ChangeNotApplicable):
        await merchant.discard_change(other, change.change_id)
    assert [c.change_id for c in await merchant.get_pending_changes(session)] == [change.change_id]


async def test_a_promotion_may_not_take_a_listing_under_its_floor(merchant):
    from merchant_agent import MerchantSessionContext, PromotionDraft
    from merchant_agent.changes import GuardrailViolation

    session = MerchantSessionContext(
        session_id="m-floor", merchant_id=merchant.merchant_id, operator="demo-operator"
    )
    context = await merchant.get_pricing_context(session, "AR-1001")
    assert context is not None and context.min_price is not None
    # A discount inside the cap that still lands under the floor.
    under = (1 - context.min_price / context.current_price) * 100 + 5
    assert under <= merchant.config.max_promotion_discount_pct
    with pytest.raises(GuardrailViolation):
        await merchant.stage_promotion(
            session,
            PromotionDraft(
                name="Too deep for the margin",
                listing_ids=["AR-1001"],
                discount_pct=round(under, 1),
                starts="2026-09-10",
                ends="2026-09-12",
            ),
        )
    assert await merchant.get_pending_changes(session) == []
