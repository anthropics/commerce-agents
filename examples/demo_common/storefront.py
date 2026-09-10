# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The storefront half of a demo API: one app carrying the routes every vertical's
storefront web app calls.

    POST   /api/session               bind a session to a demo profile, return its id and name
    POST   /api/chat                  one turn, streamed as SSE AgentEvents
    GET    /api/products[/{id}]       catalog reads (public)
    GET    /api/cart                  the session's cart
    GET    /api/orders                the session user's orders, newest first
    GET/PATCH/DELETE /api/memory      what is remembered about the session's user
    POST   /api/reset                 drop the session; optionally re-seed or purge memory
    GET    /api/health                (public)

Every route except session start, the catalog reads, and health identifies the caller by
the session id alone. A vertical adds its own routes to ``StorefrontHost.app`` using the same
dependency, and mounts a direct add-to-cart button through ``StorefrontHost.direct_add``.
"""

# Route parameters below are annotated with dependencies built at call time, so this
# module evaluates its annotations eagerly (no ``from __future__ import annotations``).

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from commerce_common.memory import MemoryStore, MemoryWriteRejected
from shopping_agent import PageContext, ProductDetails, ShoppingSessionContext, ShoppingSessionState
from shopping_agent.fencing import STOREFRONT_FENCE
from shopping_agent.gates import OPTIONS_GATE, PROVENANCE_GATE
from shopping_agent.serialization import cart_payload as serialize_cart
from shopping_agent_runtime import ShoppingAgent

from .host import DemoStorefront, append_user_turn, build_app, stream_turn
from .memory import MemoryFactEdit, MemorySeeder, install_memory_routes
from .sessions import SessionRecord, SessionStore, session_dependency
from .storefront_fixtures import SUMMARY_EXCLUDES

StorefrontRecord = SessionRecord[ShoppingSessionState]

# What the add button is told when a cart gate holds its write.
_HELD_ADD_TEXT = {
    PROVENANCE_GATE: "Product not in this session's results",
    OPTIONS_GATE: "Choose the product's options with the assistant before adding it",
}


class StartSessionRequest(BaseModel):
    # The demo's stand-in for a credential: a profile from data/users.json.
    user_id: str = Field(default="demo-user", min_length=1, max_length=64)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    page: PageContext | None = None


class CartAddRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=80)
    quantity: int = Field(default=1, ge=1)


class ResetRequest(BaseModel):
    # clear_memory deletes the user's facts and restores the fixture seed (a demo reset);
    # purge_memory deletes them and stops there (the deletion a real deployment offers).
    clear_memory: bool = False
    purge_memory: bool = False


class StorefrontHost:
    """The constructed storefront: the app plus the handles a vertical's own routes use."""

    def __init__(
        self,
        *,
        title: str,
        backend: DemoStorefront,
        agent: ShoppingAgent,
        env_hint: str,
        cart_extras: Callable[[StorefrontRecord], dict[str, Any]] | None,
        on_startup: Sequence[Callable[[], Awaitable[None]]] = (),
        sessions: SessionStore[ShoppingSessionState] | None = None,
    ) -> None:
        self.app = build_app(title, on_startup)
        self.backend = backend
        self.agent = agent
        self.memory_store = cast(MemoryStore, agent.memory.store)
        # A deployment's own store (the six storage methods over its database) or the
        # in-memory one for the examples.
        self.sessions: SessionStore[ShoppingSessionState] = sessions or SessionStore(
            ShoppingSessionState
        )
        # The parameter annotation a vertical's own routes use: ``record: host.CurrentSession``.
        self.CurrentSession = session_dependency(self.sessions, "/api/session")
        self._env_hint = env_hint
        self._cart_extras = cart_extras or (lambda record: {})

    def context(
        self, record: StorefrontRecord, page: PageContext | None = None
    ) -> ShoppingSessionContext:
        """The agent's view of a request: identity from the record, the clock from the
        host (a deployment passes the user's timezone instead)."""
        return ShoppingSessionContext(
            session_id=record.session_id,
            user_id=record.user_id,
            page=page or PageContext(),
            now=datetime.now(),
        )

    def chat(self, request: ChatRequest, record: StorefrontRecord) -> StreamingResponse:
        append_user_turn(record, request.message, "App events")
        return stream_turn(
            self.agent,
            self.sessions,
            record,
            self.context(record, request.page),
            env_hint=self._env_hint,
        )

    async def cart_payload(self, record: StorefrontRecord) -> dict[str, Any]:
        cart = await self.backend.get_cart(self.context(record))
        return serialize_cart(cart) | self._cart_extras(record)

    async def direct_add(
        self, record: StorefrontRecord, request: CartAddRequest, *, note: str
    ) -> dict[str, Any]:
        """A UI button's add, run through the same executor as the agent's ``add_to_cart``
        so provenance and quantity caps hold. ``note`` (``{title}``, ``{product_id}``,
        ``{quantity}``) is what the model is told on the next turn."""
        executor = self.agent.executor_class(
            backend=self.backend,
            config=self.agent.config,
            skills=self.agent.skills,
            session=self.context(record),
            state=record.state,
            memory=self.agent.memory,
        )
        execution = await executor.execute(
            "add_to_cart", {"product_id": request.product_id, "quantity": request.quantity}
        )
        if execution.blocked or execution.is_error:
            # The result text is written for the model; the button gets its first sentence,
            # or the gate's short reason.
            detail = _HELD_ADD_TEXT.get(execution.blocked or "", execution.result_text)
            raise HTTPException(status_code=400, detail=detail.split(". ")[0] + ".")
        product = record.state.seen_products.get(request.product_id)
        if product is None:
            raise HTTPException(status_code=400, detail="Product not in this session's results")
        # The title is catalog-authored and the note enters model context unfenced.
        record.pending_app_events.append(
            note.format(
                title=STOREFRONT_FENCE.sanitize_text(product.title, max_chars=120),
                product_id=product.product_id,
                quantity=request.quantity,
            )
        )
        cart = next((e.data.get("cart") for e in execution.events if e.type == "cart_update"), None)
        return {"ok": True, "cart": cart, **self._cart_extras(record)}


def build_storefront_host(
    *,
    title: str,
    example_root: Path,
    backend: DemoStorefront,
    agent: ShoppingAgent,
    memory_seeder: MemorySeeder,
    product_of: Callable[[str], ProductDetails | None] | None = None,
    product_detail: Callable[[ProductDetails], dict[str, Any]] | None = None,
    cart_extras: Callable[[StorefrontRecord], dict[str, Any]] | None = None,
    before_turn: Callable[[], None] | None = None,
    sessions: SessionStore[ShoppingSessionState] | None = None,
) -> StorefrontHost:
    """Seed memory, then build the app with the shared routes. ``product_of`` and
    ``product_detail`` let a vertical stamp live state onto catalog reads or enrich the
    detail payload; ``cart_extras`` adds keys to every cart payload; ``before_turn`` runs
    ahead of each chat turn (a vertical delivering server-side events as app events);
    ``sessions`` is a deployment's own ``SessionStore`` (its storage methods over a shared
    database), the in-memory store when omitted."""
    host = StorefrontHost(
        title=title,
        backend=backend,
        agent=agent,
        env_hint=f"examples/{example_root.name}/.env",
        cart_extras=cart_extras,
        sessions=sessions,
        # Seed the memory fixtures when the app starts, inside its event loop.
        on_startup=[lambda: memory_seeder.seed_at_boot(cast(MemoryStore, agent.memory.store))],
    )
    app = host.app
    read_product = product_of or backend.product
    detail_of = product_detail or (lambda product: product.model_dump())
    CurrentSession = host.CurrentSession

    @app.post("/api/session")
    async def start_session(request: StartSessionRequest | None = None) -> dict:
        record = host.sessions.start((request or StartSessionRequest()).user_id)
        profile = await backend.get_preferences(host.context(record))
        return {
            "session_id": record.session_id,
            "user_id": record.user_id,
            "name": profile.display_name,
            "tier": profile.loyalty_tier,
        }

    # A vertical's before_turn runs ahead of the record's load, so what it queues is in the turn.
    @app.post("/api/chat", dependencies=[Depends(before_turn)] if before_turn else [])
    async def chat(request: ChatRequest, record: CurrentSession) -> StreamingResponse:
        return host.chat(request, record)

    @app.get("/api/products")
    async def list_products(category: str | None = None, limit: int = 24) -> dict:
        products = [product for pid in backend.products if (product := read_product(pid))]
        if category:
            products = [product for product in products if product.category == category]
        page = products[: max(1, min(limit, 100))]
        return {"products": [product.model_dump(exclude=set(SUMMARY_EXCLUDES)) for product in page]}

    @app.get("/api/products/{product_id:path}")  # an id may contain "/"
    async def get_product(product_id: str) -> dict:
        product = read_product(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return detail_of(product)

    @app.get("/api/cart")
    async def get_cart(record: CurrentSession) -> dict:
        return await host.cart_payload(record)

    @app.get("/api/orders")
    async def list_orders(record: CurrentSession) -> dict:
        orders = await backend.get_orders(host.context(record), limit=20)
        return {"orders": [order.model_dump(mode="json") for order in orders]}

    install_memory_routes(
        app, "/api/memory", current_session=host.CurrentSession, memory_store=host.memory_store
    )

    @app.patch("/api/memory")
    async def edit_memory_fact(edit: MemoryFactEdit, record: CurrentSession) -> dict:
        existing = {fact.key: fact for fact in await host.memory_store.get_facts(record.user_id)}
        if edit.key not in existing:
            raise HTTPException(status_code=404, detail="No such fact")
        try:
            corrected = agent.memory.validate(
                edit.key,
                edit.value,
                existing[edit.key].category.value,
                source_session_id=existing[edit.key].source_session_id,
            )
        except MemoryWriteRejected as rejected:
            raise HTTPException(status_code=400, detail=str(rejected)) from None
        if not corrected.value:
            raise HTTPException(status_code=400, detail="Value must not be empty")
        await host.memory_store.upsert_facts(record.user_id, [corrected])
        return {"ok": True, "fact": corrected.model_dump(mode="json")}

    @app.post("/api/reset")
    async def reset(request: ResetRequest, record: CurrentSession) -> dict:
        if request.purge_memory or request.clear_memory:
            # clear() also advances the purge generation, so an extraction still running
            # for this user discards its result.
            await host.memory_store.clear(record.user_id)
            if request.clear_memory and not request.purge_memory:
                await memory_seeder.reseed(host.memory_store, record.user_id)
        host.sessions.reset(record)
        backend.reset_session(record.session_id)
        fresh = host.sessions.start(record.user_id)
        return {"ok": True, "session_id": fresh.session_id}

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "ok": True,
            "store": backend.store_name,
            "products": len(backend.products),
            "skills": agent.skills.names,
            "model": agent.config.model,
        }

    return host
