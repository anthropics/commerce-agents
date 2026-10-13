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
import BookingsView from "@/components/views/BookingsView";
import HomeView from "@/components/views/HomeView";
import PropertiesView from "@/components/views/PropertiesView";
import { api, fetchOccupancy, fetchOverview, UNREACHABLE, webMcpTools } from "@/lib/api";
import type { StagedChange } from "@/lib/types";

type PortalView = "home" | "properties" | "bookings";

function StoreMark() {
  return (
    <span
      aria-hidden
      className="al-display grid h-[34px] w-[34px] shrink-0 place-items-center rounded-[10px] bg-(--ink) text-[17px] font-[650] italic text-(--accent) shadow-[inset_0_-3px_0_rgba(0,0,0,0.18)]"
    >
      ◈
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
  const { data: occupancy } = useResource(session.sessionId ? fetchOccupancy : null, [session.sessionId, refreshKey]);

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
      { id: "properties", label: "Properties", icon: "bed" },
      {
        id: "bookings",
        label: "Bookings",
        icon: "calendar",
        attention: alerts?.order_issues || null,
        count: alerts ? (alerts.low_stock ?? 0) + (alerts.slow_movers ?? 0) : null,
      },
    ];
  }, [overview]);

  return (
    <>
      <PortalShell
        brand={{ mark: <StoreMark />, name: "ACME Travel", detail: "Supplier workspace" }}
        nav={nav}
        view={view}
        onViewChange={setView}
        operator={{ name: session.operator ?? "Operator", role: "Revenue manager" }}
        assistantOpen={assistantOpen}
        assistantBusy={chat.busy}
        onToggleAssistant={() => setAssistantOpen((open) => !open)}
        rail={
          <AssistantRail
            open={assistantOpen}
            storageKey="acme-travel-supplier-panel-width"
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
                failed={overviewFailed}
                occupancy={occupancy}
                operator={session.operator}
                onAskAssistant={askAssistant}
                onNavigate={setView}
              />
            ) : null}
            {view === "properties" ? <PropertiesView refreshKey={refreshKey} onAskAssistant={askAssistant} /> : null}
            {view === "bookings" ? (
              <BookingsView refreshKey={refreshKey} recentBookings={overview?.recent_orders ?? (overviewFailed ? [] : null)} onAskAssistant={askAssistant} />
            ) : null}
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
