# Watched-folder ingestion: poll the incoming directory, reconciling drops through a trigger-agnostic core

Updating the catalog should be one manual step — drop the night's Stocklist PDF into a folder — and
automatable later without reshaping the code. We split that into a **trigger-agnostic core** and a
**thin trigger**, and the core is where all the behavior (and all the tests) live:

- **`ingest_pending(conn, incoming, processed)`** does one synchronous pass: parse and reconcile every
  PDF currently in `incoming` through the single upsert-by-SKU path, then move each file to `processed`.
  It knows nothing about *how* it was triggered. A folder watcher, an HTTP upload, or a queue consumer
  can all drive it unchanged.
- **`watch_incoming(...)`** is the trigger in use today: a loop that calls `ingest_pending` and sleeps.

## Poll, don't subscribe

The trigger **polls** the directory on an interval rather than subscribing to filesystem events
(inotify / `watchdog`). The deployment target is a Docker bind-mount of an Unraid volume, where inotify
is unreliable across the mount boundary and can silently drop events — and a missed event for a
once-a-night drop means the catalog silently goes stale, the worst failure mode here. A nightly drop has
no latency requirement, so polling's only real cost (seconds of delay) does not matter.

Polling also makes **partial writes** self-correcting, within the limits of what the parser can detect. A
still-copying PDF that cannot yet be opened raises; the pass logs it and leaves the file in `incoming`, so
the next tick retries once the copy has settled. The loop swallows per-pass errors by design rather than
dying on a half-written or malformed file.

A subtler case is a file that *opens* but is incomplete. The parser returns the rows it could extract, not
a raise, so a truncated drop can parse to a short list — and reconciling an empty list would zero every SKU
in the catalog. To stop that, `ingest_pending` treats a **no-row parse as an incomplete drop**: it is
skipped and left in `incoming` for retry, never reconciled. This is a guard against the catastrophic
zero-row case, not a completeness check — a truncated PDF that yields a *partial* (non-empty) row set would
still reconcile, briefly zeroing the SKUs missing from the fragment until the next full drop. Detecting
partial extraction belongs with parser resilience, not the trigger.

This is reversible: because the trigger is a few lines over `ingest_pending`, swapping in event-driven
or HTTP-driven triggering touches no tested code. A move to cloud object storage would not reuse a local
watcher at all — it would call the same core from a bucket-notification handler.

## Move processed files aside, oldest first

Ingested PDFs are **moved to `processed/`**, not deleted: re-scanning is then idempotent (a moved file is
not seen again) and the source Stocklists remain as an audit trail.

Drops are reconciled in **Stocklist-date order** (parsed from the `..._M-D-YY.pdf` filename), not filename
order. Reconciliation zeroes absentees and advances `last_seen` by the run's date, so applying an older
Stocklist after a newer one would regress both. Note that filename lexical order disagrees with calendar
order (`6-19-26` sorts before `6-9-26`), so the sort key must be the parsed date.

Sorting within a pass only orders the files present *that tick*. Across passes — a newer Stocklist
ingested and moved aside, then an older one dropped before the next tick — there is nothing to sort
against, so ingestion is made **monotonic** instead: each pass reads the catalog's latest reconciled date
(`MAX(last_seen)`) and skips any drop not strictly newer. An out-of-order or re-dropped Stocklist is left
in `incoming` with a logged warning rather than regressing the live catalog. This guard lives in the
ingestion layer, not in `reconcile`, which stays trigger-agnostic.

A drop whose filename carries **no `M-D-YY` date** is likewise skipped and left for the user to rename:
the date is the authoritative run-date, and inventing one (e.g. today's) would silently mis-stamp
`last_seen` and mis-pivot the absentee sweep. `stocklist_date` therefore raises rather than guessing, and
the ingestion pass turns that into a per-file skip so one misnamed drop cannot wedge the others.

## Consequences

- No new dependency: polling uses the standard library, where `watchdog` would have added one.
- The watcher shares the connection the app serves from, so a drop is reflected in the live catalog
  without a restart. That makes a request handler a concurrent reader against the ingestion writer; the
  brief half-reconciled read window this opens is accepted as-is for a low-traffic internal tool.
- A drop that can never become eligible — permanently unparseable (always raises or parses to zero rows),
  misnamed, or older than the catalog — is retried and re-skipped every tick, staying in `incoming`. That
  is noisy in the logs but harmless to the catalog, and self-resolves once the user renames, removes, or
  replaces the file. Distinguishing "still copying" from "broken" is deferred to the parser-resilience work.
- Files are moved with `shutil.move`, not `Path.rename`, so `incoming` and `processed` may sit on
  different mounts without an `EXDEV` failure.
- "Absent" is defined by run date in reconciliation, so a real run needs a distinct date per Stocklist —
  the same constraint the upsert-and-never-delete design already carries (see
  [ADR 0001](0001-sku-permanent-key-upsert-never-delete.md)).
