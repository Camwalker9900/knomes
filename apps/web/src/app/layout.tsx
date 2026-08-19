import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Knomes — Know your home",
    template: "%s — Knomes",
  },
  description:
    "Property condition history for Houston and Harris County: permits, inspections, repairs, code violations, and prior transactions in one verified ledger.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col bg-paper font-sans text-ink antialiased">
        <header className="bg-navy text-white">
          <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
            <Link
              href="/"
              className="flex items-center gap-3 rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-cream"
            >
              <Image
                src="/knomes-icon.svg"
                alt=""
                width={40}
                height={40}
                unoptimized
                priority
                className="h-10 w-10 rounded-lg"
              />
              <span className="flex items-baseline gap-3">
                <span className="font-display text-2xl font-bold tracking-tight">Knomes</span>
                <span className="hidden border-l border-white/25 pl-3 text-sm text-cream sm:inline">
                  Know your home
                </span>
              </span>
            </Link>
            <nav aria-label="Main">
              <Link
                href="/"
                className="rounded-sm text-sm text-white/80 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-cream"
              >
                Search
              </Link>
            </nav>
          </div>
        </header>
        <main className="flex-1">{children}</main>
        <footer className="border-t border-navy/10 bg-white">
          <div className="mx-auto flex w-full max-w-5xl flex-col gap-2 px-4 py-6 text-sm text-ink/70 sm:px-6">
            <p>
              Public-record data refreshes periodically — each property page shows per-source
              freshness.
            </p>
            <p className="text-xs text-ink/50">
              Knomes · Property condition ledger for Houston and Harris County, Texas.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
