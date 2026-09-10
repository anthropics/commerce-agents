# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from demo_common.merchant_fixtures import (
    filter_listings,
    is_browse,
    load_campaigns,
    load_issues,
    metric_window,
    named_ids,
    rebase_daily,
    snapshot_of,
    stage_campaign,
    staged_promotion_windows,
)
from merchant_agent import (
    AlertCounts,
    Campaign,
    CampaignDraft,
    ChangeLedger,
    ChangeNotApplicable,
    Listing,
    ListingFilters,
    MerchantAgentConfig,
)

ROWS = [
    {
        "date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
        "sales": 100.0 + i,
        "orders": 10,
        "traffic": 200,
    }
    for i in range(60)
]
NO_ALERTS = AlertCounts(low_stock=0, slow_movers=0, order_issues=0, pending_changes=0)


def listing(listing_id: str, **overrides) -> Listing:
    fields = {
        "listing_id": listing_id,
        "title": listing_id,
        "status": "active",
        "price": 10.0,
        "currency": "USD",
        "stock": 5,
        "category": "camping",
        "content_quality": "good",
    }
    return Listing(**(fields | overrides))


def test_rebase_lands_the_last_row_in_the_week_before_today_and_keeps_weekdays():
    rebased = rebase_daily(ROWS)
    last = date.fromisoformat(rebased[-1]["date"])
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    assert 0 <= (yesterday - last).days < 7
    assert last.weekday() == date.fromisoformat(ROWS[-1]["date"]).weekday()
    assert [row["sales"] for row in rebased] == [row["sales"] for row in ROWS]
    assert rebase_daily([]) == []


def test_anchored_campaigns_and_issues_shift_by_the_weeks_the_metrics_do(tmp_path):
    anchor = date.fromisoformat(ROWS[-1]["date"]) + timedelta(days=1)
    campaign = {
        "campaign_id": "C-1",
        "name": "Anchored",
        "status": "active",
        "budget": 10.0,
        "spend": 1.0,
        "revenue": 2.0,
        "starts": (anchor - timedelta(days=20)).isoformat(),
        "ends": (anchor + timedelta(days=10)).isoformat(),
    }
    draft = campaign | {"campaign_id": "C-2", "status": "draft", "starts": None, "ends": None}
    issue = {
        "issue_id": "I-1",
        "order_id": "O-1",
        "kind": "delayed",
        "summary": "Late",
        "opened_at": f"{(anchor - timedelta(days=2)).isoformat()}T15:10:00Z",
    }
    (tmp_path / "merchant_campaigns.json").write_text(
        json.dumps({"dates_anchored_to": anchor.isoformat(), "campaigns": [campaign, draft]})
    )
    (tmp_path / "merchant_messages.json").write_text(
        json.dumps({"dates_anchored_to": anchor.isoformat(), "issues": [issue]})
    )
    shift = date.fromisoformat(rebase_daily(ROWS)[-1]["date"]) - date.fromisoformat(
        ROWS[-1]["date"]
    )
    assert shift.days % 7 == 0

    anchored, unscheduled = load_campaigns(tmp_path).values()
    assert anchored.starts == (anchor - timedelta(days=20) + shift).isoformat()
    assert anchored.ends == (anchor + timedelta(days=10) + shift).isoformat()
    assert unscheduled.starts is None and unscheduled.ends is None
    (opened,) = load_issues(tmp_path)
    authored = datetime.combine(anchor - timedelta(days=2), datetime.min.time(), UTC)
    assert opened.opened_at == authored + timedelta(hours=15, minutes=10) + shift

    (tmp_path / "merchant_messages.json").write_text(json.dumps({"issues": [issue]}))
    assert load_issues(tmp_path)[0].opened_at.date() == anchor - timedelta(days=2)


def test_metric_window_sizes_and_labels_the_period_and_its_prior():
    current, prior, label = metric_window(ROWS, None)
    assert (len(current), len(prior), label) == (7, 7, "2026-02-23/2026-03-01")
    current, prior, _ = metric_window(ROWS, "last 30 days")
    assert (len(current), len(prior)) == (30, 30)
    current, prior, label = metric_window(ROWS, "2026-01-08/2026-01-14")
    assert (len(current), len(prior), label) == (7, 7, "2026-01-08/2026-01-14")
    assert prior[0]["date"] == "2026-01-01"
    assert len(metric_window(ROWS, "not a period")[0]) == 7


def test_snapshot_totals_and_changes_come_from_the_two_windows():
    snapshot = snapshot_of(ROWS, None, currency="USD", alerts=NO_ALERTS)
    assert snapshot.orders == 70 and snapshot.traffic == 1400 and snapshot.conversion_rate == 5.0
    assert snapshot.sales == round(sum(row["sales"] for row in ROWS[-7:]), 2)
    prior_sales = sum(row["sales"] for row in ROWS[-14:-7])
    assert snapshot.sales_change_pct == round((snapshot.sales - prior_sales) / prior_sales * 100, 1)
    assert snapshot.orders_change_pct == 0.0
    assert snapshot.conversion_change_pct == 0.0 and snapshot.compare_to == "2026-02-16/2026-02-22"
    renamed = [
        {"date": r["date"], "revenue": r["sales"], "bookings": r["orders"], "traffic": r["traffic"]}
        for r in ROWS
    ]
    other = snapshot_of(
        renamed, None, sales_key="revenue", orders_key="bookings", currency="USD", alerts=NO_ALERTS
    )
    assert other.sales == snapshot.sales and other.orders == snapshot.orders


def test_named_ids_and_browse_queries():
    ids = ["AR-1", "AR-2", "AR-3"]
    assert named_ids("ar-2, AR-1 ar-2", ids) == ["AR-2", "AR-1"]
    assert named_ids("camping tent", ids) == []
    assert [is_browse(q) for q in ("", " * ", "All", "tents")] == [True, True, True, False]


def test_filter_listings_narrows_sorts_and_pages():
    rows = [
        listing("A", stock=50, price=5.0),
        listing("B", stock=2, price=20.0, status="paused"),
        listing("C", stock=8, price=10.0, content_quality="needs_work"),
    ]
    sales = {"A": 1, "B": 30, "C": 12}
    ranked = filter_listings(
        list(rows), ListingFilters(sort="sales_desc"), 10, sales_of=sales.__getitem__
    )
    assert [x.listing_id for x in ranked] == ["B", "C", "A"]
    assert [
        x.listing_id
        for x in filter_listings(
            list(rows),
            ListingFilters(status="active", sort="price_desc"),
            1,
            sales_of=sales.__getitem__,
        )
    ] == ["C"]
    assert [
        x.listing_id
        for x in filter_listings(
            list(rows),
            ListingFilters(max_stock=8, sort="stock_asc"),
            10,
            sales_of=sales.__getitem__,
        )
    ] == ["B", "C"]
    assert [
        x.listing_id
        for x in filter_listings(
            list(rows), ListingFilters(content_quality="needs_work"), 10, sales_of=sales.__getitem__
        )
    ] == ["C"]
    assert [
        x.listing_id for x in filter_listings(list(rows), None, 2, sales_of=sales.__getitem__)
    ] == ["A", "B"]


def test_campaign_staging_and_promotion_windows():
    ledger = ChangeLedger(MerchantAgentConfig(brand_name="ACME"))
    campaigns = {
        "C-1": Campaign(
            campaign_id="C-1",
            name="Spring",
            status="active",
            channel="email",
            budget=100.0,
            spend=40.0,
            revenue=90.0,
        )
    }
    draft = CampaignDraft(campaign_id="C-1", name="Spring", budget=150.0, copy_text="Fresh copy")
    change = stage_campaign(ledger, campaigns, draft, actor="Avery", currency="USD")
    assert [(i.field, i.before, i.after) for i in change.items] == [
        ("budget", 100.0, 150.0),
        ("copy_text", None, "Fresh copy"),
    ]
    assert change.created_by == "Avery" and change.currency == "USD"
    copy_only = CampaignDraft(campaign_id="C-1", name="Spring", copy_text="Fresher copy")
    copy_change = stage_campaign(ledger, campaigns, copy_only, actor="Avery", currency="USD")
    assert [i.field for i in copy_change.items] == ["copy_text"]
    with pytest.raises(ChangeNotApplicable, match="changes no budget"):
        stage_campaign(
            ledger,
            campaigns,
            CampaignDraft(campaign_id="C-1", name="Spring"),
            actor="Avery",
            currency="USD",
        )

    windows = {
        change.change_id: {"name": "Spring", "starts": "2026-10-01", "ends": "2026-10-07"},
        "gone": {"name": "Old", "starts": "x", "ends": "y", "nights": ["fri"]},
    }
    assert staged_promotion_windows(ledger, windows) == [
        {
            "change_id": change.change_id,
            "name": "Spring",
            "starts": "2026-10-01",
            "ends": "2026-10-07",
            "listing_ids": ["C-1"],
        }
    ]


def test_stage_campaign_refuses_an_unknown_campaign_id():
    from demo_common.merchant_fixtures import stage_campaign
    from merchant_agent import CampaignDraft, MerchantAgentConfig
    from merchant_agent.changes import ChangeLedger, ChangeNotApplicable

    ledger = ChangeLedger(MerchantAgentConfig())
    with pytest.raises(ChangeNotApplicable):
        stage_campaign(
            ledger,
            {},
            CampaignDraft(campaign_id="camp-does-not-exist", name="Edit", budget=10),
            actor="op",
            currency="USD",
        )
    assert ledger.pending() == []
