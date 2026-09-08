// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { AgentApi, createStorefrontWebMcpTools } from "web-shared";
import type { CartPayload, Hold, Product, ReturnOffer, WaitlistEntry, WalletTicket } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8003";

export const api = new AgentApi(API_URL, "/api");

export const UNREACHABLE =
  "Couldn't reach the entertainment API on port 8003. Start it with " +
  "`uvicorn entertainment.api.main:app --app-dir examples --port 8003` and try again.";

/** The cart is a view of the session's unexpired holds. */
export async function fetchProducts(): Promise<Product[] | null> {
  const data = await api.get<{ products?: Product[] }>("/products", { limit: "100" });
  return data?.products ?? null;
}

/** Places a timed hold. */
export async function addToCart(productId: string, quantity = 1): Promise<CartPayload | null> {
  const data = await api.post<{ cart?: CartPayload }>("/cart/add", {
    product_id: productId,
    quantity,
  });
  return data?.cart ?? null;
}

export interface HoldsRead {
  holds: Hold[];
  /** The venue's hold and return-offer windows, from the ticketing engine. */
  hold_minutes?: number;
  offer_window_minutes?: number;
}

export async function fetchHolds(): Promise<HoldsRead | null> {
  const data = await api.get<Partial<HoldsRead>>("/holds");
  return data ? { ...data, holds: data.holds ?? [] } : null;
}

export function releaseHold(holdId: string): Promise<{ holds?: Hold[] } | null> {
  return api.post<{ holds?: Hold[] }>("/holds/release", { hold_id: holdId });
}

export async function joinWaitlist(productId: string, quantity = 1): Promise<number | null> {
  const data = await api.post<{ position?: unknown }>("/waitlist/join", {
    product_id: productId,
    quantity,
  });
  return typeof data?.position === "number" ? data.position : null;
}

export interface WaitlistPayload {
  entries: WaitlistEntry[];
  offers: ReturnOffer[];
}

export async function fetchWaitlist(): Promise<WaitlistPayload | null> {
  const data = await api.get<Partial<WaitlistPayload>>("/waitlist");
  return data ? { entries: data.entries ?? [], offers: data.offers ?? [] } : null;
}

/** The server converts the offer into a timed hold. */
export function claimOffer(offerId: string): Promise<{ holds?: Hold[] } | null> {
  return api.post<{ holds?: Hold[] }>("/waitlist/claim", { offer_id: offerId });
}

/** Entry codes rotate server-side. */
export async function fetchTickets(): Promise<WalletTicket[] | null> {
  const data = await api.get<{ tickets?: WalletTicket[] }>("/tickets");
  return data ? (data.tickets ?? []) : null;
}

export const webMcpTools = createStorefrontWebMcpTools({ fetchProducts, fetchOrders: () => api.fetchOrders() });
