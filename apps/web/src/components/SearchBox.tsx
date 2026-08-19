"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { searchProperties } from "@/lib/api";
import type { PropertySearchResult, SearchMatchType } from "@/lib/types";

const DEBOUNCE_MS = 250;
const MIN_QUERY_LENGTH = 2;

const MATCH_TYPE_HINTS: Record<SearchMatchType, string> = {
  EXACT_ADDRESS: "Exact address",
  HCAD_ID: "HCAD account",
  FUZZY_ADDRESS: "Similar address",
};

type SearchState = "idle" | "loading" | "done" | "error";

function formatResult(result: PropertySearchResult): string {
  const unit = result.unit ? ` ${result.unit}` : "";
  return `${result.address_line1}${unit}, ${result.city}, ${result.state} ${result.postal_code}`;
}

export default function SearchBox() {
  const router = useRouter();
  const baseId = useId();
  const listboxId = `${baseId}-listbox`;

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PropertySearchResult[]>([]);
  const [state, setState] = useState<SearchState>("idle");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  const requestRef = useRef(0);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      requestRef.current += 1;
      setResults([]);
      setState("idle");
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    const requestId = ++requestRef.current;
    setState("loading");
    const timer = setTimeout(() => {
      void searchProperties(trimmed)
        .then((found) => {
          if (requestRef.current !== requestId) {
            return;
          }
          setResults(found);
          setState("done");
          setOpen(true);
          setActiveIndex(-1);
        })
        .catch(() => {
          if (requestRef.current !== requestId) {
            return;
          }
          setResults([]);
          setState("error");
          setOpen(true);
          setActiveIndex(-1);
        });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointerDown(event: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
        setActiveIndex(-1);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function selectResult(result: PropertySearchResult) {
    setOpen(false);
    setActiveIndex(-1);
    setQuery(formatResult(result));
    router.push(`/property/${result.id}`);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    if (!open && event.key === "ArrowDown" && results.length > 0) {
      setOpen(true);
      setActiveIndex(0);
      event.preventDefault();
      return;
    }
    if (!open || results.length === 0) {
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => (index + 1) % results.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => (index <= 0 ? results.length - 1 : index - 1));
    } else if (event.key === "Enter") {
      const active = activeIndex >= 0 ? results[activeIndex] : undefined;
      if (active) {
        event.preventDefault();
        selectResult(active);
      }
    }
  }

  return (
    <div ref={containerRef} className="relative mx-auto w-full max-w-xl text-left">
      <label htmlFor={`${baseId}-input`} className="sr-only">
        Search by street address or HCAD account number
      </label>
      <input
        id={`${baseId}-input`}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={activeIndex >= 0 ? `${baseId}-option-${activeIndex}` : undefined}
        autoComplete="off"
        spellCheck={false}
        placeholder="Search a Houston address or HCAD account number"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={onKeyDown}
        className="w-full rounded-md border border-navy/25 bg-white px-4 py-3 text-base text-ink shadow-sm placeholder:text-ink/40 focus:border-flame focus:outline-2 focus:outline-offset-2 focus:outline-flame"
      />
      <ul
        id={listboxId}
        role="listbox"
        aria-label="Property search results"
        className={`absolute top-full right-0 left-0 z-20 mt-1 overflow-hidden rounded-md border border-navy/15 bg-white shadow-lg ${
          open ? "" : "hidden"
        }`}
      >
        {state === "error" ? (
          <li className="px-4 py-3 text-sm text-ink/60">
            Search is unavailable right now. Try again in a moment.
          </li>
        ) : results.length === 0 ? (
          state === "done" ? (
            <li className="px-4 py-3 text-sm text-ink/60">
              No matching properties found. Try a street address like 100 Test Street.
            </li>
          ) : null
        ) : (
          results.map((result, index) => (
            <li
              key={result.id}
              id={`${baseId}-option-${index}`}
              role="option"
              aria-selected={index === activeIndex}
              onPointerDown={(event) => {
                // pointerdown (not click) so selection wins over the outside-close handler
                event.preventDefault();
                selectResult(result);
              }}
              className={`flex cursor-pointer items-baseline justify-between gap-3 px-4 py-2.5 text-sm ${
                index === activeIndex ? "bg-flame/10 text-navy" : "text-ink hover:bg-navy/5"
              }`}
            >
              <span className="min-w-0">
                <span className="block truncate font-medium">{formatResult(result)}</span>
                {result.hcad_account_id ? (
                  <span className="font-mono text-[11px] text-ink/50">
                    HCAD {result.hcad_account_id}
                  </span>
                ) : null}
              </span>
              <span className="shrink-0 font-mono text-[11px] text-steel">
                {MATCH_TYPE_HINTS[result.match_type]}
              </span>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
