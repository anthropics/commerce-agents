# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Routes, gates, and merchant writes every vertical honors; each test_contract.py star-imports it."""

import asyncio
import inspect
import re
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from commerce_common.memory import InMemoryMemoryStore
from commerce_common.types import MemoryFact
from demo_common import (
    SESSION_HEADER,
    MemorySeeder,
    SessionStore,
    build_merchant_router,
    build_storefront_host,
)
from demo_common.storefront_fixtures import load_catalog, load_json
from demo_common.tests.fixtures import showcase_products, start_operator, start_shopper
from merchant_agent import (
    ActorKind,
    CampaignDraft,
    ChangeNotApplicable,
    ChangeStatus,
    InventoryActionItem,
    MerchantSessionState,
    PriceUpdateItem,
    PromotionDraft,
)
from merchant_agent_runtime import MerchantAgent
from shopping_agent import SearchFilters, ShoppingSessionContext, ShoppingSessionState

IDENTITY_FIELDS = {"user_id", "session_id", "merchant_id", "operator"}
STOREFRONT_PUBLIC = {
    "/api/session",
    "/api/products",
    "/api/products/{product_id:path}",
    "/api/health",
}
MERCHANT_PUBLIC = {"/api/merchant/session", "/api/merchant/health"}
LOOPBACK = ("127.0.0.1", 12345)


# -- fixtures ------------------------------------------------------------------------


@pytest.fixture
def seeded_shopper(client):
    """Returns shopper headers over a freshly reseeded memory; reseeds again afterwards."""
    client.post("/api/reset", json={"clear_memory": True}, headers=start_shopper(client))
    yield start_shopper(client)
    client.post("/api/reset", json={"clear_memory": True}, headers=start_shopper(client))


@pytest.fixture
def portal(make_storefront, make_merchant_router):
    """Returns (client, store, merchant_id) for a portal whose store holds two facts."""
    store = InMemoryMemoryStore()
    app = FastAPI()
    app.include_router(make_merchant_router(make_storefront(), store), prefix="/api/merchant")
    client = TestClient(app, base_url="http://localhost", client=LOOPBACK)
    merchant_id = client.post("/api/merchant/session").json()["merchant_id"]
    asyncio.run(
        store.upsert_facts(
            merchant_id,
            [
                MemoryFact(
                    key="margin_floor",
                    value="keeps every listing above a 30% margin",
                    category="constraint",
                ),
                MemoryFact(key="digest_style", value="wants the morning digest kept to five lines"),
            ],
        )
    )
    return client, store, merchant_id


def _routes(app: FastAPI) -> list[Any]:
    """Every ``/api/`` path operation with its effective path, methods, and dependant.

    FastAPI 0.137 stopped copying an included router's operations into ``app.routes``;
    ``iter_route_contexts`` walks the nested shape, and older releases keep the flat one.
    """
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:  # FastAPI < 0.137
        routes: Iterable[Any] = app.routes
    else:
        routes = iter_route_contexts(app.routes)
    return [
        r
        for r in routes
        if isinstance(getattr(r, "original_route", r), APIRoute) and r.path.startswith("/api/")
    ]


def _dependency_of(route: Any):
    """The route's session dependency: the last one declared (a route-level hook such as
    the ticketing example's before_turn precedes it), or None on a public route."""
    calls = [dep.call for dep in route.dependant.dependencies]
    return calls[-1] if calls else None


def _resolver(current_session: Any):
    """The callable behind a role's ``CurrentSession`` annotation."""
    return current_session.__metadata__[0].dependency


def _declared_fields(route: Any) -> set[str]:
    dependant = route.dependant
    declared = {param.name for param in (*dependant.query_params, *dependant.path_params)}
    for body in dependant.body_params:
        annotation = body.field_info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            declared |= set(annotation.model_fields)
        else:
            declared.add(body.name)
    return declared


# -- identity ------------------------------------------------------------------------


def test_no_route_reads_identity_from_the_request(main):
    for route in _routes(main.app):
        if not route.path.endswith("/session"):
            assert not (_declared_fields(route) & IDENTITY_FIELDS), route.path


def test_public_route_sets_are_closed_and_each_role_has_its_own_token_store(
    main, extra_public_routes
):
    routes = _routes(main.app)
    storefront = [r for r in routes if not r.path.startswith("/api/merchant")]
    portal = [r for r in routes if r.path.startswith("/api/merchant")]
    assert {r.path for r in storefront if _dependency_of(r) is None} == (
        STOREFRONT_PUBLIC | extra_public_routes
    )
    assert {r.path for r in portal if _dependency_of(r) is None} == MERCHANT_PUBLIC
    resolver = _resolver(main.host.CurrentSession)
    assert all(_dependency_of(r) is resolver for r in storefront if _dependency_of(r))
    portal_dependencies = {_dependency_of(r) for r in portal if _dependency_of(r)}
    assert len(portal_dependencies) == 1
    assert resolver not in portal_dependencies


def test_every_scoped_route_refuses_a_missing_or_unknown_token(main, client):
    made_up = {SESSION_HEADER: "not-a-token"}
    for route in _routes(main.app):
        if _dependency_of(route) is None:
            continue
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in route.methods:
            assert client.request(method, path, json={}).status_code == 401, (method, path)
            assert client.request(method, path, json={}, headers=made_up).status_code == 401


def test_storefront_session_binds_the_profile_and_reset_reissues_it(main, client):
    started = client.post("/api/session", json={"user_id": "demo-user-2"}).json()
    assert started["user_id"] == "demo-user-2" and started["name"]
    assert main.host.sessions.require(started["session_id"]).user_id == "demo-user-2"
    assert client.post("/api/session", json={}).json()["user_id"] == "demo-user"
    assert (
        client.post("/api/session").json()["user_id"] == "demo-user"
    )  # the storefront sends no body
    assert client.post("/api/session", json={"user_id": ""}).status_code == 422

    mine, theirs = start_shopper(client), start_shopper(client, "demo-user-2")
    assert client.post("/api/reset", json={}, headers=theirs).status_code == 200
    fresh = client.post("/api/reset", json={}, headers=mine).json()["session_id"]
    assert client.get("/api/cart", headers=mine).status_code == 401
    assert main.host.sessions.require(fresh).user_id == "demo-user"


def test_merchant_session_binds_the_server_held_identity(portal):
    client, _, merchant_id = portal
    started = client.post("/api/merchant/session", json={"merchant_id": "someone-else"}).json()
    assert started["merchant_id"] == merchant_id
    headers = {SESSION_HEADER: started["session_id"]}
    assert client.get("/api/merchant/overview", headers=headers).status_code == 200
    fresh = client.post("/api/merchant/reset", json={}, headers=headers).json()["session_id"]
    assert client.get("/api/merchant/overview", headers=headers).status_code == 401
    assert client.get("/api/merchant/overview", headers={SESSION_HEADER: fresh}).status_code == 200


# -- host hardening --------------------------------------------------------------------


def test_non_loopback_host_names_are_rejected_before_any_route(main):
    for host, status in (
        ("http://localhost", 200),
        ("http://127.0.0.1", 200),
        ("http://evil.example.com", 400),
        ("http://testserver", 400),
        ("http://localhost.evil.example", 400),
    ):
        assert TestClient(main.app, base_url=host).get("/api/health").status_code == status, host


def test_json_body_without_a_content_type_is_not_parsed(client):
    # A cross-site "simple request" cannot set a JSON Content-Type.
    shopper = start_shopper(client)
    body = b'{"key": "x", "value": "y"}'
    assert client.request("PATCH", "/api/memory", content=body).status_code == 401
    assert client.request("PATCH", "/api/memory", content=body, headers=shopper).status_code == 422
    text = shopper | {"Content-Type": "text/plain"}
    assert client.request("PATCH", "/api/memory", content=body, headers=text).status_code == 422


def test_health_names_the_store_and_the_model(main, client, portal):
    storefront = client.get("/api/health").json()
    assert storefront["store"] == main.backend.store_name
    assert storefront["products"] == len(main.backend.products)
    assert storefront["model"] == main.agent.config.model
    portal_health = portal[0].get("/api/merchant/health").json()
    assert portal_health["store"] == main.backend.store_name and portal_health["role"] == "merchant"


# -- memory --------------------------------------------------------------------------


def _facts(main, user_id: str = "demo-user") -> dict:
    return {fact.key: fact for fact in asyncio.run(main.host.memory_store.get_facts(user_id))}


def test_memory_routes_carry_the_key_in_the_body(main):
    routes = [r for r in _routes(main.app) if r.path.endswith("/memory")]
    assert {(m, r.path) for r in routes for m in r.methods} == {
        ("GET", "/api/memory"),
        ("PATCH", "/api/memory"),
        ("DELETE", "/api/memory"),
        ("GET", "/api/merchant/memory"),
        ("DELETE", "/api/merchant/memory"),
    }
    assert not any(r.dependant.path_params or r.dependant.query_params for r in routes)


def test_seed_file_matches_what_a_fresh_shopper_sees(main, client, seeded_shopper):
    seed = load_json(main.DATA_DIR, "memory-seed.json")
    listed = client.get("/api/memory", headers=seeded_shopper).json()["facts"]
    assert {fact["key"] for fact in listed} == {fact["key"] for fact in seed["demo-user"]}
    assert all(fact["updated_at"] for fact in listed)
    assert (
        client.get("/api/memory", headers=start_shopper(client, "demo-user-2")).json()["facts"]
        == []
    )


def test_delete_and_edit_touch_exactly_one_fact_of_the_callers_own(main, client, seeded_shopper):
    key, *rest = sorted(_facts(main))
    stranger = start_shopper(client, "demo-user-2")
    assert (
        client.request("DELETE", "/api/memory", json={"key": key}, headers=stranger).status_code
        == 404
    )
    assert (
        client.patch("/api/memory", json={"key": key, "value": "x"}, headers=stranger).status_code
        == 404
    )

    edited = client.patch(
        "/api/memory", json={"key": key, "value": "corrected"}, headers=seeded_shopper
    )
    assert edited.status_code == 200
    assert _facts(main)[key].value == "corrected"
    rejected = client.patch(
        "/api/memory",
        json={"key": key, "value": "card on file 4000 1234 5678 9010"},
        headers=seeded_shopper,
    )
    assert rejected.status_code == 400 and "4000" not in rejected.text
    assert _facts(main)[key].value == "corrected"

    deleted = client.request("DELETE", "/api/memory", json={"key": key}, headers=seeded_shopper)
    assert deleted.json() == {"ok": True, "deleted": key}
    assert set(_facts(main)) == set(rest)
    assert (
        client.request(
            "DELETE", "/api/memory", json={"key": key}, headers=seeded_shopper
        ).status_code
        == 404
    )


def test_purge_deletes_everything_and_clear_memory_reseeds(main, client, seeded_shopper):
    assert (
        client.post("/api/reset", json={"purge_memory": True}, headers=seeded_shopper).status_code
        == 200
    )
    assert _facts(main) == {}
    assert asyncio.run(main.host.memory_store.purge_generation("demo-user")) >= 1
    client.post("/api/reset", json={"clear_memory": True}, headers=start_shopper(client))
    assert _facts(main)


def test_merchant_memory_lists_deletes_one_fact_and_purges(portal):
    client, store, merchant_id = portal
    headers = start_operator(client)
    listed = client.get("/api/merchant/memory", headers=headers).json()["facts"]
    assert {fact["key"] for fact in listed} == {"margin_floor", "digest_style"}
    body = {"key": "margin_floor"}
    deleted = client.request("DELETE", "/api/merchant/memory", json=body, headers=headers)
    assert deleted.json() == {"ok": True, "deleted": "margin_floor"}
    assert (
        client.request("DELETE", "/api/merchant/memory", json=body, headers=headers).status_code
        == 404
    )

    assert client.post("/api/merchant/reset", json={}, headers=headers).status_code == 200
    assert (
        len(client.get("/api/merchant/memory", headers=start_operator(client)).json()["facts"]) == 1
    )
    headers = start_operator(client)
    assert (
        client.post("/api/merchant/reset", json={"purge_memory": True}, headers=headers).status_code
        == 200
    )
    assert client.get("/api/merchant/memory", headers=start_operator(client)).json()["facts"] == []
    assert asyncio.run(store.purge_generation(merchant_id)) == 1


# -- portal routes -------------------------------------------------------------------


def test_listings_total_counts_the_universe_and_survives_paging(portal):
    client, _, _ = portal
    headers = start_operator(client)
    everything = client.get("/api/merchant/listings", headers=headers).json()
    assert everything["total"] == len(everything["listings"]) > 1
    page = client.get("/api/merchant/listings", params={"limit": 1}, headers=headers).json()
    assert page["total"] == everything["total"] and len(page["listings"]) == 1
    listing_id = everything["listings"][0]["listing_id"]
    detail = client.get(f"/api/merchant/listings/{listing_id}", headers=headers).json()
    assert detail["listing"]["listing_id"] == listing_id
    assert client.get("/api/merchant/listings/nope", headers=headers).status_code == 404


def test_ids_containing_slashes_reach_the_id_routes(client, main):
    """A platform's global id ("gid://shop/Product/7") reaches the handler, which answers
    404 for an unknown id; the id routes take ``:path`` parameters so it does not miss."""
    headers = start_operator(client)
    weird = "gid://acme/Listing/7"
    listing = client.get(f"/api/merchant/listings/{weird}", headers=headers)
    assert listing.status_code == 404 and listing.json()["detail"] == "Listing not found"
    product = client.get(f"/api/products/{weird}")
    assert product.status_code == 404 and product.json()["detail"] == "Product not found"
    applied = client.post(f"/api/merchant/changes/{weird}/apply", headers=headers)
    assert applied.status_code == 200 and weird in applied.json()["reason"]


def test_overview_and_alerts_carry_the_portal_keys(portal):
    client, _, _ = portal
    headers = start_operator(client)
    overview = client.get("/api/merchant/overview", headers=headers).json()
    assert {"snapshot", "needs_attention", "recent_orders", "recent_changes"} <= set(overview)
    assert set(overview["needs_attention"]) == {"inventory", "order_issues", "pending_changes"}
    assert set(client.get("/api/merchant/alerts", headers=headers).json()) == {
        "inventory",
        "order_issues",
    }


def test_preview_card_buttons_report_a_hold_apart_from_an_apply(
    backend, merchant, merchant_identity, operator_session
):
    """A held call answers ok=false with the reason; only an applied change queues an app event."""
    agent = MerchantAgent(
        backend=merchant, config=merchant.config, memory_store=InMemoryMemoryStore()
    )
    app = FastAPI()
    router = build_merchant_router(
        storefront=backend,
        backend=merchant,
        agent=agent,
        identity=merchant_identity,
        example_dir="contract",
    )
    app.include_router(router, prefix="/api/merchant")
    client = TestClient(app, base_url="http://localhost", client=LOOPBACK)
    headers = start_operator(client)
    apply_route = next(
        r for r in _routes(app) if r.path.endswith("/changes/{change_id:path}/apply")
    )
    sessions = inspect.getclosurevars(_dependency_of(apply_route)).nonlocals["store"]

    def stored():
        return sessions.require(headers[SESSION_HEADER])

    held = client.post("/api/merchant/changes/chg-none/apply", headers=headers)
    assert held.status_code == 200
    body = held.json()
    assert body["ok"] is False and body["change"] is None
    assert "chg-none was not staged or listed in this session" in body["reason"]
    dismissed = client.post("/api/merchant/changes/chg-none/discard", headers=headers).json()
    assert dismissed["ok"] is False and "nothing to discard" in dismissed["reason"]
    record = stored()
    assert record.pending_app_events == []
    assert not record.state.approved_change_ids and not record.state.host_action_change_ids

    listing = merchant.all_listings()[0]
    staged = asyncio.run(
        merchant.stage_price_update(
            operator_session,
            [PriceUpdateItem(listing_id=listing.listing_id, new_price=listing.price)],
        )
    )
    record.state.remember_change(staged)
    sessions.save(record)
    applied = client.post(f"/api/merchant/changes/{staged.change_id}/apply", headers=headers).json()
    assert applied["ok"] is True and applied["change"]["status"] == "applied"
    record = stored()
    assert record.pending_app_events == [
        f"Operator approved and applied change {staged.change_id} from the preview card."
    ]
    assert not record.state.approved_change_ids
    again = client.post(f"/api/merchant/changes/{staged.change_id}/apply", headers=headers)
    assert again.status_code == 400 and len(stored().pending_app_events) == 1


def test_a_deployment_supplies_its_own_session_stores(
    main, backend, merchant, merchant_identity, tmp_path
):
    """The six storage methods of ``SessionStore`` are what a deployment puts over its own
    database; the builders take that store, so the session routes read and write through it."""

    class Recording(SessionStore):
        def __init__(self, state_type):
            super().__init__(state_type)
            self.written: list[str] = []

        def write_state(self, session_id, document, version):
            self.written.append(session_id)
            super().write_state(session_id, document, version)

    shoppers = Recording(ShoppingSessionState)
    seed = tmp_path / "seed.json"
    seed.write_text("{}")
    host = build_storefront_host(
        title="contract",
        example_root=tmp_path,
        backend=backend,
        agent=main.host.agent,
        memory_seeder=MemorySeeder(seed, marker=tmp_path / "seeded"),
        sessions=shoppers,
    )
    assert host.sessions is shoppers
    with TestClient(host.app, base_url="http://localhost", client=LOOPBACK) as client:
        started = client.post("/api/session", json={"user_id": "demo-user"}).json()
        assert shoppers.written == [started["session_id"]]
        assert shoppers.require(started["session_id"]).user_id == "demo-user"
        headers = {SESSION_HEADER: started["session_id"]}
        assert client.get("/api/cart", headers=headers).status_code == 200

    operators = Recording(MerchantSessionState)
    agent = MerchantAgent(
        backend=merchant, config=merchant.config, memory_store=InMemoryMemoryStore()
    )
    app = FastAPI()
    app.include_router(
        build_merchant_router(
            storefront=backend,
            backend=merchant,
            agent=agent,
            identity=merchant_identity,
            example_dir="contract",
            sessions=operators,
        ),
        prefix="/api/merchant",
    )
    client = TestClient(app, base_url="http://localhost", client=LOOPBACK)
    headers = start_operator(client)
    assert operators.written == [headers[SESSION_HEADER]]
    assert operators.require(headers[SESSION_HEADER]).user_id == merchant_identity.merchant_id


def test_orders_route_lists_the_callers_own_orders_newest_first(client):
    other = start_shopper(client, "demo-user-2")
    mine = client.get("/api/orders", headers=start_shopper(client)).json()["orders"]
    theirs = client.get("/api/orders", headers=other).json()["orders"]
    placed = [order["placed_at"] for order in mine]
    assert mine and placed == sorted(placed, reverse=True)
    assert not {o["order_id"] for o in mine} & {o["order_id"] for o in theirs}


# -- storefront backend --------------------------------------------------------------


async def test_orders_are_newest_first_per_user_and_resolve_case_insensitively(
    backend, session, other_session
):
    orders = await backend.get_orders(session)
    assert orders and [order.placed_at for order in orders] == sorted(
        (o.placed_at for o in orders), reverse=True
    )
    found = await backend.get_order(session, orders[0].order_id.lower())
    assert found is not None and found.order_id == orders[0].order_id
    assert await backend.get_order(other_session, orders[0].order_id) is None


async def test_unknown_users_get_a_guest_profile(backend, session):
    known = await backend.get_preferences(session)
    assert known.user_id == session.user_id and known.display_name not in (None, "Guest")
    stranger = ShoppingSessionContext(session_id="s-9", user_id="someone-new")
    assert (await backend.get_preferences(stranger)).display_name == "Guest"


async def test_cart_lines_belong_to_the_session(backend, session, other_session, cart_product):
    await backend.add_to_cart(session, cart_product, 1)
    assert (await backend.add_to_cart(session, cart_product, 2)).item_count == 3
    assert (await backend.get_cart(other_session)).item_count == 0
    assert (await backend.update_cart_item(session, cart_product, 1)).item_count == 1
    assert (await backend.remove_from_cart(session, cart_product)).items == []


async def test_a_non_relevance_sort_keeps_only_relevant_matches(backend, session, relevance_probe):
    query, sort, hero, faint = relevance_probe
    results = await backend.search_products(session, query, SearchFilters(sort=sort), limit=6)
    ids = [product.product_id for product in results]
    assert ids and ids[0] == hero, ids
    assert set(ids).isdisjoint(faint), ids


# -- merchant backend ----------------------------------------------------------------


def test_portal_config_follows_the_host_approval_switch(request, monkeypatch):
    monkeypatch.setenv("MERCHANT_REQUIRE_HOST_APPROVAL", "0")
    assert request.getfixturevalue("merchant").config.require_host_approval is False


async def test_snapshot_periods_end_before_boot_and_compare_to_the_prior_block(
    merchant, operator_session
):
    latest = await merchant.get_business_snapshot(operator_session)
    assert latest.sales > 0 and latest.orders > 0
    period_end = date.fromisoformat(latest.period.split("/")[-1])
    assert 0 < (date.today() - period_end).days <= 7
    monthly = await merchant.get_business_snapshot(operator_session, "last_30_days")
    start, end = (date.fromisoformat(day) for day in monthly.period.split("/"))
    prior_start, prior_end = (date.fromisoformat(day) for day in monthly.compare_to.split("/"))
    assert (end - start).days == (prior_end - prior_start).days == 29
    assert prior_end == start - timedelta(days=1)
    assert monthly.sales_change_pct is not None


async def test_campaigns_and_issues_land_in_the_same_week_as_the_snapshot(
    merchant, operator_session
):
    period_end = date.fromisoformat(
        (await merchant.get_business_snapshot(operator_session)).period.split("/")[-1]
    )
    campaigns = await merchant.get_campaign_performance(operator_session)
    by_status = {status: [] for status in ("active", "ended", "paused", "draft")}
    for campaign in campaigns:
        by_status[campaign.status].append(campaign)
    assert by_status["active"] and by_status["ended"]
    for campaign in by_status["active"]:
        assert campaign.starts <= period_end.isoformat() <= campaign.ends, campaign.campaign_id
    for campaign in by_status["ended"]:
        assert campaign.ends < period_end.isoformat(), campaign.campaign_id
    issues = await merchant.get_order_issues(operator_session)
    assert issues
    for issue in issues:
        assert 0 <= (period_end - issue.opened_at.date()).days < 7, issue.issue_id


async def test_listing_ids_resolve_case_insensitively_and_browse_queries_return_the_universe(
    merchant, operator_session
):
    universe = [listing.listing_id for listing in merchant.all_listings()]
    first, second = universe[0], universe[1]
    detail = await merchant.get_listing(operator_session, first.lower())
    assert detail is not None and detail.listing_id == first
    assert await merchant.get_listing(operator_session, "ZZ-NOPE") is None
    named = await merchant.search_listings(operator_session, f"{second.lower()}, {first}")
    assert [listing.listing_id for listing in named] == [second, first]
    assert await merchant.search_listings(operator_session, "ZZ-NOPE") == []
    for query in ("", "*", "all"):
        browsed = await merchant.search_listings(operator_session, query, limit=len(universe) + 5)
        assert sorted(listing.listing_id for listing in browsed) == sorted(universe), query


async def test_pricing_context_repeats_the_deployments_movement_caps(merchant, operator_session):
    context = await merchant.get_pricing_context(
        operator_session, merchant.all_listings()[0].listing_id
    )
    assert context.max_price_delta_pct == merchant.config.max_price_delta_pct
    assert context.max_promotion_discount_pct == merchant.config.max_promotion_discount_pct
    assert await merchant.get_pricing_context(operator_session, "ZZ-NOPE") is None


async def test_every_staging_path_is_the_agents_proposal_and_applying_is_the_operators_act(
    merchant, operator_session
):
    listing = merchant.all_listings()[0]
    staged = [
        await merchant.stage_price_update(
            operator_session,
            [PriceUpdateItem(listing_id=listing.listing_id, new_price=listing.price)],
        ),
        await merchant.stage_listing_update(
            operator_session, listing.listing_id, {"title": listing.title}
        ),
        await merchant.stage_promotion(
            operator_session,
            PromotionDraft(
                name="Contract",
                listing_ids=[listing.listing_id],
                discount_pct=5,
                starts="2026-09-01",
                ends="2026-09-07",
            ),
        ),
        await merchant.stage_campaign(
            operator_session, CampaignDraft(name="Contract", budget=10.0)
        ),
    ]
    for change in staged:
        assert (
            change.created_by == operator_session.operator
            and change.created_by_kind is ActorKind.AGENT
        )
        assert change.applied_by is None
    applied = await merchant.apply_change(operator_session, staged[0].change_id)
    assert (
        applied.applied_by == operator_session.operator and applied.status is ChangeStatus.APPLIED
    )
    with pytest.raises(ChangeNotApplicable, match="applied"):
        await merchant.apply_change(operator_session, staged[0].change_id)
    context = await merchant.get_merchant_context(operator_session)
    assert context["operator"] == operator_session.operator


# -- showcase fixtures ---------------------------------------------------------------


def test_showcase_products_are_catalog_records_plus_the_backends_stamps(main, showcase_stamps):
    # The catalog as the backends load it: a family record's options are derived there.
    _, listings, variants = load_catalog(main.DATA_DIR)
    records = {
        product_id: product.model_dump(mode="json", exclude_unset=True)
        for product_id, product in (listings | variants).items()
    }
    for fixture in showcase_products(main.DATA_DIR.parent):
        record = records[fixture["product_id"]]
        pairs = [(key, value, record) for key, value in fixture.items() if key != "attributes"]
        pairs += [
            (key, value, record.get("attributes", {}))
            for key, value in fixture.get("attributes", {}).items()
        ]
        for key, value, source in pairs:
            if key in showcase_stamps and key not in source:
                continue
            assert source.get(key) == value, (fixture["product_id"], key)


# -- presentation extensions ---------------------------------------------------------


def test_presentation_extensions_advertise_their_payload_models(main, merchant_extensions):
    extensions = [*main.agent.extra_presentation_tools, *merchant_extensions]
    for extension in extensions:
        model_schema = extension.payload_model.model_json_schema()
        advertised = extension.input_schema["properties"]
        assert extension.input_schema.get("additionalProperties") is False, extension.name
        assert set(advertised) == set(model_schema["properties"]), extension.name
        assert sorted(extension.input_schema.get("required", [])) == sorted(
            model_schema.get("required", [])
        ), extension.name
        for name, field_schema in model_schema["properties"].items():
            if "maxItems" in field_schema:
                assert advertised[name].get("maxItems") == field_schema["maxItems"], (
                    extension.name,
                    name,
                )


# -- merchant writes -----------------------------------------------------------------


async def test_unknown_ids_are_refused_on_every_staging_path(merchant, operator_session):
    promotion = PromotionDraft(
        name="Ghost",
        listing_ids=["ZZ-NOPE"],
        discount_pct=10,
        starts="2026-08-01",
        ends="2026-08-10",
    )
    with pytest.raises(ValueError, match="no listing ZZ-NOPE"):
        await merchant.stage_price_update(
            operator_session, [PriceUpdateItem(listing_id="ZZ-NOPE", new_price=1.0)]
        )
    with pytest.raises(ValueError, match="no listing ZZ-NOPE"):
        await merchant.stage_inventory_action(
            operator_session,
            [InventoryActionItem(listing_id="ZZ-NOPE", action="restock", quantity=1)],
        )
    with pytest.raises(ValueError, match="no listing ZZ-NOPE"):
        await merchant.stage_promotion(operator_session, promotion)
    assert await merchant.get_pending_changes(operator_session) == []


async def test_campaign_previews_carry_budget_audience_and_copy(merchant, operator_session):
    draft = CampaignDraft(
        name="Spring", budget=120.0, audience="lapsed buyers", copy_text="Back in stock."
    )
    change = await merchant.stage_campaign(operator_session, draft)
    assert [(item.field, item.after) for item in change.items] == [
        ("budget", 120.0),
        ("audience", "lapsed buyers"),
        ("copy_text", "Back in stock."),
    ]


async def test_two_staged_restocks_both_count_when_applied(
    merchant, restockable_listing, operator_session
):
    if restockable_listing is None:
        pytest.skip("this vertical's inventory actions are not additive restocks")
    before = (await merchant.get_listing(operator_session, restockable_listing)).stock
    restock = [InventoryActionItem(listing_id=restockable_listing, action="restock", quantity=3)]
    first = await merchant.stage_inventory_action(operator_session, restock)
    second = await merchant.stage_inventory_action(operator_session, restock)
    await merchant.apply_change(operator_session, first.change_id)
    await merchant.apply_change(operator_session, second.change_id)
    assert (await merchant.get_listing(operator_session, restockable_listing)).stock == before + 6
