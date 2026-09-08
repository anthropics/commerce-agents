// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useState } from "react";
import { type AgentEvent, OrdersView, plural, StoreShell, type StoreView, upcoming, useAgentTurn, useResource, useSession, useWebMcpTools } from "web-shared";
import Chat from "@/components/Chat";
import TripPanel from "@/components/TripPanel";
import HomeView from "@/components/views/HomeView";
import { api, UNREACHABLE, webMcpTools } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import { NOUNS, TripThumb } from "@/lib/orders";
import type { CartPayload } from "@/lib/types";

type View = "assistant" | "trips";

const ASSISTANT = "ACME Assistant";

function Wordmark() {
  return (
    <span className="al-display pr-1 text-[22px] italic leading-none text-(--ink)" style={{ fontWeight: 650 }}>
      <span className="mr-1 not-italic text-[13px] text-(--accent)" aria-hidden>
        ◈
      </span>
      ACME Travel
    </span>
  );
}

export default function StorefrontPage() {
  const session = useSession(api);
  useWebMcpTools(Boolean(session.sessionId), webMcpTools);
  const [view, setView] = useState<View>("assistant");
  const [cart, setCart] = useState<CartPayload | null>(null);
  // A staged checkout owns the panel's primary action until the trip changes again.
  const [checkoutStaged, setCheckoutStaged] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);

  const onEvent = useCallback((event: AgentEvent) => {
    if (event.type === "cart_update") {
      setCart(event.data.cart as CartPayload);
      setCheckoutStaged(false);
    } else if (event.type === "ui" && event.data.component === "checkout") {
      setCheckoutStaged(true);
    }
  }, []);

  const chat = useAgentTurn(api, { ...session, unreachable: UNREACHABLE, onEvent });
  // A reply may have changed or refunded a booking, so trips re-read after each one.
  const { data: trips, failed: tripsFailed } = useResource(session.sessionId ? () => api.fetchOrders() : null, [session.sessionId, chat.completed]);

  useEffect(() => {
    if (session.sessionId) void api.fetchCart<CartPayload>().then((next) => next && setCart(next));
  }, [session.sessionId]);

  const views: StoreView<View>[] = [
    { id: "assistant", label: "Assistant", icon: "spark" },
    { id: "trips", label: "Trips", icon: "plane" },
  ];
  const shopper = session.shopper ?? { name: "Guest" };
  const count = cart?.items.length ?? 0;

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
      bag={{ label: "Trip", count, noun: "booking", figure: count ? formatPrice(cart?.subtotal ?? 0) : null }}
      panel={<TripPanel cart={cart} checkoutStaged={checkoutStaged} />}
      panelOpen={panelOpen}
      onPanelOpenChange={setPanelOpen}
      placeholder={view === "trips" ? "Ask about a trip, a change, a refund…" : "Ask about a trip, a flight, a booking…"}
    >
      {/* The conversation stays mounted under the other view so its cards keep their state. */}
      <div className={view === "assistant" ? "h-full" : "hidden"}>
        <Chat chat={chat} home={<HomeView travelerName={shopper.name} trips={trips} tripsFailed={tripsFailed} onSeeTrips={() => setView("trips")} />} />
      </div>
      {view === "trips" ? (
        <OrdersView
          orders={trips}
          failed={tripsFailed}
          nouns={NOUNS}
          subtitle={trips ? `${plural(upcoming(trips).length, "trip")} coming up. Ask about any of them, or plan the next one from a past trip.` : undefined}
          thumb={(order) => <TripThumb order={order} />}
        />
      ) : null}
    </StoreShell>
  );
}
