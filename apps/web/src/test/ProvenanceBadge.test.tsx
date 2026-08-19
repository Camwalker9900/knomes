import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ProvenanceBadge, {
  PROVENANCE_EXPLANATIONS,
  PROVENANCE_LABELS,
} from "@/components/ProvenanceBadge";
import type { VerificationLevel } from "@/lib/types";

const EXPECTED_LABELS: Record<VerificationLevel, string> = {
  GOVERNMENT_RECORD: "Government Record",
  LICENSED_PROFESSIONAL: "Licensed Professional",
  TRANSACTION_DOCUMENT: "Transaction Document",
  DOCUMENT_SUPPORTED: "Document Supported",
  PARTICIPANT_REPORTED: "Participant Report",
  SYSTEM_INFERRED: "System Inference",
  UNVERIFIED: "Unverified",
};

const ALL_LEVELS = Object.keys(EXPECTED_LABELS) as VerificationLevel[];

describe("ProvenanceBadge", () => {
  it.each(ALL_LEVELS)("renders the contract label for %s", (level) => {
    render(<ProvenanceBadge level={level} />);
    expect(screen.getByRole("button", { name: EXPECTED_LABELS[level] })).toBeInTheDocument();
  });

  it("exports a label map covering exactly the seven verification levels", () => {
    expect(PROVENANCE_LABELS).toEqual(EXPECTED_LABELS);
  });

  it("toggles an explanation popover on click", () => {
    render(<ProvenanceBadge level="GOVERNMENT_RECORD" />);
    const badge = screen.getByRole("button", { name: "Government Record" });

    expect(badge).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByText(PROVENANCE_EXPLANATIONS.GOVERNMENT_RECORD),
    ).not.toBeInTheDocument();

    fireEvent.click(badge);
    expect(badge).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Sourced directly from a government record.")).toBeInTheDocument();

    fireEvent.click(badge);
    expect(badge).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByText(PROVENANCE_EXPLANATIONS.GOVERNMENT_RECORD),
    ).not.toBeInTheDocument();
  });
});
