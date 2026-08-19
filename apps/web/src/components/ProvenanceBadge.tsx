"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { VerificationLevel } from "@/lib/types";

/** Public labels for the seven verification levels (plan/CONTRACTS.md, "Web"). */
export const PROVENANCE_LABELS: Record<VerificationLevel, string> = {
  GOVERNMENT_RECORD: "Government Record",
  LICENSED_PROFESSIONAL: "Licensed Professional",
  TRANSACTION_DOCUMENT: "Transaction Document",
  DOCUMENT_SUPPORTED: "Document Supported",
  PARTICIPANT_REPORTED: "Participant Report",
  SYSTEM_INFERRED: "System Inference",
  UNVERIFIED: "Unverified",
};

/** Plain-language explanation shown in the click-to-open popover. */
export const PROVENANCE_EXPLANATIONS: Record<VerificationLevel, string> = {
  GOVERNMENT_RECORD: "Sourced directly from a government record.",
  LICENSED_PROFESSIONAL: "Reported by a licensed professional in the relevant trade.",
  TRANSACTION_DOCUMENT:
    "Backed by a document from the transaction, such as a contract or termination notice.",
  DOCUMENT_SUPPORTED: "Supported by an uploaded document that has been reviewed.",
  PARTICIPANT_REPORTED:
    "Reported by a participant in the transaction. Not independently verified.",
  SYSTEM_INFERRED: "Inferred automatically from patterns in the data. Not verified.",
  UNVERIFIED: "Not verified against any record or document.",
};

/** Chip styling per level: small bordered chip with a distinct hue. */
const CHIP_CLASSES: Record<VerificationLevel, string> = {
  GOVERNMENT_RECORD: "border-steel/40 bg-steel/10 text-steel",
  LICENSED_PROFESSIONAL: "border-emerald-700/40 bg-emerald-700/10 text-emerald-800",
  TRANSACTION_DOCUMENT: "border-violet-700/40 bg-violet-700/10 text-violet-800",
  DOCUMENT_SUPPORTED: "border-violet-500/40 bg-violet-500/10 text-violet-700",
  PARTICIPANT_REPORTED: "border-amber-600/50 bg-amber-500/10 text-amber-800",
  SYSTEM_INFERRED: "border-gray-400 bg-transparent text-gray-600",
  UNVERIFIED: "border-gray-300 bg-gray-100 text-gray-500",
};

/** Timeline dot color per level (used by Timeline). */
export const LEVEL_DOT_CLASSES: Record<VerificationLevel, string> = {
  GOVERNMENT_RECORD: "bg-steel",
  LICENSED_PROFESSIONAL: "bg-emerald-600",
  TRANSACTION_DOCUMENT: "bg-violet-600",
  DOCUMENT_SUPPORTED: "bg-violet-400",
  PARTICIPANT_REPORTED: "bg-amber-500",
  SYSTEM_INFERRED: "bg-gray-400",
  UNVERIFIED: "bg-gray-300",
};

export default function ProvenanceBadge({ level }: { level: VerificationLevel }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLSpanElement>(null);
  const popoverId = useId();

  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointerDown(event: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <span ref={containerRef} className="relative inline-flex">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={() => setOpen((current) => !current)}
        className={`inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[11px] leading-4 tracking-wide whitespace-nowrap transition-colors hover:opacity-80 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-flame ${CHIP_CLASSES[level]}`}
      >
        {PROVENANCE_LABELS[level]}
      </button>
      {open ? (
        <span
          id={popoverId}
          role="note"
          className="absolute top-full left-0 z-10 mt-1.5 w-60 rounded-md border border-navy/15 bg-white p-2.5 text-xs leading-relaxed text-ink shadow-lg"
        >
          {PROVENANCE_EXPLANATIONS[level]}
        </span>
      ) : null}
    </span>
  );
}
