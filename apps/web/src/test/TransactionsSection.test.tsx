import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import TransactionsSection, {
  INFERRED_TERMINATION_WORDING,
} from "@/components/TransactionsSection";
import type { TransactionCycleOut } from "@/lib/types";

const inferredTermination: TransactionCycleOut = {
  id: "0f65a200-0001-4000-8000-000000000001",
  started_at: "2025-03-01T00:00:00+00:00",
  under_contract_at: "2025-03-10T00:00:00+00:00",
  terminated_at: "2025-03-24T00:00:00+00:00",
  closed_at: null,
  outcome: "TERMINATED",
  termination_reason: "UNKNOWN",
  reason_verification_level: "SYSTEM_INFERRED",
};

const documentedTermination: TransactionCycleOut = {
  id: "0f65a200-0002-4000-8000-000000000002",
  started_at: "2025-01-01T00:00:00+00:00",
  under_contract_at: "2025-01-05T00:00:00+00:00",
  terminated_at: "2025-01-12T00:00:00+00:00",
  closed_at: null,
  outcome: "TERMINATED",
  termination_reason: "PROPERTY_CONDITION",
  reason_verification_level: "TRANSACTION_DOCUMENT",
};

describe("TransactionsSection", () => {
  it("renders the exact unverified wording for a SYSTEM_INFERRED unknown-reason termination", () => {
    render(<TransactionsSection transactions={[inferredTermination]} />);
    expect(
      screen.getByText("A previous contract appears not to have closed. Reason not verified."),
    ).toBeInTheDocument();
    expect(INFERRED_TERMINATION_WORDING).toBe(
      "A previous contract appears not to have closed. Reason not verified.",
    );
  });

  it("shows a documented termination reason at its verification level", () => {
    render(<TransactionsSection transactions={[documentedTermination]} />);
    expect(screen.getByText("Termination reason: property condition.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Transaction Document" })).toBeInTheDocument();
  });

  it("shows an empty state when there are no transactions", () => {
    render(<TransactionsSection transactions={[]} />);
    expect(screen.getByText("No prior transactions on record.")).toBeInTheDocument();
  });
});
