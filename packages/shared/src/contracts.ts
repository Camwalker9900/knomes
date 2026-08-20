/**
 * Knomes shared API contracts (TypeScript).
 *
 * Single source of truth for the JSON shapes exchanged between the FastAPI
 * backend (apps/api/app/schemas/) and the web client (apps/web/src/lib/types.ts).
 * For now this file is kept in sync MANUALLY with apps/web/src/lib/types.ts and
 * the Pydantic response schemas — update all copies together whenever the
 * contract in plan/CONTRACTS.md changes.
 */

export type VerificationLevel =
  | "GOVERNMENT_RECORD"
  | "LICENSED_PROFESSIONAL"
  | "TRANSACTION_DOCUMENT"
  | "DOCUMENT_SUPPORTED"
  | "PARTICIPANT_REPORTED"
  | "SYSTEM_INFERRED"
  | "UNVERIFIED";

export type EventType =
  | "PROPERTY_CREATED"
  | "OWNERSHIP_TRANSFER"
  | "DEED_RECORDED"
  | "LIEN_RECORDED"
  | "LIEN_RELEASED"
  | "PERMIT_APPLIED"
  | "PERMIT_ISSUED"
  | "PERMIT_INSPECTION"
  | "PERMIT_FINALIZED"
  | "CODE_VIOLATION_OPENED"
  | "CODE_VIOLATION_ACTION"
  | "CODE_VIOLATION_RESOLVED"
  | "LISTING_CREATED"
  | "LISTING_PRICE_CHANGED"
  | "LISTING_ACTIVE"
  | "LISTING_UNDER_CONTRACT"
  | "LISTING_PENDING"
  | "LISTING_BACK_ON_MARKET"
  | "LISTING_CLOSED"
  | "LISTING_WITHDRAWN"
  | "INSPECTION_PERFORMED"
  | "CONDITION_FINDING"
  | "REPAIR_RECOMMENDED"
  | "TRANSACTION_TERMINATED"
  | "REPAIR_REPORTED"
  | "REPAIR_VERIFIED"
  | "FINDING_DISPUTED"
  | "FINDING_RESOLVED"
  | "FINDING_REOPENED";

export type FindingCategory =
  | "ROOF"
  | "HVAC"
  | "ELECTRICAL"
  | "PLUMBING"
  | "SEWER"
  | "FOUNDATION"
  | "STRUCTURAL"
  | "WATER_INTRUSION"
  | "DRAINAGE"
  | "MOLD"
  | "PEST"
  | "POOL"
  | "APPLIANCE"
  | "WINDOWS"
  | "EXTERIOR"
  | "INTERIOR"
  | "SAFETY"
  | "OTHER";

export type FindingStatus =
  | "OPEN"
  | "RESOLUTION_REPORTED"
  | "RESOLUTION_EVIDENCE_FOUND"
  | "RESOLVED"
  | "DISPUTED"
  | "SUPERSEDED"
  | "UNKNOWN";

export type FindingSeverity = "MINOR" | "MODERATE" | "MAJOR" | "SAFETY";

export type ResolutionType =
  | "REPAIR"
  | "REPLACEMENT"
  | "PROFESSIONAL_REEVALUATION"
  | "PERMIT_FINALIZATION"
  | "SELLER_DOCUMENTATION"
  | "INSPECTION_CLEARANCE"
  | "OTHER";

export type TransactionOutcome = "UNKNOWN" | "CLOSED" | "TERMINATED" | "WITHDRAWN" | "EXPIRED";

export type TerminationReason =
  | "UNKNOWN"
  | "PROPERTY_CONDITION"
  | "FINANCING"
  | "APPRAISAL"
  | "TITLE"
  | "INSURANCE"
  | "BUYER_DECISION"
  | "SELLER_DECISION"
  | "CONTINGENCY"
  | "OTHER";

export type SearchMatchType = "EXACT_ADDRESS" | "HCAD_ID" | "FUZZY_ADDRESS";

export type TimelineFilter =
  | "all"
  | "transactions"
  | "inspections"
  | "findings"
  | "repairs"
  | "permits"
  | "code"
  | "ownership";

export interface PropertySearchResult {
  id: string;
  address_line1: string;
  unit: string | null;
  city: string;
  state: string;
  postal_code: string;
  hcad_account_id: string | null;
  match_type: SearchMatchType;
}

export interface ConditionSummaryCounts {
  open_findings: number;
  resolved_findings: number;
  disputed_findings: number;
  verified_inspections: number;
  prior_transactions: number;
}

export interface SourceFreshness {
  source: string;
  last_refreshed: string | null;
}

export interface PropertyDetail {
  id: string;
  address_line1: string;
  unit: string | null;
  city: string;
  state: string;
  postal_code: string;
  hcad_account_id: string | null;
  year_built: number | null;
  building_sqft: number | null;
  lot_sqft: number | null;
  property_type: string | null;
  latitude: number | null;
  longitude: number | null;
  bedrooms: number | null;
  bathrooms_full: number | null;
  bathrooms_half: number | null;
  quality_code: string | null;
  year_remodeled: number | null;
  condition_summary: ConditionSummaryCounts;
  freshness: SourceFreshness[];
}

export interface EventProvenance {
  source_name: string;
  record_type: string;
}

export interface TimelineEvent {
  id: string;
  event_type: EventType;
  event_date: string;
  title: string;
  summary: string | null;
  verification_level: VerificationLevel;
  confidence: number | null;
  provenance: EventProvenance | null;
}

export interface FindingResolutionOut {
  id: string;
  resolution_type: ResolutionType;
  description: string | null;
  verification_level: VerificationLevel;
  resolved_at: string | null;
  event_id: string | null;
}

export interface FindingDetail {
  id: string;
  category: FindingCategory;
  subcategory: string | null;
  title: string;
  description: string | null;
  severity: FindingSeverity | null;
  status: FindingStatus;
  first_observed_at: string | null;
  latest_observed_at: string | null;
  resolutions: FindingResolutionOut[];
}

export interface TransactionCycleOut {
  id: string;
  started_at: string | null;
  under_contract_at: string | null;
  terminated_at: string | null;
  closed_at: string | null;
  outcome: TransactionOutcome;
  termination_reason: TerminationReason;
  reason_verification_level: VerificationLevel;
}

export interface SourceProvenance {
  source: string;
  record_count: number;
  last_refreshed: string | null;
}

export interface SearchResponse {
  results: PropertySearchResult[];
}

export interface TimelineResponse {
  events: TimelineEvent[];
}

export interface FindingsResponse {
  findings: FindingDetail[];
}

export interface TransactionsResponse {
  transactions: TransactionCycleOut[];
}

export interface SourcesResponse {
  sources: SourceProvenance[];
}
