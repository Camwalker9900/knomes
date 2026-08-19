import { formatNumber } from "@/lib/format";
import type { ConditionSummaryCounts } from "@/lib/types";

const TILES: { key: keyof ConditionSummaryCounts; label: string }[] = [
  { key: "open_findings", label: "Open findings" },
  { key: "resolved_findings", label: "Resolved findings" },
  { key: "disputed_findings", label: "Disputed findings" },
  { key: "verified_inspections", label: "Verified inspections" },
  { key: "prior_transactions", label: "Prior transactions" },
];

export default function ConditionSummary({ summary }: { summary: ConditionSummaryCounts }) {
  return (
    <section aria-labelledby="condition-summary-heading">
      <h2 id="condition-summary-heading" className="font-display text-2xl font-bold text-navy">
        Condition summary
      </h2>
      <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {TILES.map(({ key, label }) => (
          <div
            key={key}
            className="rounded-md border border-navy/10 bg-white px-4 py-3 shadow-sm"
          >
            <dd className="font-display text-3xl font-bold text-navy">
              {formatNumber(summary[key])}
            </dd>
            <dt className="mt-1 text-xs tracking-wide text-ink/60">{label}</dt>
          </div>
        ))}
      </dl>
    </section>
  );
}
