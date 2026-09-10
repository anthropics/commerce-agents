# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The merchant half of a demo API, mounted under ``/api/merchant`` in the same process
as the storefront so an approved change shows up in the storefront at once.

    POST   /session                    start a session bound to the process's one merchant
    POST   /chat                       one assistant turn, streamed as SSE
    GET    /overview, /listings[/{id}], /alerts     the portal widgets' reads
    POST   /changes/{id}/apply|discard the preview card's buttons, through the same gate
                                       as the assistant's own apply/discard tools
    GET/DELETE /memory                 what the assistant remembers about the store
    POST   /reset                      drop the session; optionally purge memory
    GET    /health                     (public)

The merchant identity is held server-side; a session id only proves the caller started a
portal session. A vertical's own portal reads are passed in as ``portal_reads``.
"""

# Route parameters below are annotated with dependencies built at call time, so this
# module evaluates its annotations eagerly (no ``from __future__ import annotations``).

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from commerce_common.memory import MemoryStore
from merchant_agent import (
    BusinessSnapshot,
    ChangeLedger,
    InventoryAlert,
    Listing,
    ListingDetails,
    MerchantSessionContext,
    MerchantSessionState,
    OrderIssue,
    PricingContext,
    StagedChange,
)
from merchant_agent.executor import MerchantToolExecutor
from merchant_agent_runtime import MerchantAgent

from .host import DemoStorefront, append_user_turn, stream_turn
from .memory import install_memory_routes
from .sessions import SessionRecord, SessionStore, session_dependency

MerchantRecord = SessionRecord[MerchantSessionState]

_LISTINGS_CAP = 100


class DemoMerchant(Protocol):
    """What the router uses from a vertical's mock merchant on top of the
    ``MerchantBackend`` interface it implements."""

    ledger: ChangeLedger

    def all_listings(self) -> list[Listing]: ...

    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot: ...

    async def get_inventory_alerts(
        self, session: MerchantSessionContext
    ) -> list[InventoryAlert]: ...

    async def get_order_issues(self, session: MerchantSessionContext) -> list[OrderIssue]: ...

    async def get_pending_changes(self, session: MerchantSessionContext) -> list[StagedChange]: ...

    async def search_listings(
        self, session: MerchantSessionContext, query: str, filters: Any, limit: int
    ) -> list[Listing]: ...

    async def get_listing(
        self, session: MerchantSessionContext, listing_id: str
    ) -> ListingDetails | None: ...

    async def get_pricing_context(
        self, session: MerchantSessionContext, listing_id: str
    ) -> PricingContext | None: ...


@dataclass(frozen=True)
class MerchantIdentity:
    """The one tenant a demo process serves and the fixture operator acting for it; the
    operator name is what the ledger stamps on staged and applied changes."""

    merchant_id: str
    operator: str


class MerchantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class MerchantResetRequest(BaseModel):
    purge_memory: bool = False


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)


def build_merchant_router(
    *,
    storefront: DemoStorefront,
    backend: DemoMerchant,
    agent: MerchantAgent,
    identity: MerchantIdentity,
    example_dir: str,
    overview_extras: Callable[[], dict[str, Any]] | None = None,
    portal_reads: Mapping[str, Callable[[], Any]] | None = None,
    sessions: SessionStore[MerchantSessionState] | None = None,
) -> APIRouter:
    """The router above, over an agent the vertical has already constructed.
    ``overview_extras`` supplies the vertical's own home-page keys on ``/overview``;
    ``portal_reads`` maps a path to a callable (sync or async) served as a scoped GET;
    ``sessions`` is a deployment's own ``SessionStore`` (its storage methods over a shared
    database), the in-memory store when omitted."""
    memory_store = cast(MemoryStore, agent.memory.store)
    sessions = sessions or SessionStore(MerchantSessionState)
    CurrentSession = session_dependency(sessions, "/api/merchant/session")
    router = APIRouter()

    def context(record: MerchantRecord) -> MerchantSessionContext:
        return MerchantSessionContext(
            session_id=record.session_id,
            merchant_id=identity.merchant_id,
            operator=identity.operator,
            now=datetime.now(),
        )

    @router.post("/session")
    async def start_session() -> dict:
        record = sessions.start(identity.merchant_id)
        return {
            "session_id": record.session_id,
            "merchant_id": identity.merchant_id,
            "operator": identity.operator,
        }

    @router.post("/chat")
    async def chat(request: MerchantChatRequest, record: CurrentSession) -> StreamingResponse:
        append_user_turn(record, request.message, "Portal events")
        return stream_turn(
            agent, sessions, record, context(record), env_hint=f"examples/{example_dir}/.env"
        )

    @router.get("/overview")
    async def overview(record: CurrentSession) -> dict:
        session = context(record)
        snapshot = await backend.get_business_snapshot(session)
        alerts = await backend.get_inventory_alerts(session)
        issues = await backend.get_order_issues(session)
        pending = await backend.get_pending_changes(session)
        resolved = sorted(
            backend.ledger.resolved(),
            key=lambda change: change.applied_at or change.discarded_at or change.created_at,
            reverse=True,
        )
        return {
            "snapshot": _dump(snapshot),
            "needs_attention": {
                "inventory": [alert.model_dump(exclude_none=True) for alert in alerts[:6]],
                "order_issues": [_dump(issue) for issue in issues[:6]],
                "pending_changes": [_dump(change) for change in pending[:6]],
            },
            "recent_orders": [
                {
                    "order_id": order.order_id,
                    "status": order.status.value,
                    "placed_at": order.placed_at.isoformat(),
                    "total": order.total,
                    "items": sum(item.quantity for item in order.items),
                }
                for order in storefront.recent_orders(6)
            ],
            "recent_changes": [_dump(change) for change in resolved[:8]],
            **(overview_extras() if overview_extras is not None else {}),
        }

    @router.get("/listings")
    async def listings(
        record: CurrentSession, query: str | None = None, limit: int = _LISTINGS_CAP
    ) -> dict:
        # `total` counts every match; the page is sliced afterwards.
        if query:
            results = await backend.search_listings(context(record), query, None, _LISTINGS_CAP)
        else:
            results = backend.all_listings()
        page = results[: max(1, min(limit, _LISTINGS_CAP))]
        return {
            "total": len(results),
            "listings": [listing.model_dump(exclude_none=True) for listing in page],
        }

    # Ids are opaque strings; ":path" lets one that contains "/" (a platform's global id)
    # reach the handler instead of missing the route.
    @router.get("/listings/{listing_id:path}")
    async def listing_detail(listing_id: str, record: CurrentSession) -> dict:
        session = context(record)
        listing = await backend.get_listing(session, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="Listing not found")
        pricing = await backend.get_pricing_context(session, listing.listing_id)
        return {
            "listing": _dump(listing),
            "pricing": pricing.model_dump(exclude_none=True) if pricing else None,
        }

    @router.get("/alerts")
    async def alerts(record: CurrentSession) -> dict:
        session = context(record)
        return {
            "inventory": [
                alert.model_dump(exclude_none=True)
                for alert in await backend.get_inventory_alerts(session)
            ],
            "order_issues": [_dump(issue) for issue in await backend.get_order_issues(session)],
        }

    def portal_route(read: Callable[[], Any]) -> Callable[..., Any]:
        async def route(record: CurrentSession) -> Any:
            del record  # scoped like the other portal reads; the payload itself is store-wide
            result = read()
            return await result if inspect.isawaitable(result) else result

        return route

    for path, read in (portal_reads or {}).items():
        router.add_api_route(path, portal_route(read), methods=["GET"])

    async def change_action(change_id: str, action: str, record: MerchantRecord) -> dict:
        """``{"ok": true, "change": record}`` when the change moved; ``{"ok": false,
        "change": null, "reason": text}`` when a gate held it; 400 when the call failed.
        A click on the preview card is the host's own approval or dismissal, so the id is
        marked before the executor runs; neither mark outlives the click, whatever the
        outcome, so a later chat turn cannot spend one."""
        if action == "apply_change":
            record.state.approved_change_ids.add(change_id)
        else:
            record.state.host_action_change_ids.add(change_id)
        executor = MerchantToolExecutor(
            backend=backend,
            config=agent.config,
            skills=agent.skills,
            session=context(record),
            state=record.state,
            memory=agent.memory,
        )
        execution = await executor.execute(action, {"change_id": change_id})
        record.state.host_action_change_ids.discard(change_id)
        record.state.approved_change_ids.discard(change_id)
        if execution.is_error:
            raise HTTPException(status_code=400, detail=execution.result_text)
        if execution.blocked is not None:
            return {"ok": False, "change": None, "reason": execution.result_text}
        verb = "approved and applied" if action == "apply_change" else "dismissed"
        record.pending_app_events.append(
            f"Operator {verb} change {change_id} from the preview card."
        )
        change = next(
            (
                event.data.get("change")
                for event in execution.events
                if event.type == "change_update"
            ),
            None,
        )
        return {"ok": True, "change": change}

    @router.post("/changes/{change_id:path}/apply")
    async def apply_change(change_id: str, record: CurrentSession) -> dict:
        return await change_action(change_id, "apply_change", record)

    @router.post("/changes/{change_id:path}/discard")
    async def discard_change(change_id: str, record: CurrentSession) -> dict:
        return await change_action(change_id, "discard_change", record)

    install_memory_routes(
        router, "/memory", current_session=CurrentSession, memory_store=memory_store
    )

    @router.post("/reset")
    async def reset(request: MerchantResetRequest, record: CurrentSession) -> dict:
        if request.purge_memory:
            await memory_store.clear(record.user_id)
        sessions.reset(record)
        fresh = sessions.start(record.user_id)
        return {"ok": True, "session_id": fresh.session_id}

    @router.get("/health")
    async def health() -> dict:
        return {
            "ok": True,
            "store": storefront.store_name,
            "role": "merchant",
            "listings": len(storefront.products),
            "skills": agent.skills.names,
            "model": agent.config.model,
        }

    return router
