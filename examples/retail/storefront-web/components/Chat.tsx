// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import type { ReactNode } from "react";
import { ActivityLine, type AgentTurn, type AssistantChatItem, Chat as ChatShell } from "web-shared";
import { addToCart } from "@/lib/api";
import type { CartPayload } from "@/lib/types";
import GenerativeBlock from "./generative";

const WIDE = new Set(["comparison", "plan"]);
const CART_ENABLED = process.env.NEXT_PUBLIC_CART_ENABLED !== "false";

/** Shimmers where the carousel will land while a search runs. */
function Pending({ item }: { item: AssistantChatItem }) {
  const searching = item.tools.includes("search_products") && !item.segments.some((s) => s.type === "ui");
  if (!searching) return <ActivityLine item={item} />;
  return (
    <section role="status" className="rounded-2xl border border-(--line) bg-(--card) p-3 shadow-(--shadow-sm)">
      <div className="mb-3 animate-pulse text-[15px] text-(--ink-soft)">{item.activity ?? "Searching the catalog…"}</div>
      <div className="flex gap-3 overflow-hidden pb-1">
        {[0, 1, 2, 3].map((slot) => (
          <div key={slot} className="ac-skeleton h-[150px] w-48 shrink-0 rounded-xl" />
        ))}
      </div>
    </section>
  );
}

export default function Chat({ chat, home, onCartUpdate }: { chat: AgentTurn; home: ReactNode; onCartUpdate: (cart: CartPayload) => void }) {
  return (
    <ChatShell
      chat={chat}
      home={home}
      wide={WIDE}
      renderPending={(item) => <Pending item={item} />}
      renderBlock={(segment) => (
        <GenerativeBlock
          block={segment.block}
          status={segment.status}
          onAdd={
            CART_ENABLED
              ? async (product) => {
                  const cart = await addToCart(product.product_id);
                  if (cart) onCartUpdate(cart);
                  return cart !== null;
                }
              : undefined
          }
        />
      )}
    />
  );
}
