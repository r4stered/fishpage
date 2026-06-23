"""Watched-folder ingestion: turn a Stocklist PDF dropped into a directory into a catalog update.

The trigger is kept separate from the work. :func:`ingest_pending` does one synchronous
scan-and-reconcile pass over the incoming directory and is trigger-agnostic; a folder watcher,
an HTTP upload, or a queue consumer can all drive it. :func:`watch_incoming` is the thin
polling loop that drives it on a mounted volume today.
"""

import logging
import re
import shutil
import sqlite3
import time
from datetime import date
from pathlib import Path

from fishpage.parser import parse_stocklist
from fishpage.store import latest_stocklist_date, reconcile

_log = logging.getLogger(__name__)


def ingest_pending(conn: sqlite3.Connection, incoming_dir: Path, processed_dir: Path) -> list[Path]:
    """Reconcile every Stocklist PDF currently in ``incoming_dir`` into the catalog.

    Each eligible PDF is parsed and reconciled (the single upsert-by-SKU path), then moved to
    ``processed_dir`` so a later scan won't re-ingest it. Returns the source paths ingested,
    in processing order — each has already been moved, so it now lives under ``processed_dir``,
    not at the returned location.

    Three kinds of drop are skipped and left in ``incoming_dir`` rather than reconciled, because
    each would otherwise corrupt the catalog through ``reconcile``'s run-date semantics:

    - **No valid date in the filename.** The Stocklist date drives ``last_seen`` and the absentee
      sweep, so a missing or out-of-range date can't be guessed — the file waits to be renamed.
    - **Older than the catalog.** Ingestion is monotonic: a Stocklist no newer than the latest
      already reconciled would regress ``last_seen`` and zero every SKU absent from it. This
      guards the cross-pass case the within-pass date sort cannot see.
    - **No parsed Items.** Treated as an incomplete copy, not an empty Stocklist; reconciling
      nothing would zero every SKU. It waits to settle and is retried.
    """
    processed_dir.mkdir(parents=True, exist_ok=True)
    latest = latest_stocklist_date(conn)

    dated: list[tuple[date, Path]] = []
    for pdf in incoming_dir.glob("*.pdf"):
        try:
            dated.append((stocklist_date(pdf), pdf))
        except ValueError:
            _log.warning(
                "Skipping %s: no valid M-D-YY date in its name; rename it to ingest.", pdf.name
            )

    ingested: list[Path] = []
    # Oldest-first so the newest Stocklist lands last; reconcile pivots the absentee sweep on
    # the run date, so an older drop applied after a newer one regresses the catalog.
    for stocklist, pdf in sorted(dated, key=lambda pair: pair[0]):
        if latest is not None and stocklist <= latest:
            _log.warning(
                "Skipping %s: its date %s is not newer than the catalog's %s.",
                pdf.name,
                stocklist,
                latest,
            )
            continue
        items = parse_stocklist(pdf)
        if not items:
            _log.warning(
                "Parsed no Items from %s; leaving it for retry (incomplete copy?).", pdf.name
            )
            continue
        reconcile(conn, items, stocklist)
        latest = stocklist  # advance so a same-pass duplicate date is also held back
        # shutil.move, not Path.rename: incoming and processed may sit on different mounts,
        # where rename raises EXDEV. move falls back to copy+delete across devices.
        shutil.move(pdf, processed_dir / pdf.name)
        ingested.append(pdf)
    return ingested


def watch_incoming(
    conn: sqlite3.Connection,
    incoming_dir: Path,
    processed_dir: Path,
    *,
    interval: float = 30.0,
) -> None:
    """Poll ``incoming_dir`` forever, ingesting each Stocklist PDF as it lands.

    Polling rather than filesystem events is deliberate: the incoming folder is a mounted
    volume where inotify is unreliable, and a nightly drop has no latency requirement. A drop
    still being copied in is handled on the next tick: if its PDF cannot yet be opened the pass
    raises and is logged, and if it opens but parses to no rows it is skipped — either way the
    file stays in ``incoming_dir`` and is picked up once it has settled.
    """
    incoming_dir.mkdir(parents=True, exist_ok=True)
    while True:
        _ingest_pass(conn, incoming_dir, processed_dir)
        time.sleep(interval)


def _ingest_pass(conn: sqlite3.Connection, incoming_dir: Path, processed_dir: Path) -> None:
    """One watcher iteration: ingest pending drops, surviving any failure to the next poll.

    A failed pass (e.g. a PDF still being copied in that cannot be opened yet) is logged and
    swallowed so the loop keeps polling and the file is retried once it has settled.
    """
    try:
        for pdf in ingest_pending(conn, incoming_dir, processed_dir):
            _log.info("Ingested Stocklist %s", pdf.name)
    except Exception:
        _log.exception("Ingestion pass failed; retrying on next poll")


def stocklist_date(pdf_path: Path) -> date:
    """Derive the Stocklist date from a ``..._M-D-YY.pdf`` filename.

    Raises :class:`ValueError` when the name carries no ``M-D-YY`` token *or* carries one that
    is not a real date (e.g. ``13-40-26``). The date is the authoritative run-date for
    reconciliation, so a caller must decide what to do about such a file rather than have a date
    silently invented for it.
    """
    match = re.search(r"(\d{1,2})-(\d{1,2})-(\d{2})\b", pdf_path.stem)
    if match is not None:
        month, day, year = (int(part) for part in match.groups())
        try:
            return date(2000 + year, month, day)
        except ValueError:
            pass  # matched a date-shaped token, but it is out of range — fall through
    raise ValueError(f"no valid Stocklist date in filename: {pdf_path.name!r}")
