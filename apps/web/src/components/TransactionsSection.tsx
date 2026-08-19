import ProvenanceBadge from "@/components/ProvenanceBadge";
import { formatDate, humanizeEnum } from "@/lib/format";
import type { TransactionCycleOut, TransactionOutcome } from "@/lib/types";

const OUTCOME_LABELS: Record<TransactionOutcome, string> = {
  UNKNOWN: "Outcome unknown",
  CLOSED: "Closed",
  TERMINATED: "Terminated",
  WITHDRAWN: "Withdrawn",
  EXPIRED: "Expired",
};

/** Exact public wording for an inferred, unverified non-close (spec §33). */
export const INFERRED_TERMINATION_WORDING =
  "A previous contract appears not to have closed. Reason not verified.";

function dateSegments(transaction: TransactionCycleOut): { label: string; date: string }[] {
  const segments: { label: string; date: string }[] = [];
  if (transaction.started_at) {
    segments.push({ label: "Started", date: transaction.started_at });
  }
  if (transaction.under_contract_at) {
    segments.push({ label: "Under contract", date: transaction.under_contract_at });
  }
  if (transaction.terminated_at) {
    segments.push({ label: "Terminated", date: transaction.terminated_at });
  }
  if (transaction.closed_at) {
    segments.push({ label: "Closed", date: transaction.closed_at });
  }
  return segments;
}

function TransactionRow({ transaction }: { transaction: TransactionCycleOut }) {
  const inferredUnknown =
    transaction.reason_verification_level === "SYSTEM_INFERRED" &&
    transaction.termination_reason === "UNKNOWN";
  const showReason = transaction.outcome === "TERMINATED" || transaction.termination_reason !== "UNKNOWN";

  return (
    <li className="rounded-md border border-navy/10 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-display text-base font-bold text-navy">
          {OUTCOME_LABELS[transaction.outcome]}
        </span>
        <span className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-ink/60">
          {dateSegments(transaction).map(({ label, date }) => (
            <span key={label}>
              {label}{" "}
              <time dateTime={date} className="text-ink/80">
                {formatDate(date)}
              </time>
            </span>
          ))}
        </span>
      </div>
      {showReason ? (
        inferredUnknown ? (
          <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-ink/80">
            <span>{INFERRED_TERMINATION_WORDING}</span>
            <ProvenanceBadge level={transaction.reason_verification_level} />
          </p>
        ) : transaction.termination_reason !== "UNKNOWN" ? (
          <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-ink/80">
            <span>
              Termination reason: {humanizeEnum(transaction.termination_reason).toLowerCase()}.
            </span>
            <ProvenanceBadge level={transaction.reason_verification_level} />
          </p>
        ) : (
          <p className="mt-2 text-sm text-ink/60">Termination reason not documented.</p>
        )
      ) : null}
    </li>
  );
}

export default function TransactionsSection({
  transactions,
}: {
  transactions: TransactionCycleOut[];
}) {
  return (
    <section aria-labelledby="transactions-heading">
      <h2 id="transactions-heading" className="font-display text-2xl font-bold text-navy">
        Previous transactions
      </h2>
      {transactions.length === 0 ? (
        <p className="mt-4 text-sm text-ink/60">No prior transactions on record.</p>
      ) : (
        <ul className="mt-4 space-y-3">
          {transactions.map((transaction) => (
            <TransactionRow key={transaction.id} transaction={transaction} />
          ))}
        </ul>
      )}
    </section>
  );
}
