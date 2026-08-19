import ProvenanceBadge from "@/components/ProvenanceBadge";
import { formatConfidence, formatDate } from "@/lib/format";
import type { TimelineEvent } from "@/lib/types";

export default function EventCard({ event }: { event: TimelineEvent }) {
  return (
    <article className="rounded-md border border-navy/10 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <time
            dateTime={event.event_date}
            className="font-mono text-xs tracking-wide text-ink/60"
          >
            {formatDate(event.event_date)}
          </time>
          <h4 className="mt-0.5 font-display text-base font-bold text-navy">{event.title}</h4>
        </div>
        <ProvenanceBadge level={event.verification_level} />
      </div>
      {event.summary ? (
        <p className="mt-2 text-sm leading-relaxed text-ink/80">{event.summary}</p>
      ) : null}
      {event.confidence !== null || event.provenance ? (
        <p className="mt-2 font-mono text-[11px] text-ink/50">
          {event.provenance
            ? `Source: ${event.provenance.source_name} · ${event.provenance.record_type}`
            : null}
          {event.provenance && event.confidence !== null ? " · " : null}
          {event.confidence !== null ? `Confidence ${formatConfidence(event.confidence)}` : null}
        </p>
      ) : null}
    </article>
  );
}
