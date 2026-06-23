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

When several drops are pending in one pass they are reconciled in **Stocklist-date order** (parsed from
the `..._M-D-YY.pdf` filename), not filename order. Reconciliation zeroes absentees and advances
`last_seen` by the run's date, so applying an older Stocklist after a newer one would regress both. Note
that filename lexical order disagrees with calendar order (`6-19-26` sorts before `6-9-26`), so the sort
key must be the parsed date.

## Consequences

- No new dependency: polling uses the standard library, where `watchdog` would have added one.
- The watcher shares the connection the app serves from, so a drop is reflected in the live catalog
  without a restart. That makes a request handler a concurrent reader against the ingestion writer; the
  brief half-reconciled read window this opens is accepted as-is for a low-traffic internal tool.
- A *permanently* unparseable PDF — one that always raises, or always parses to zero rows — is retried
  every tick forever and never leaves `incoming`. Distinguishing "still copying" from "broken" is deferred
  to the parser-resilience work; until then a broken drop is noisy in the logs but harmless to the catalog.
- Files are moved with `shutil.move`, not `Path.rename`, so `incoming` and `processed` may sit on
  different mounts without an `EXDEV` failure.
- "Absent" is defined by run date in reconciliation, so a real run needs a distinct date per Stocklist —
  the same constraint the upsert-and-never-delete design already carries (see
  [ADR 0001](0001-sku-permanent-key-upsert-never-delete.md)).
