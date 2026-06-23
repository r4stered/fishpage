# fishpage

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

## Project docs

- [`CONTEXT.md`](CONTEXT.md) — domain glossary. Use this vocabulary throughout.
- [`docs/adr/`](docs/adr/) — architectural decision records.
- [`docs/agents/`](docs/agents/) — issue-tracker, triage-label, and domain-doc conventions
  for AI agents working in this repo.

## Status

Pre-implementation. The design is published as PRD
[#1](https://github.com/r4stered/fishpage/issues/1), broken into slices #2–#10. Start with
[#2 (walking skeleton)](https://github.com/r4stered/fishpage/issues/2).

## Out of scope (deferred to phase 2)

Images and care **Classifiers** (difficulty of care, plant needs, …), the AI-assisted
**Enrichment** that would populate them, the approval admin surface, and the nightly
email-poller. The v1 schema does not pre-build support for these.
