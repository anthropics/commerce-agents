// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { AgentApi, createMerchantWebMcpTools } from "web-shared";
import type {
  BaseOverviewResponse,
  ListingDetailResponse,
  ListingsResponse,
  OverviewResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";

export const api = new AgentApi(API_URL, "/api/merchant");

export const UNREACHABLE =
  "Couldn't reach the telecom API on port 8002. Start it with " +
  "`uvicorn telecom.api.main:app --app-dir examples --port 8002` and try again.";

export function fetchOverview(): Promise<OverviewResponse | null> {
  return api.get<OverviewResponse>("/overview");
}

export function fetchBase(): Promise<BaseOverviewResponse | null> {
  return api.get<BaseOverviewResponse>("/base");
}

export function fetchListings(query?: string): Promise<ListingsResponse | null> {
  return api.get<ListingsResponse>("/listings", query ? { query } : undefined);
}

export function fetchListingDetail(listingId: string): Promise<ListingDetailResponse | null> {
  return api.get<ListingDetailResponse>(`/listings/${encodeURIComponent(listingId)}`);
}

export const webMcpTools = createMerchantWebMcpTools({
  fetchOverview,
  fetchListings,
  fetchListingDetail,
  extraReads: [{ name: "get_subscriber_base", description: "Read the current subscriber-base overview without changing it.", read: fetchBase }],
});
