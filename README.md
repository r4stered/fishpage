# fishpage

[![codecov](https://codecov.io/github/r4stered/fishpage/graph/badge.svg?token=GCF1B15WGZ)](https://codecov.io/github/r4stered/fishpage)

An internal browsing tool that turns Sea Dwelling Creatures' (SDC) freshwater wholesale
**Stocklist** PDF into a searchable, filterable, sortable catalog — used to decide what
livestock to order. Not a public storefront.

Stack: Python (managed with [uv](https://docs.astral.sh/uv/)) · FastAPI · pdfplumber · SQLite.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Install dependencies (project + dev) with:

```sh
uv sync
```

## Running

```sh
uv run just run
```

Then open <http://127.0.0.1:8000/> for the catalog grid, or `GET /catalog` for JSON.

The catalog lives in a local SQLite file (`fishpage.db`) that persists across restarts. The
first run seeds it from the committed sample PDF; later runs reuse whatever is already there.
Common overrides:

- `STOCKLIST_PDF=/path/to.pdf` — seed an empty catalog from a different Stocklist.
- `FISHPAGE_DB=/path/to.db` — use a different database file.

While it runs, drop a Stocklist PDF named `..._M-D-YY.pdf` into `data/incoming/` and the
catalog reconciles it within one poll, moving the file to `data/processed/`.

## Testing & checks

[`just`](https://just.systems/) recipes are the single source of truth for the CI gate — the
same commands run locally and in CI. `just` ships as a dev dependency, so `uv sync` is all the
setup you need.

```sh
uv run just check       # full gate: lint + typecheck + test (what CI runs)

uv run just lint        # ruff check + ruff format --check
uv run just format      # ruff check --fix + ruff format (writes)
uv run just typecheck   # ty check
uv run just test        # pytest
uv run just --list      # show all recipes
```

Run `uv run just check` before pushing. Optionally install the pre-commit hooks:

```sh
uvx pre-commit install
```

## Deployment

The app deploys as a single Docker image to [Fly.io](https://fly.io/), auto-deployed on merge
to `main`. Every cloud dependency is opt-in and defaults off, so the commands above need no
cloud credentials. First-time bring-up of the whole cloud stack is one command (`just
bootstrap`); see [`infra/README.md`](infra/README.md) for prerequisites and the full deploy,
rollback, and operations details.

## Project docs

- [`CONTEXT.md`](CONTEXT.md) — domain glossary. Use this vocabulary throughout.
- [`docs/adr/`](docs/adr/) — architectural decision records (parsing, ingestion, deploy, …).
- [`docs/agents/`](docs/agents/) — issue-tracker, triage-label, and domain-doc conventions
  for AI agents working in this repo.
