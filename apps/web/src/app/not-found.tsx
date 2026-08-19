import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-24 text-center sm:px-6">
      <h1 className="font-display text-3xl font-bold text-navy">Property not found</h1>
      <p className="mt-3 text-ink/70">
        No property ledger exists at this address. Search again to find a Houston-area property.
      </p>
      <Link
        href="/"
        className="mt-6 inline-block rounded-md bg-flame px-4 py-2 text-sm font-medium text-white hover:bg-flame/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-flame"
      >
        Back to search
      </Link>
    </div>
  );
}
