/**
 * Formatting helpers. All date formatting is pinned to en-US + UTC so that
 * server-rendered and client-rendered output are identical (no hydration
 * drift, no timezone-dependent test flakiness).
 */

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const numberFormatter = new Intl.NumberFormat("en-US");

/** "2025-01-08T00:00:00Z" -> "Jan 8, 2025" */
export function formatDate(iso: string): string {
  return dateFormatter.format(new Date(iso));
}

/** UTC year of an ISO timestamp, for year-grouping the timeline. */
export function yearOf(iso: string): number {
  return new Date(iso).getUTCFullYear();
}

/** 1850 -> "1,850" */
export function formatNumber(value: number): string {
  return numberFormatter.format(value);
}

/** "RESOLUTION_EVIDENCE_FOUND" -> "Resolution evidence found" */
export function humanizeEnum(value: string): string {
  const lower = value.toLowerCase().replaceAll("_", " ");
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

/** 0.75 -> "75%" */
export function formatConfidence(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}
