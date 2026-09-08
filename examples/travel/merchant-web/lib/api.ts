// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { AgentApi, createMerchantWebMcpTools } from "web-shared";
import type {
  AlertsResponse,
  ListingDetailResponse,
  ListingsResponse,
  OccupancyOverviewResponse,
  OverviewResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

export const api = new AgentApi(API_URL, "/api/merchant");

export const UNREACHABLE =
  "Couldn't reach the travel API on port 8001. Start it with " +
  "`uvicorn travel.api.main:app --app-dir examples --port 8001` and try again.";

export function fetchOverview(): Promise<OverviewResponse | null> {
  return api.get<OverviewResponse>("/overview");
}

export function fetchOccupancy(): Promise<OccupancyOverviewResponse | null> {
  return api.get<OccupancyOverviewResponse>("/occupancy");
}

export function fetchListings(query?: string): Promise<ListingsResponse | null> {
  return api.get<ListingsResponse>("/listings", query ? { query } : undefined);
}

export function fetchListingDetail(listingId: string): Promise<ListingDetailResponse | null> {
  return api.get<ListingDetailResponse>(`/listings/${encodeURIComponent(listingId)}`);
}

export function fetchAlerts(): Promise<AlertsResponse | null> {
  return api.get<AlertsResponse>("/alerts");
}

export const webMcpTools = createMerchantWebMcpTools({
  fetchOverview,
  fetchListings,
  fetchListingDetail,
  extraReads: [{ name: "get_occupancy", description: "Read the current property occupancy window without changing it.", read: fetchOccupancy }],
});
