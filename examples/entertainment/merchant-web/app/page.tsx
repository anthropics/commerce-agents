// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AssistantRail,
  Inspector,
  type PortalNavItem,
  PortalShell,
  type Prefill,
  useMerchantChat,
  useResource,
  useSession,
  useWebMcpTools,
} from "web-shared";
import AssistantPanel from "@/components/AssistantPanel";
import EventsView from "@/components/views/EventsView";
import HoldsView from "@/components/views/HoldsView";
import HomeView from "@/components/views/HomeView";
import { api, fetchOverview, UNREACHABLE, webMcpTools } from "@/lib/api";
import type { StagedChange } from "@/lib/types";

type PortalView = "home" | "events" | "holds";

function StoreMark() {
  return (
    <span
      aria-hidden
      className="at-display relative grid h-[34px] w-[34px] shrink-0 place-items-center rounded-[8px] bg-(--well) text-[19px] text-(--ink) shadow-[inset_0_0_0_1px_var(--line-strong)]"
    >
      A
      <span className="at-live absolute -right-0.5 -top-0.5" />
    </span>
  );
}

export default function PortalPage() {
  const session = useSession(api);
  useWebMcpTools(Boolean(session.sessionId), webMcpTools);
  const [view, setView] = useState<PortalView>("home");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  // Bumped whenever a staged change moves, so every widget re-reads the store the agent wrote.
  const [refreshKey, setRefreshKey] = useState(0);
  const refreshPortal = useCallback(() => setRefreshKey((value) => value + 1), []);

  const chat = useMerchantChat<StagedChange>(api, {
    ...session,
    unreachable: UNREACHABLE,
    onPortalRefresh: refreshPortal,
  });

  // The overview feeds the home page and the sidebar counts, so it loads here.
  const { data: overview, failed: overviewFailed } = useResource(session.sessionId ? fetchOverview : null, [session.sessionId, refreshKey]);

  // The rail is part of the default layout on wide screens; narrow screens open it on demand.
  useEffect(() => {
    setAssistantOpen(window.innerWidth >= 1024);
  }, []);

  const askAssistant = useCallback((text: string) => {
    setAssistantOpen(true);
    setPrefill({ text, nonce: Date.now() });
  }, []);

  const nav = useMemo<PortalNavItem<PortalView>[]>(() => {
    const alerts = overview?.snapshot.alerts;
    return [
      { id: "home", label: "Home", icon: "home" },
      {
        id: "events",
        label: "Events",
        icon: "ticket",
        count: alerts ? (alerts.low_stock ?? 0) + (alerts.slow_movers ?? 0) : null,
      },
      { id: "holds", label: "Holds", icon: "clock", count: alerts?.pending_changes || null },
    ];
  }, [overview]);

  return (
    <>
      <PortalShell
        brand={{ mark: <StoreMark />, name: "ACME Tickets", detail: "Box-office workspace" }}
        nav={nav}
        view={view}
        onViewChange={setView}
        operator={{ name: session.operator ?? "Operator", role: "Box-office manager" }}
        assistantOpen={assistantOpen}
        assistantBusy={chat.busy}
        onToggleAssistant={() => setAssistantOpen((open) => !open)}
        rail={
          <AssistantRail
            open={assistantOpen}
            storageKey="acme-tickets-boxoffice-panel-width"
            onClose={() => setAssistantOpen(false)}
          >
            {(rail) => (
              <AssistantPanel
                chat={chat}
                prefill={prefill}
                onPrefill={askAssistant}
                newMemoryCount={chat.newMemoryKeys.size}
                onOpenActivity={() => setActivityOpen(true)}
                {...rail}
              />
            )}
          </AssistantRail>
        }
      >
        {session.sessionId ? (
          <>
            {view === "home" ? (
              <HomeView
                data={overview}
                failed={overviewFailed && session.sessionId != null}
                operator={session.operator}
                onAskAssistant={askAssistant}
                onNavigate={setView}
              />
            ) : null}
            {view === "events" ? <EventsView refreshKey={refreshKey} onAskAssistant={askAssistant} /> : null}
            {view === "holds" ? <HoldsView refreshKey={refreshKey} onAskAssistant={askAssistant} /> : null}
          </>
        ) : null}
      </PortalShell>
      {activityOpen ? (
        <Inspector
          turnCount={chat.turnCount}
          streaming={chat.streaming}
          trace={chat.trace}
          memory={chat.memory}
          newMemoryKeys={chat.newMemoryKeys}
          memoryTitle="Business memory"
          onClose={() => setActivityOpen(false)}
        />
      ) : null}
    </>
  );
}
