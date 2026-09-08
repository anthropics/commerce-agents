// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useState } from "react";
import { type AgentEvent, type Profile, StoreShell, type StoreView, useAgentTurn, useCatalogIndex, useResource, useSession, useWebMcpTools } from "web-shared";
import Chat from "@/components/Chat";
import OrderPanel from "@/components/OrderPanel";
import AccountView from "@/components/views/AccountView";
import HomeView from "@/components/views/HomeView";
import { api, fetchAccount, fetchProducts, UNREACHABLE, webMcpTools } from "@/lib/api";
import { formatPrice, splitCart } from "@/lib/format";
import type { CartPayload } from "@/lib/types";

const PROFILES: Profile[] = [
  { id: "demo-user", name: "Riley" },
  { id: "demo-user-2", name: "Casey" },
];

const ASSISTANT = "ACME Assistant";

type View = "assistant" | "account";

const VIEWS: StoreView<View>[] = [
  { id: "assistant", label: "Assistant", icon: "spark" },
  { id: "account", label: "Account", icon: "signal" },
];

/** The plan matrix arrives as one final event, so its skeleton mounts on the call. */
const pendingComponent = (tool: string) => (tool === "present_plan_comparison" ? "plan_matrix" : null);

function Wordmark() {
  return (
    <span className="flex items-baseline gap-2 pr-1 text-[19px] leading-none text-(--ink)">
      <span className="am-live self-center" aria-hidden />
      <span style={{ fontWeight: 800, letterSpacing: "-0.02em" }}>ACME</span>
      <span style={{ fontWeight: 100, letterSpacing: "0.01em" }}>Mobile</span>
    </span>
  );
}

export default function StorefrontPage() {
  const [profileId, setProfileId] = useState(PROFILES[0].id);
  const profile = PROFILES.find((candidate) => candidate.id === profileId) ?? PROFILES[0];
  // A session is bound to one profile, so switching remounts the storefront and signs in again.
  return <Storefront key={profile.id} profile={profile} onSwitchProfile={setProfileId} />;
}

function Storefront({ profile, onSwitchProfile }: { profile: Profile; onSwitchProfile: (id: string) => void }) {
  const session = useSession(api, { profile: profile.id });
  useWebMcpTools(Boolean(session.sessionId), webMcpTools);
  const [view, setView] = useState<View>("assistant");
  const [cart, setCart] = useState<CartPayload | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const catalog = useCatalogIndex(fetchProducts);

  const onEvent = useCallback((event: AgentEvent) => {
    if (event.type === "cart_update") setCart(event.data.cart as CartPayload);
  }, []);

  const chat = useAgentTurn(api, { ...session, unreachable: UNREACHABLE, onEvent, pendingComponent });
  // A reply may have changed the plan or placed an order, so both re-read after each one.
  const account = useResource(session.sessionId ? fetchAccount : null, [session.sessionId, chat.completed]);
  const { data: orders } = useResource(session.sessionId ? () => api.fetchOrders() : null, [session.sessionId, chat.completed]);

  useEffect(() => {
    if (session.sessionId) void api.fetchCart<CartPayload>().then((next) => next && setCart(next));
  }, [session.sessionId]);

  const { monthly, today } = splitCart(cart?.items ?? [], catalog);
  const figure = [monthly > 0 ? `${formatPrice(monthly)}/mo` : null, today > 0 ? `${formatPrice(today)} today` : null].filter(Boolean).join(" + ");
  const shopper = session.shopper ?? { name: profile.name };

  return (
    <StoreShell
      brand={<Wordmark />}
      views={VIEWS}
      view={view}
      onViewChange={setView}
      chat={chat}
      api={api}
      assistantName={ASSISTANT}
      shopper={shopper}
      profiles={PROFILES}
      profileId={profile.id}
      onSwitchProfile={onSwitchProfile}
      bag={{ label: "Order", count: cart?.item_count ?? 0, noun: "item", figure: figure || null }}
      panel={<OrderPanel cart={cart} account={account.data?.account ?? null} />}
      panelOpen={panelOpen}
      onPanelOpenChange={setPanelOpen}
      placeholder={view === "account" ? "Ask about your plan, an upgrade, a charge…" : "Ask about plans, phones, a fee…"}
    >
      {/* The conversation stays mounted under the other view so its cards keep their state. */}
      <div className={view === "assistant" ? "h-full" : "hidden"}>
        <Chat chat={chat} account={account.data?.account ?? null} onCartUpdate={setCart} home={<HomeView shopperName={shopper.name} account={account.data} />} />
      </div>
      {view === "account" ? (
        <AccountView shopperName={shopper.name} account={account.data} failed={account.failed} orders={orders} />
      ) : null}
    </StoreShell>
  );
}
