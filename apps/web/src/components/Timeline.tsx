"use client";

import { useMemo, useState } from "react";
import EventCard from "@/components/EventCard";
import { LEVEL_DOT_CLASSES } from "@/components/ProvenanceBadge";
import { yearOf } from "@/lib/format";
import type { EventType, TimelineEvent, TimelineFilter } from "@/lib/types";

export const TIMELINE_FILTERS: { value: TimelineFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "transactions", label: "Transactions" },
  { value: "inspections", label: "Inspections" },
  { value: "findings", label: "Findings" },
  { value: "repairs", label: "Repairs" },
  { value: "permits", label: "Permits" },
  { value: "code", label: "Code enforcement" },
  { value: "ownership", label: "Ownership" },
];

/** Filter → event-type mapping per plan/CONTRACTS.md (timeline endpoint). */
export function eventMatchesFilter(eventType: EventType, filter: TimelineFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "transactions":
      return eventType.startsWith("LISTING_") || eventType === "TRANSACTION_TERMINATED";
    case "inspections":
      return eventType === "INSPECTION_PERFORMED";
    case "findings":
      return eventType === "CONDITION_FINDING" || eventType.startsWith("FINDING_");
    case "repairs":
      return eventType.startsWith("REPAIR_");
    case "permits":
      return eventType.startsWith("PERMIT_");
    case "code":
      return eventType.startsWith("CODE_VIOLATION_");
    case "ownership":
      return (
        eventType === "PROPERTY_CREATED" ||
        eventType === "OWNERSHIP_TRANSFER" ||
        eventType === "DEED_RECORDED" ||
        eventType.startsWith("LIEN_")
      );
  }
}

function groupByYear(events: TimelineEvent[]): [number, TimelineEvent[]][] {
  const groups = new Map<number, TimelineEvent[]>();
  for (const event of events) {
    const year = yearOf(event.event_date);
    const group = groups.get(year);
    if (group) {
      group.push(event);
    } else {
      groups.set(year, [event]);
    }
  }
  return [...groups.entries()].sort(([a], [b]) => a - b);
}

export default function Timeline({ events }: { events: TimelineEvent[] }) {
  const [filter, setFilter] = useState<TimelineFilter>("all");

  const yearGroups = useMemo(() => {
    const sorted = [...events].sort(
      (a, b) => new Date(a.event_date).getTime() - new Date(b.event_date).getTime(),
    );
    return groupByYear(sorted.filter((event) => eventMatchesFilter(event.event_type, filter)));
  }, [events, filter]);

  return (
    <section aria-labelledby="timeline-heading">
      <h2 id="timeline-heading" className="font-display text-2xl font-bold text-navy">
        Timeline
      </h2>
      <div
        role="group"
        aria-label="Filter timeline"
        className="mt-4 flex flex-wrap gap-2"
      >
        {TIMELINE_FILTERS.map(({ value, label }) => {
          const selected = filter === value;
          return (
            <button
              key={value}
              type="button"
              aria-pressed={selected}
              onClick={() => setFilter(value)}
              className={`rounded-full border px-3 py-1 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-flame ${
                selected
                  ? "border-flame bg-flame text-white"
                  : "border-navy/20 bg-white text-ink/80 hover:border-flame hover:text-flame"
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>
      {yearGroups.length === 0 ? (
        <p className="mt-6 text-sm text-ink/60">No events recorded for this filter.</p>
      ) : (
        <div className="mt-6 space-y-8">
          {yearGroups.map(([year, yearEvents]) => (
            <div key={year}>
              <h3 className="font-mono text-sm font-semibold tracking-widest text-steel">
                {year}
              </h3>
              <ol className="mt-3 border-l-2 border-navy/10 pl-6 sm:ml-1">
                {yearEvents.map((event) => (
                  <li key={event.id} className="relative pb-5 last:pb-0">
                    <span
                      aria-hidden="true"
                      className={`absolute top-4 -left-[31px] h-3 w-3 rounded-full ring-4 ring-paper ${LEVEL_DOT_CLASSES[event.verification_level]}`}
                    />
                    <EventCard event={event} />
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
