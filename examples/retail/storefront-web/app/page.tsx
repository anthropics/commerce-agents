// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useState } from "react";
import { type AgentEvent, formatMoney, OrdersView, plural, StoreShell, type StoreView, upcoming, useAgentTurn, useResource, useSession, useWebMcpTools } from "web-shared";
import CartPanel from "@/components/CartPanel";
import Chat from "@/components/Chat";
import HomeView from "@/components/views/HomeView";
import { api, UNREACHABLE, webMcpTools } from "@/lib/api";
import { NOUNS, OrderThumb } from "@/lib/orders";
import type { CartPayload } from "@/lib/types";

type View = "assistant" | "orders";

const ASSISTANT = "ACME Assistant";

function Wordmark() {
  return (
    <span className="flex items-center gap-2.5 pr-1">
      <span aria-hidden className="grid h-[30px] w-[30px] place-items-center rounded-lg bg-(--ink) text-[15px] font-bold text-(--surface)">
        A
      </span>
      <span className="text-[17px] font-bold tracking-[-0.02em] text-(--ink)">ACME</span>
    </span>
  );
}

export default function StorefrontPage() {
  const session = useSession(api);
  useWebMcpTools(Boolean(session.sessionId), webMcpTools);
  const [view, setView] = useState<View>("assistant");
  const [cart, setCart] = useState<CartPayload | null>(null);
  // A staged checkout owns the panel's primary action until the cart changes again.
  const [checkoutStaged, setCheckoutStaged] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);

  const handleCartUpdate = useCallback((next: CartPayload) => {
    setCart(next);
    setCheckoutStaged(false);
  }, []);

  const onEvent = useCallback(
    (event: AgentEvent) => {
      if (event.type === "cart_update") handleCartUpdate(event.data.cart as CartPayload);
      else if (event.type === "ui" && event.data.component === "checkout") setCheckoutStaged(true);
    },
    [handleCartUpdate],
  );

  const chat = useAgentTurn(api, { ...session, unreachable: UNREACHABLE, onEvent });
  // A reply may have started a return, so orders re-read after each one.
  const { data: orders, failed: ordersFailed } = useResource(session.sessionId ? () => api.fetchOrders() : null, [session.sessionId, chat.completed]);

  useEffect(() => {
    if (session.sessionId) void api.fetchCart<CartPayload>().then((next) => next && setCart(next));
  }, [session.sessionId]);

  const late = orders?.filter((order) => order.status === "delayed").length ?? 0;
  const views: StoreView<View>[] = [
    { id: "assistant", label: "Assistant", icon: "spark" },
    { id: "orders", label: "Orders", icon: "box", attention: late ? { count: late, label: `${late} delayed` } : null },
  ];
  const shopper = session.shopper ?? { name: "Guest" };
  const count = cart?.item_count ?? 0;

  return (
    <StoreShell
      brand={<Wordmark />}
      views={views}
      view={view}
      onViewChange={setView}
      chat={chat}
      api={api}
      assistantName={ASSISTANT}
      shopper={shopper}
      bag={{ label: "Cart", count, noun: "item", figure: count ? formatMoney(cart?.subtotal ?? 0, cart?.currency) : null }}
      panel={<CartPanel cart={cart} checkoutStaged={checkoutStaged} />}
      panelOpen={panelOpen}
      onPanelOpenChange={setPanelOpen}
      placeholder={view === "orders" ? "Ask about an order, a return, a delivery…" : "Ask about a product, a project, an order…"}
    >
      {/* The conversation stays mounted under the other view so its cards keep their state. */}
      <div className={view === "assistant" ? "h-full" : "hidden"}>
        <Chat chat={chat} onCartUpdate={handleCartUpdate} home={<HomeView shopperName={shopper.name} orders={orders} ordersFailed={ordersFailed} onSeeOrders={() => setView("orders")} />} />
      </div>
      {view === "orders" ? (
        <OrdersView
          orders={orders}
          failed={ordersFailed}
          nouns={NOUNS}
          subtitle={
            orders
              ? late
                ? `${plural(late, "order")} running late. Ask why, or ask about a return on anything delivered.`
                : `${plural(upcoming(orders).length, "order")} on the way. Ask about any of them, or about a return on anything delivered.`
              : undefined
          }
          thumb={(order) => <OrderThumb order={order} />}
        />
      ) : null}
    </StoreShell>
  );
}
