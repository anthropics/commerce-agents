// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { AgentApi, createStorefrontWebMcpTools } from "web-shared";
import type { AccountContext, CartPayload, Product } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";

export const api = new AgentApi(API_URL, "/api");

export const UNREACHABLE =
  "Couldn't reach the telecom API on port 8002. Start it with " +
  "`uvicorn telecom.api.main:app --app-dir examples --port 8002` and try again.";

export async function fetchProducts(): Promise<Product[] | null> {
  const data = await api.get<{ products: Product[] }>("/products", { limit: "100" });
  return data?.products ?? null;
}

/** `{ account: null }` for a prospect; null when the read failed. */
export function fetchAccount(): Promise<{ account: AccountContext | null } | null> {
  return api.get<{ account: AccountContext | null }>("/account");
}

/** The server refuses plans and lines. */
export async function addToCart(productId: string, quantity = 1): Promise<CartPayload | null> {
  const body = { product_id: productId, quantity };
  const data = await api.post<{ cart: CartPayload }>("/cart/add", body);
  return data?.cart ?? null;
}

export const webMcpTools = createStorefrontWebMcpTools({ fetchProducts, fetchOrders: () => api.fetchOrders() });
