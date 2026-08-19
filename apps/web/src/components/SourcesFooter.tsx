import { formatDate, formatNumber } from "@/lib/format";
import type { SourceProvenance } from "@/lib/types";

export default function SourcesFooter({ sources }: { sources: SourceProvenance[] }) {
  return (
    <section
      aria-labelledby="sources-heading"
      className="rounded-md border border-navy/10 bg-white p-5 shadow-sm"
    >
      <h2
        id="sources-heading"
        className="text-sm font-semibold tracking-widest text-steel uppercase"
      >
        Data sources
      </h2>
      {sources.length === 0 ? (
        <p className="mt-3 text-sm text-ink/60">No source records attached to this property yet.</p>
      ) : (
        <ul className="mt-3 divide-y divide-navy/10">
          {sources.map((source) => (
            <li
              key={source.source}
              className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2 text-sm"
            >
              <span className="font-mono text-ink">{source.source}</span>
              <span className="font-mono text-[11px] text-ink/60">
                {formatNumber(source.record_count)}{" "}
                {source.record_count === 1 ? "record" : "records"} ·{" "}
                {source.last_refreshed
                  ? `last refreshed ${formatDate(source.last_refreshed)}`
                  : "not yet refreshed"}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-ink/50">
        Public-record data refreshes periodically — freshness above is per source.
      </p>
    </section>
  );
}
