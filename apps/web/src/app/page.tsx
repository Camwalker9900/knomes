import SearchBox from "@/components/SearchBox";

const HISTORY_SECTIONS: { title: string; description: string }[] = [
  {
    title: "Permits",
    description: "City permits from application through final inspection, straight from the record.",
  },
  {
    title: "Inspections",
    description: "Inspections performed by licensed professionals, with the source attached.",
  },
  {
    title: "Repairs",
    description: "Repairs reported by participants and, where possible, verified against permits.",
  },
  {
    title: "Code violations",
    description: "Code enforcement cases as they were opened, acted on, and resolved.",
  },
  {
    title: "Previous transactions",
    description: "Prior contracts and how each one ended — closed, terminated, or withdrawn.",
  },
  {
    title: "Verified property findings",
    description: "Condition findings tracked from open to resolved. History is never deleted.",
  },
];

export default function HomePage() {
  return (
    <div className="mx-auto w-full max-w-5xl px-4 sm:px-6">
      <section className="mx-auto max-w-2xl pt-14 pb-12 text-center sm:pt-20">
        <h1 className="font-display text-4xl leading-tight font-bold text-navy sm:text-5xl">
          Know what happened to a house before you buy it.
        </h1>
        <p className="mt-4 text-lg text-ink/70">
          Knomes assembles the public record of a Houston-area home — permits, inspections,
          repairs, and past contracts — into one timeline, with every fact labeled by how it was
          verified.
        </p>
        <div className="mt-8">
          <SearchBox />
        </div>
      </section>
      <section aria-labelledby="property-history-heading" className="pb-16">
        <h2
          id="property-history-heading"
          className="text-sm font-semibold tracking-widest text-steel uppercase"
        >
          Property history
        </h2>
        <ul className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {HISTORY_SECTIONS.map((section) => (
            <li
              key={section.title}
              className="rounded-md border border-navy/10 bg-white p-5 shadow-sm"
            >
              <h3 className="font-display text-lg font-bold text-navy">{section.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink/70">{section.description}</p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
