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
from fishpage.store import reconcile

_log = logging.getLogger(__name__)


def ingest_pending(conn: sqlite3.Connection, incoming_dir: Path, processed_dir: Path) -> list[Path]:
    """Reconcile every Stocklist PDF currently in ``incoming_dir`` into the catalog.

    Each PDF is parsed and reconciled (the single upsert-by-SKU path), then moved to
    ``processed_dir`` so a later scan won't re-ingest it. Returns the source paths ingested,
    in processing order — each has already been moved, so it now lives under ``processed_dir``,
    not at the returned location.

    A parse that yields no Items is treated as an incomplete drop, not an empty Stocklist:
    it is skipped and left in ``incoming_dir`` for a later retry rather than reconciled, since
    reconciling nothing would zero every SKU in the catalog.
    """
    processed_dir.mkdir(parents=True, exist_ok=True)
    # Reconcile oldest-first so the newest Stocklist lands last: reconcile zeroes absentees
    # and advances last_seen by the run's date, so applying an older drop after a newer one
    # would regress both. Sort by the filename-derived date, not the filename itself.
    pending = sorted(incoming_dir.glob("*.pdf"), key=stocklist_date)
    ingested: list[Path] = []
    for pdf in pending:
        items = parse_stocklist(pdf)
        if not items:
            _log.warning(
                "Parsed no Items from %s; leaving it for retry (incomplete copy?).", pdf.name
            )
            continue
        reconcile(conn, items, stocklist_date(pdf))
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
    """Derive the Stocklist date from a ``..._M-D-YY.pdf`` filename, else fall back to today."""
    match = re.search(r"(\d{1,2})-(\d{1,2})-(\d{2})\b", pdf_path.stem)
    if match is None:
        return date.today()
    month, day, year = (int(part) for part in match.groups())
    return date(2000 + year, month, day)
