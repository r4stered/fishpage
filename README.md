# fishpage

[![codecov](https://codecov.io/github/r4stered/fishpage/graph/badge.svg?token=GCF1B15WGZ)](https://codecov.io/github/r4stered/fishpage)

An internal browsing tool that turns Sea Dwelling Creatures' (SDC) freshwater wholesale
**Stocklist** PDF into a searchable, filterable, sortable catalog — used to decide what
livestock to order. Not a public storefront.

## What it does (v1)

- Parses the SDC Stocklist PDF into structured records (SKU, size, name, retail/special
  price, quantity) and stores them in SQLite. Columns are located from the page header
  rather than fixed coordinates, so a shifted layout still parses and a misaligned price is
  flagged instead of silently recorded; see
  [ADR 0006](docs/adr/0006-header-derived-column-boundaries.md).
- Ingestion is **upsert-by-SKU and never deletes**: an item missing from the latest
  Stocklist goes to quantity 0 (with a `last_seen` date) rather than disappearing, so any
  data attached to it survives temporary stock-outs. A **reuse guard** flags any SKU that
  reappears under a materially different name for human review. See
  [ADR 0001](docs/adr/0001-sku-permanent-key-upsert-never-delete.md).
- Serves a grid of Item cards — defaulting to in-stock only, with a toggle for the rest —
  with **fuzzy name search** (approximate, order-independent matching ranked by relevance;
  see [ADR 0004](docs/adr/0004-fuzzy-name-search-scorers.md)) and **Derived Category**,
  **Size**, and **on-special** filters, plus an **effective-price sort** (special price if
  present, else retail). The Size filter matches the raw stocklist token against the grade
  set `-`/`S`/`M`/`L`/`Jumbo`; per
  [ADR 0002](docs/adr/0002-size-column-is-overloaded.md) packaging-unit rows (e.g. `POTTED`)
  match no grade. Every control combines.
- **Persists the catalog across restarts**: the SQLite file is never deleted on boot. An empty
  catalog is seeded once from the configured sample Stocklist PDF; a catalog that already holds
  Items is reused as-is. It then **watches an incoming
  folder**: a Stocklist PDF dropped into it is parsed and reconciled into the live catalog and
  moved aside. A polling trigger over a trigger-agnostic core keeps the drop reliable on a
  mounted volume; the cloud deployment drives the same core from an authenticated upload page
  instead (see [ADR 0005](docs/adr/0005-watched-folder-polling-trigger.md)).

## Stack

Python (managed with [uv](https://docs.astral.sh/uv/)) · FastAPI · pdfplumber · SQLite.
Deploys as a single Docker image to [Fly.io](https://fly.io/), auto-deployed from `main`. SQLite is
kept durable by [Litestream](https://litestream.io/) streaming the database to Cloudflare R2 and
restoring it on boot (see [ADR 0008](docs/adr/0008-sqlite-litestream-object-storage.md)); a Cloudflare
Tunnel + Access login fronts the app so the wholesale prices are not public (see
[ADR 0007](docs/adr/0007-deploy-to-flyio-cloud-not-unraid.md)). In the cloud a new Stocklist is
ingested through an authenticated upload page rather than the local watched folder.

## Run it

```sh
uv run just run        # serves a local SQLite catalog, seeding it from the sample Stocklist if empty
```

Then open <http://127.0.0.1:8000/> for the catalog grid, or `GET /catalog` for JSON.

The catalog lives in a local SQLite file (`fishpage.db`) that persists across restarts. The first
run seeds it from the committed sample PDF (`tests/fixtures/Freshwater_Stocklist_6-19-26.pdf`);
later runs reuse whatever is already there. Seed from another PDF into an empty catalog with
`STOCKLIST_PDF=/path/to.pdf uv run just run`, or point at a different database file with
`FISHPAGE_DB=/path/to.db`.

While it runs, drop a Stocklist PDF into `data/incoming/` (named `..._M-D-YY.pdf` so its date
is read from the filename) and the catalog reconciles it within one poll, moving the file to
`data/processed/`. Override the locations and cadence with `INCOMING_DIR`, `PROCESSED_DIR`, and
`INGEST_POLL_SECONDS`.

Every cloud dependency is opt-in and defaults off, so the commands above need no cloud
credentials. The cloud deploy switches them on through the environment: `LITESTREAM_REPLICA_URL`
(Litestream replication to Cloudflare R2), `OTEL_EXPORTER_OTLP_ENDPOINT` (telemetry export), and
`FISHPAGE_CLOUD_INGEST` (drive ingestion from the authenticated upload page instead of the local
watched folder).

## Checks

[`just`](https://just.systems/) recipes are the single source of truth for the CI gate —
the same commands run locally, in CI, and in pre-commit. `just` itself ships as a dev
dependency (`rust-just`), so `uv sync` is all the setup you need.

```sh
uv run just check       # the full gate: lint + typecheck + test (what CI runs)

uv run just lint        # ruff check + ruff format --check
uv run just format      # ruff check --fix + ruff format (writes)
uv run just typecheck   # ty check
uv run just test        # pytest
uv run just --list      # show all recipes
```

CI runs `lint`, `types`, and `test` as three required checks on every PR to `main`, so run
`uv run just check` before pushing. Optionally install the pre-commit hooks (ruff + ty; tests
stay in CI only) to catch issues before each commit:

```sh
uvx pre-commit install
```

## Project docs

- [`CONTEXT.md`](CONTEXT.md) — domain glossary. Use this vocabulary throughout.
- [`docs/adr/`](docs/adr/) — architectural decision records.
- [`docs/agents/`](docs/agents/) — issue-tracker, triage-label, and domain-doc conventions
  for AI agents working in this repo.

## Status

Active development against PRD [#1](https://github.com/r4stered/fishpage/issues/1). Landed
so far: the walking skeleton ([#2](https://github.com/r4stered/fishpage/issues/2)),
upsert-by-SKU reconciliation with `last_seen` and zeroed absentees
([#3](https://github.com/r4stered/fishpage/issues/3)), the reuse guard
([#4](https://github.com/r4stered/fishpage/issues/4)), the in-stock default + out-of-stock
toggle ([#5](https://github.com/r4stered/fishpage/issues/5)), the Derived Category filter
([#6](https://github.com/r4stered/fishpage/issues/6)), fuzzy name search
([#7](https://github.com/r4stered/fishpage/issues/7)), the Size/on-special filters and
effective-price sort ([#8](https://github.com/r4stered/fishpage/issues/8)), the
CI / dev-tooling gate ([#14](https://github.com/r4stered/fishpage/issues/14)),
watched-folder ingestion ([#9](https://github.com/r4stered/fishpage/issues/9)), parser
row resilience — skip-and-log malformed rows + SKU-shape validation
([#12](https://github.com/r4stered/fishpage/issues/12)), and header-derived column detection
for varied Stocklist layouts ([#13](https://github.com/r4stered/fishpage/issues/13)).

Remaining v1 slices: cloud deployment to Fly.io — a Litestream-backed image, an authenticated
upload page, a gated push-to-`main` pipeline, and OpenTelemetry wiring
([#10](https://github.com/r4stered/fishpage/issues/10), to be rewritten from its original
Unraid framing; see [ADR 0007](docs/adr/0007-deploy-to-flyio-cloud-not-unraid.md) and
[ADR 0008](docs/adr/0008-sqlite-litestream-object-storage.md)).

## Out of scope (deferred to phase 2)

Images and care **Classifiers** (difficulty of care, plant needs, …), the AI-assisted
**Enrichment** that would populate them, the approval admin surface, and the nightly
email-poller. The v1 schema does not pre-build support for these.
