# tests/

Cross-cutting test notes only — no test code lives at the repo root.

- **Backend tests** (pytest): `apps/api/tests/` — run with `make test-api`
  (or `cd apps/api && uv run pytest -q`). Uses `TEST_DATABASE_URL`
  (default `postgresql+psycopg://knomes:knomes@localhost:5433/knomes_test`).
- **Web tests** (Vitest + Playwright): `apps/web/` — run with `make test-web`
  (or `cd apps/web && npm test -- --run`).
- **Contract parity fixtures**: `packages/shared/fixtures/` (exported by the
  backend suite, parsed by the web suite — see `packages/shared/README.md`).

`make test` runs both suites.
