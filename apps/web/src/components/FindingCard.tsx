import ProvenanceBadge from "@/components/ProvenanceBadge";
import { formatDate, humanizeEnum } from "@/lib/format";
import type { FindingDetail, FindingSeverity, FindingStatus } from "@/lib/types";

const STATUS_CLASSES: Record<FindingStatus, string> = {
  OPEN: "border-amber-600/50 bg-amber-500/10 text-amber-800",
  RESOLUTION_REPORTED: "border-steel/40 bg-steel/10 text-steel",
  RESOLUTION_EVIDENCE_FOUND: "border-steel/40 bg-steel/10 text-steel",
  RESOLVED: "border-emerald-700/40 bg-emerald-700/10 text-emerald-800",
  DISPUTED: "border-red-700/40 bg-red-700/10 text-red-800",
  SUPERSEDED: "border-gray-300 bg-gray-100 text-gray-600",
  UNKNOWN: "border-gray-300 bg-gray-100 text-gray-600",
};

const SEVERITY_CLASSES: Record<FindingSeverity, string> = {
  MINOR: "border-gray-300 bg-gray-100 text-gray-600",
  MODERATE: "border-amber-600/50 bg-amber-500/10 text-amber-800",
  MAJOR: "border-flame/50 bg-flame/10 text-flame",
  SAFETY: "border-red-700/50 bg-red-700/10 text-red-800",
};

function chip(classes: string, text: string) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[11px] leading-4 tracking-wide whitespace-nowrap ${classes}`}
    >
      {text}
    </span>
  );
}

export default function FindingCard({ finding }: { finding: FindingDetail }) {
  return (
    <article className="rounded-md border border-navy/10 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        {chip("border-navy/25 bg-navy/5 text-navy", humanizeEnum(finding.category))}
        {finding.subcategory
          ? chip("border-navy/15 bg-transparent text-ink/60", finding.subcategory)
          : null}
        {finding.severity
          ? chip(SEVERITY_CLASSES[finding.severity], humanizeEnum(finding.severity))
          : null}
        {chip(STATUS_CLASSES[finding.status], humanizeEnum(finding.status))}
      </div>
      <h3 className="mt-3 font-display text-lg font-bold text-navy">{finding.title}</h3>
      {finding.description ? (
        <p className="mt-2 text-sm leading-relaxed text-ink/80">{finding.description}</p>
      ) : null}
      {finding.first_observed_at || finding.latest_observed_at ? (
        <p className="mt-2 font-mono text-[11px] text-ink/50">
          {finding.first_observed_at
            ? `First observed ${formatDate(finding.first_observed_at)}`
            : null}
          {finding.first_observed_at && finding.latest_observed_at ? " · " : null}
          {finding.latest_observed_at
            ? `Last observed ${formatDate(finding.latest_observed_at)}`
            : null}
        </p>
      ) : null}
      {finding.resolutions.length > 0 ? (
        <div className="mt-4 border-t border-navy/10 pt-3">
          <h4 className="text-xs font-semibold tracking-widest text-steel uppercase">
            Resolution history
          </h4>
          <ol className="mt-2 space-y-2">
            {finding.resolutions.map((resolution) => (
              <li
                key={resolution.id}
                className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-sm"
              >
                <span className="min-w-0">
                  <span className="font-medium text-ink">
                    {humanizeEnum(resolution.resolution_type)}
                  </span>
                  {resolution.description ? (
                    <span className="text-ink/70"> — {resolution.description}</span>
                  ) : null}
                  {resolution.resolved_at ? (
                    <span className="font-mono text-[11px] text-ink/50">
                      {" "}
                      · {formatDate(resolution.resolved_at)}
                    </span>
                  ) : null}
                </span>
                <ProvenanceBadge level={resolution.verification_level} />
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </article>
  );
}
