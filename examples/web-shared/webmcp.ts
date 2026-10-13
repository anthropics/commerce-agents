// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect } from "react";

export interface WebMcpTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  annotations: {
    readOnlyHint: boolean;
    untrustedContentHint?: boolean;
  };
  execute(input: unknown): Promise<unknown>;
}

interface ModelContext {
  registerTool(tool: WebMcpTool): Promise<unknown>;
}

declare global {
  interface Navigator {
    modelContext?: ModelContext;
  }

  interface Document {
    modelContext?: ModelContext;
  }
}

/** Registers a page's tools once its authenticated/demo session is ready. */
export function useWebMcpTools(ready: boolean, tools: readonly WebMcpTool[]): void {
  useEffect(() => {
    if (!ready) return;
    const modelContext = navigator.modelContext || document.modelContext;
    if (!modelContext?.registerTool) return;
    void Promise.all(tools.map((tool) => modelContext.registerTool(tool))).catch(() => {
      // Unsupported or shutting-down browser contexts should not break the site.
    });
  }, [ready, tools]);
}

export function strictObject(input: unknown, allowed: readonly string[]): Record<string, unknown> {
  if (typeof input !== "object" || input === null || Array.isArray(input)) throw new Error("Expected an object");
  const value = input as Record<string, unknown>;
  if (Object.keys(value).some((key) => !allowed.includes(key))) throw new Error("Unknown field");
  return value;
}

export function optionalString(value: unknown, name: string): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "string" || value.length > 120) throw new Error(`Invalid ${name}`);
  return value;
}

export function requiredString(value: unknown, name: string): string {
  const parsed = optionalString(value, name);
  if (!parsed?.trim()) throw new Error(`Invalid ${name}`);
  return parsed;
}

export function optionalInteger(value: unknown, name: string, fallback: number, min: number, max: number): number {
  if (value === undefined) return fallback;
  if (!Number.isInteger(value) || (value as number) < min || (value as number) > max) throw new Error(`Invalid ${name}`);
  return value as number;
}

export const emptyObjectSchema = {
  type: "object",
  properties: {},
  additionalProperties: false,
} as const;

type ProductLike = {
  product_id: string;
  title: string;
  price: number;
  currency?: string;
  category?: string | null;
  in_stock?: boolean;
  short_description?: string | null;
};

export function createStorefrontWebMcpTools(options: {
  fetchProducts(): Promise<ProductLike[] | null>;
  fetchOrders(): Promise<unknown[] | null>;
}): readonly WebMcpTool[] {
  const tools: WebMcpTool[] = [
    {
      name: "search_catalog",
      description: "Search the products visible in this storefront without changing state.",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string", maxLength: 120 },
          limit: { type: "integer", minimum: 1, maximum: 20 },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute: async (input) => {
        const value = strictObject(input, ["query", "limit"]);
        const query = optionalString(value.query, "query")?.trim().toLowerCase() ?? "";
        const limit = optionalInteger(value.limit, "limit", 10, 1, 20);
        const products = await options.fetchProducts();
        if (!products) throw new Error("Catalog unavailable");
        const matches = query
          ? products.filter((product) => `${product.title} ${product.category ?? ""} ${product.short_description ?? ""}`.toLowerCase().includes(query))
          : products;
        return {
          total: matches.length,
          products: matches.slice(0, limit).map(({ product_id, title, price, currency, category, in_stock }) => ({
            product_id,
            title,
            price,
            currency,
            category,
            in_stock,
          })),
        };
      },
    },
    {
      name: "get_orders",
      description: "Read the signed-in shopper's orders without changing them.",
      inputSchema: emptyObjectSchema,
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute: async (input) => {
        strictObject(input, []);
        const orders = await options.fetchOrders();
        if (!orders) throw new Error("Orders unavailable");
        return { orders };
      },
    },
  ];

  return tools;
}

export function createMerchantWebMcpTools(options: {
  fetchOverview(): Promise<unknown | null>;
  fetchListings?: (query?: string) => Promise<{ total?: number; listings: unknown[] } | null>;
  fetchListingDetail(listingId: string): Promise<unknown | null>;
  extraReads?: readonly { name: string; description: string; read(): Promise<unknown | null> }[];
}): readonly WebMcpTool[] {
  const readTool = (name: string, description: string, read: () => Promise<unknown | null>): WebMcpTool => ({
    name,
    description,
    inputSchema: emptyObjectSchema,
    annotations: { readOnlyHint: true, untrustedContentHint: true },
    execute: async (input) => {
      strictObject(input, []);
      const result = await read();
      if (result === null) throw new Error("Data unavailable");
      return result;
    },
  });

  const tools: WebMcpTool[] = [
    readTool("get_business_overview", "Read the current merchant dashboard overview without changing business state.", options.fetchOverview),
    {
      name: "get_listing",
      description: "Read one merchant listing and its pricing context without changing it.",
      inputSchema: {
        type: "object",
        properties: { listing_id: { type: "string", minLength: 1, maxLength: 120 } },
        required: ["listing_id"],
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute: async (input) => {
        const value = strictObject(input, ["listing_id"]);
        const result = await options.fetchListingDetail(requiredString(value.listing_id, "listing_id"));
        if (!result) throw new Error("Listing unavailable");
        return result;
      },
    },
  ];

  if (options.fetchListings) {
    tools.push({
      name: "search_listings",
      description: "Search listings visible in the merchant catalog without changing them.",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string", maxLength: 120 },
          limit: { type: "integer", minimum: 1, maximum: 20 },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute: async (input) => {
        const value = strictObject(input, ["query", "limit"]);
        const query = optionalString(value.query, "query")?.trim();
        const limit = optionalInteger(value.limit, "limit", 10, 1, 20);
        const result = await options.fetchListings!(query || undefined);
        if (!result) throw new Error("Listings unavailable");
        return { total: result.total ?? result.listings.length, listings: result.listings.slice(0, limit) };
      },
    });
  }

  for (const extra of options.extraReads ?? []) tools.push(readTool(extra.name, extra.description, extra.read));
  return tools;
}
