// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useState } from "react";
import { type AgentEvent, type Profile, type Session, StoreShell, type StoreView, useAgentTurn, useSession, useWebMcpTools } from "web-shared";
import Chat from "@/components/Chat";
import { PRESENTATION_COMPONENTS } from "@/components/generative/Bones";
import { CountdownArc } from "@/components/generative/shared";
import NightPanel from "@/components/NightPanel";
import OfferBanner from "@/components/OfferBanner";
import HomeView from "@/components/views/HomeView";
import TicketsView from "@/components/views/TicketsView";
import { api, UNREACHABLE, webMcpTools } from "@/lib/api";
import { countdownTone, formatCountdown } from "@/lib/format";
import { LiveProvider, useCountdown, useLive } from "@/lib/live";
import type { CartPayload } from "@/lib/types";

const PROFILES: Profile[] = [
  { id: "demo-user", name: "Riley" },
  { id: "demo-user-2", name: "Casey" },
];

const ASSISTANT = "ACME Assistant";

type View = "assistant" | "tickets";

const pendingComponent = (tool: string) => PRESENTATION_COMPONENTS[tool];

const VIEWS: StoreView<View>[] = [
  { id: "assistant", label: "Assistant", icon: "spark" },
  { id: "tickets", label: "Tickets", icon: "ticket" },
];

interface StoreProps {
  session: Session;
  cart: CartPayload | null;
  onCartUpdate: (cart: CartPayload) => void;
  profile: Profile;
  onSwitchProfile: (id: string) => void;
}

function Wordmark({ warm }: { warm: boolean }) {
  return (
    <span className="flex items-baseline gap-2 pr-1 leading-none text-(--ink)">
      <span className={`at-live self-center ${warm ? "at-live--warm" : ""}`} aria-hidden />
      <span className="at-display text-[21px] tracking-[0.04em]">ACME</span>
      <span className="at-display text-[21px] font-medium tracking-[0.09em] text-(--ink-soft)">TICKETS</span>
    </span>
  );
}

/** The soonest hold's countdown, inside the bag button. */
function HoldTimer({ deadline }: { deadline: number | null }) {
  const { holdMinutes } = useLive();
  const seconds = useCountdown(deadline);
  if (seconds == null) return null;
  return (
    <span
      className={`at-mono flex items-center gap-1 text-[11.5px] font-semibold ${seconds <= 30 ? "at-urgent" : ""}`}
      style={{ color: countdownTone(seconds) }}
      title="Hold expires; the seats release to other buyers at 0:00"
    >
      <CountdownArc fraction={seconds / (holdMinutes * 60)} tone={countdownTone(seconds)} size={12} strokeWidth={4} />
      {formatCountdown(seconds)}
    </span>
  );
}

export default function StorefrontPage() {
  const [profileId, setProfileId] = useState(PROFILES[0].id);
  const profile = PROFILES.find((candidate) => candidate.id === profileId) ?? PROFILES[0];
  // A session is bound to one profile, so switching remounts the storefront and signs in again.
  return <Storefront key={profile.id} profile={profile} onSwitchProfile={setProfileId} />;
}

/** The live holds, offers, and wallet need the session before anything below reads them. */
function Storefront({ profile, onSwitchProfile }: Pick<StoreProps, "profile" | "onSwitchProfile">) {
  const session = useSession(api, { profile: profile.id });
  useWebMcpTools(Boolean(session.sessionId), webMcpTools);
  const [cart, setCart] = useState<CartPayload | null>(null);
  return (
    <LiveProvider ready={session.sessionId !== null} onCartUpdate={setCart}>
      <Store session={session} cart={cart} onCartUpdate={setCart} profile={profile} onSwitchProfile={onSwitchProfile} />
    </LiveProvider>
  );
}

function Store({ session, cart, onCartUpdate, profile, onSwitchProfile }: StoreProps) {
  const { holds, refreshHolds, refreshWaitlist, refreshTickets } = useLive();
  const [view, setView] = useState<View>("assistant");
  const [panelOpen, setPanelOpen] = useState(false);

  // Cart lines are holds, so a cart change re-reads their deadlines too.
  const onEvent = useCallback(
    (event: AgentEvent) => {
      if (event.type !== "cart_update") return;
      onCartUpdate(event.data.cart as CartPayload);
      refreshHolds();
    },
    [onCartUpdate, refreshHolds],
  );

  // A reply can hold, release, join a waitlist, or transfer a pass; re-read all of it when it ends.
  const onTurnEnd = useCallback(() => {
    refreshHolds();
    refreshWaitlist();
    refreshTickets();
  }, [refreshHolds, refreshWaitlist, refreshTickets]);

  const chat = useAgentTurn(api, { ...session, unreachable: UNREACHABLE, onEvent, onTurnEnd, pendingComponent });

  const soonest = holds.length ? Math.min(...holds.map((hold) => hold.deadline)) : null;
  const held = cart?.item_count ?? 0;
  const shopper = session.shopper ?? { name: profile.name };

  return (
    <StoreShell
      brand={<Wordmark warm={holds.length > 0} />}
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
      bag={{ label: "Your night", count: held, noun: "held seat", extra: holds.length ? <HoldTimer deadline={soonest} /> : null }}
      panel={<NightPanel />}
      panelOpen={panelOpen}
      onPanelOpenChange={setPanelOpen}
      placeholder={view === "tickets" ? "Ask about a ticket, a transfer, a refund…" : "Ask about a show, a tier, a fee…"}
      banner={<OfferBanner />}
    >
      {/* The conversation stays mounted under the other view so its cards keep their state. */}
      <div className={view === "assistant" ? "h-full" : "hidden"}>
        <Chat chat={chat} home={<HomeView fanName={shopper.name} onSeeTickets={() => setView("tickets")} />} />
      </div>
      {view === "tickets" ? (
        <TicketsView />
      ) : null}
    </StoreShell>
  );
}
