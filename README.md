# fishpage

[![codecov](https://codecov.io/github/r4stered/fishpage/graph/badge.svg?token=GCF1B15WGZ)](https://codecov.io/github/r4stered/fishpage)

An internal browsing tool that turns Sea Dwelling Creatures' (SDC) freshwater wholesale
**Stocklist** PDF into a searchable, filterable, sortable catalog — used to decide what
livestock to order. Not a public storefront.

## What it does (v1)

- Parses the SDC Stocklist PDF into structured records (SKU, size, name, retail/special
  price, quantity) and stores them in SQLite.
- Ingestion is **upsert-by-SKU and never deletes**: an item missing from the latest
  Stocklist goes to quantity 0 (with a `last_seen` date) rather than disappearing, so any
  data attached to it survives temporary stock-outs. See
  [ADR 0001](docs/adr/0001-sku-permanent-key-upsert-never-delete.md).
- Serves a grid of item cards — defaulting to in-stock only, with a toggle for the rest —
  with fuzzy name search, filtering by Derived Category / size / on-special, and
  effective-price sort.
- Ingests by watching a folder: drop a new PDF in and it re-parses, so the same path works
  for a future nightly automation.

## Stack

Python (managed with [uv](https://docs.astral.sh/uv/)) · FastAPI · pdfplumber · SQLite.
Deploys as a single Docker container on Unraid, with the SQLite database and the watched
incoming folder on mounted volumes.

## Run it

```sh
uv run just run        # parses the sample Stocklist into a fresh SQLite catalog and serves it
```

Then open <http://127.0.0.1:8000/> for the catalog grid, or `GET /catalog` for JSON.

The walking skeleton rebuilds the catalog from the committed sample PDF
(`tests/fixtures/Freshwater_Stocklist_6-19-26.pdf`) on every start. Point it at another PDF
with `STOCKLIST_PDF=/path/to.pdf uv run just run`.

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

Walking skeleton ([#2](https://github.com/r4stered/fishpage/issues/2)) landed: the sample
Stocklist parses into SQLite and renders as an unstyled grid of Item cards. Ingestion is a
plain insert into a fresh DB for now — the upsert-by-SKU reconciliation, search/filter/sort,
and watched folder are later slices (#3–#10) of PRD
[#1](https://github.com/r4stered/fishpage/issues/1).

## Out of scope (deferred to phase 2)

Images and care **Classifiers** (difficulty of care, plant needs, …), the AI-assisted
**Enrichment** that would populate them, the approval admin surface, and the nightly
email-poller. The v1 schema does not pre-build support for these.
