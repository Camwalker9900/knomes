/**
 * Typed client for the Knomes API v1 (see plan/CONTRACTS.md, "API v1").
 * Base URL comes from NEXT_PUBLIC_API_URL (default http://localhost:8000).
 */

import type {
  FindingDetail,
  FindingsResponse,
  PropertyDetail,
  PropertySearchResult,
  SearchResponse,
  SourceProvenance,
  SourcesResponse,
  TimelineEvent,
  TimelineFilter,
  TimelineResponse,
  TransactionCycleOut,
  TransactionsResponse,
} from "@/lib/types";

// Server-side (SSR in docker) reaches the api service directly; the browser uses the public URL.
const API_BASE_URL =
  (typeof window === "undefined" && process.env.API_URL_INTERNAL) ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new ApiError(response.status, `GET ${path} failed with status ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function searchProperties(q: string): Promise<PropertySearchResult[]> {
  const data = await apiGet<SearchResponse>(
    `/api/v1/properties/search?q=${encodeURIComponent(q)}`,
  );
  return data.results;
}

export async function getProperty(id: string): Promise<PropertyDetail> {
  return apiGet<PropertyDetail>(`/api/v1/properties/${encodeURIComponent(id)}`);
}

export async function getTimeline(id: string, filter?: TimelineFilter): Promise<TimelineEvent[]> {
  const query = filter ? `?filter=${encodeURIComponent(filter)}` : "";
  const data = await apiGet<TimelineResponse>(
    `/api/v1/properties/${encodeURIComponent(id)}/timeline${query}`,
  );
  return data.events;
}

export async function getFindings(id: string): Promise<FindingDetail[]> {
  const data = await apiGet<FindingsResponse>(
    `/api/v1/properties/${encodeURIComponent(id)}/findings`,
  );
  return data.findings;
}

export async function getTransactions(id: string): Promise<TransactionCycleOut[]> {
  const data = await apiGet<TransactionsResponse>(
    `/api/v1/properties/${encodeURIComponent(id)}/transactions`,
  );
  return data.transactions;
}

export async function getSources(id: string): Promise<SourceProvenance[]> {
  const data = await apiGet<SourcesResponse>(
    `/api/v1/properties/${encodeURIComponent(id)}/sources`,
  );
  return data.sources;
}
