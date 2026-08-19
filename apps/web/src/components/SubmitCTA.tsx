const SUBMIT_ACTIONS = [
  "Submit inspection",
  "Report previous transaction",
  "Submit repair evidence",
  "Dispute a finding",
] as const;

export default function SubmitCTA() {
  return (
    <section
      aria-labelledby="submit-cta-heading"
      className="rounded-md border border-steel/25 bg-steel/5 p-5"
    >
      <h2 id="submit-cta-heading" className="font-display text-xl font-bold text-navy">
        Have verified information about this property?
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-ink/70">
        Knomes accepts documented submissions — inspection reports, transaction records, and
        repair evidence. Every submission is reviewed before anything becomes public.
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        {SUBMIT_ACTIONS.map((action) => (
          <button
            key={action}
            type="button"
            disabled
            aria-disabled="true"
            title="Coming soon"
            className="inline-flex cursor-not-allowed items-center gap-2 rounded-md border border-navy/20 bg-white px-3 py-1.5 text-sm text-ink/50"
          >
            {action}
            <span className="rounded-full border border-navy/15 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-ink/40 uppercase">
              Coming soon
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
