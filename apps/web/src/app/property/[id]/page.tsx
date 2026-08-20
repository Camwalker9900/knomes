import { notFound } from "next/navigation";
import ConditionSummary from "@/components/ConditionSummary";
import FindingCard from "@/components/FindingCard";
import SourcesFooter from "@/components/SourcesFooter";
import SubmitCTA from "@/components/SubmitCTA";
import Timeline from "@/components/Timeline";
import TransactionsSection from "@/components/TransactionsSection";
import {
  ApiError,
  getFindings,
  getProperty,
  getSources,
  getTimeline,
  getTransactions,
} from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type {
  FindingDetail,
  PropertyDetail,
  SourceProvenance,
  TimelineEvent,
  TransactionCycleOut,
} from "@/lib/types";

export const dynamic = "force-dynamic";

function metaLine(property: PropertyDetail): string {
  const segments: string[] = [];
  if (property.year_built !== null) {
    segments.push(`Built ${property.year_built}`);
  }
  if (property.year_remodeled !== null) {
    segments.push(`Remodeled ${property.year_remodeled}`);
  }
  if (property.bedrooms !== null) {
    segments.push(`${property.bedrooms} bd`);
  }
  if (property.bathrooms_full !== null) {
    // Half baths count as 0.5, e.g. 2 full + 1 half renders "2.5 ba".
    const baths = property.bathrooms_full + (property.bathrooms_half ?? 0) / 2;
    segments.push(`${baths} ba`);
  }
  if (property.building_sqft !== null) {
    segments.push(`${formatNumber(property.building_sqft)} sq ft`);
  }
  if (property.lot_sqft !== null) {
    segments.push(`${formatNumber(property.lot_sqft)} sq ft lot`);
  }
  if (property.hcad_account_id !== null) {
    segments.push(`HCAD account: ${property.hcad_account_id}`);
  }
  return segments.join(" · ");
}

export default async function PropertyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let property: PropertyDetail;
  let events: TimelineEvent[];
  let findings: FindingDetail[];
  let transactions: TransactionCycleOut[];
  let sources: SourceProvenance[];
  try {
    [property, events, findings, transactions, sources] = await Promise.all([
      getProperty(id),
      getTimeline(id),
      getFindings(id),
      getTransactions(id),
      getSources(id),
    ]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  }

  const addressLine = `${property.address_line1}${property.unit ? ` ${property.unit}` : ""}`;
  const meta = metaLine(property);

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-10 sm:px-6">
      <header>
        <h1 className="font-display text-3xl font-bold text-navy sm:text-4xl">{addressLine}</h1>
        <p className="mt-1 text-lg text-ink/70">
          {property.city}, {property.state} {property.postal_code}
        </p>
        {meta ? <p className="mt-2 font-mono text-sm text-ink/60">{meta}</p> : null}
        {property.property_type ? (
          <p className="mt-1 font-mono text-xs text-ink/50">{property.property_type}</p>
        ) : null}
      </header>

      <div className="mt-10 space-y-12">
        <ConditionSummary summary={property.condition_summary} />
        <Timeline events={events} />
        <section aria-labelledby="findings-heading">
          <h2 id="findings-heading" className="font-display text-2xl font-bold text-navy">
            Findings
          </h2>
          {findings.length === 0 ? (
            <p className="mt-4 text-sm text-ink/60">No findings recorded for this property.</p>
          ) : (
            <div className="mt-4 space-y-4">
              {findings.map((finding) => (
                <FindingCard key={finding.id} finding={finding} />
              ))}
            </div>
          )}
        </section>
        <TransactionsSection transactions={transactions} />
        <SubmitCTA />
        <SourcesFooter sources={sources} />
      </div>
    </div>
  );
}
