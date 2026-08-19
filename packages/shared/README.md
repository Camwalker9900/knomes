# packages/shared — API contract types & fixtures

This package is the neutral ground where the FastAPI backend and the Next.js
frontend agree on the wire format.

## What lives here

- `src/contracts.ts` — TypeScript declarations that mirror the backend's
  Pydantic response schemas 1:1 (`PropertySearchResult`, `PropertyDetail`,
  `TimelineEvent`, `FindingDetail`, `TransactionCycleOut`,
  `SourceProvenance`). JSON field names in `plan/CONTRACTS.md` are the frozen
  contract; these types must never drift from them.
- `fixtures/` — JSON payloads **exported by the backend test suite**
  (serialized straight from the Pydantic models against seeded data). They are
  checked in so the frontend can test against real backend output without a
  running API.

## How the parity loop works

1. A pytest in `apps/api/tests` serializes canonical fixture responses and
   writes/asserts the JSON files in `packages/shared/fixtures/`.
2. A Vitest suite in `apps/web` parses those same files with the client types
   from `apps/web/src/lib/types.ts`.
3. If either side changes shape, one of the two suites fails — the contract
   cannot silently break.

Regenerate fixtures only by running the backend test suite; never edit the
JSON by hand.
