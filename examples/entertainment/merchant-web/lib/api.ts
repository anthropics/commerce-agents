// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { AgentApi, createMerchantWebMcpTools } from "web-shared";
import type { ListingDetailResponse, OverviewResponse, PacingOverviewResponse } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8003";

export const api = new AgentApi(API_URL, "/api/merchant");

export const UNREACHABLE =
  "Couldn't reach the entertainment API on port 8003. Start it with " +
  "`uvicorn entertainment.api.main:app --app-dir examples --port 8003` and try again.";

export function fetchOverview(): Promise<OverviewResponse | null> {
  return api.get<OverviewResponse>("/overview");
}

export function fetchPacing(): Promise<PacingOverviewResponse | null> {
  return api.get<PacingOverviewResponse>("/pacing");
}

export function fetchListingDetail(listingId: string): Promise<ListingDetailResponse | null> {
  return api.get<ListingDetailResponse>(`/listings/${encodeURIComponent(listingId)}`);
}

export const webMcpTools = createMerchantWebMcpTools({
  fetchOverview,
  fetchListingDetail,
  extraReads: [{ name: "get_event_pacing", description: "Read current event sales pacing without changing it.", read: fetchPacing }],
});
