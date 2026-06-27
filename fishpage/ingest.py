"""Watched-folder ingestion: turn a Stocklist PDF dropped into a directory into a catalog update.

The trigger is kept separate from the work. :func:`ingest_pending` does one synchronous
scan-and-reconcile pass over the incoming directory and is trigger-agnostic; a folder watcher,
an HTTP upload, or a queue consumer can all drive it. :func:`watch_incoming` is the thin
polling loop that drives it on a mounted volume today.
"""

import logging
import os
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from fishpage import observability
from fishpage.config import Settings, load_settings
from fishpage.models import Item
from fishpage.parser import parse_stocklist
from fishpage.store import all_items, latest_stocklist_date, reconcile

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionReport:
    """What one parse would do to the catalog, computed before reconciling it.

    ``new`` SKUs are unseen, ``returned`` SKUs were out of stock and come back In stock, ``zeroed``
    is how many currently In-stock SKUs this parse omits (the absentee sweep would zero them),
    ``price_changed`` counts SKUs whose Retail price moved, and ``flagged`` counts Items whose
    Retail price is zero, negative, or absurd. ``implausible`` is the verdict: a parse whose shape
    is too far from the previous Stocklist to trust — held in incoming rather than reconciled.
    """

    parsed: int
    prior_in_stock: int
    new: int
    returned: int
    zeroed: int
    price_changed: int
    flagged: int
    implausible: bool


def _price_is_sane(retail: Decimal, max_retail: float) -> bool:
    return Decimal(0) < retail < Decimal(str(max_retail))


def build_ingestion_report(
    stored: list[Item], parsed: list[Item], settings: Settings
) -> IngestionReport:
    """Compare a freshly parsed Stocklist against the stored catalog and judge its plausibility.

    The baseline is the previous Stocklist's In-stock SKUs (``qty_avail > 0``); absent SKUs were
    already zeroed by the prior run and are not part of "what the last Stocklist carried". A parse
    is implausible when it keeps too few of those rows (a partial or truncated extraction) or would
    zero too many of them (a column-shifted parse the absentee sweep would mistake for a mass
    discontinuation). With no In-stock baseline — a first-ever ingest — there is nothing to compare
    against, so no swing can be implausible.
    """
    by_sku = {item.sku: item for item in stored}
    in_stock = {sku for sku, item in by_sku.items() if item.qty_avail > 0}

    new = returned = price_changed = flagged = 0
    present: set[str] = set()
    for item in parsed:
        present.add(item.sku)
        prior = by_sku.get(item.sku)
        if prior is None:
            new += 1
        else:
            if prior.qty_avail == 0 and item.qty_avail > 0:
                returned += 1
            if prior.retail_price != item.retail_price:
                price_changed += 1
        if not _price_is_sane(item.retail_price, settings.ingest_max_retail_price):
            flagged += 1

    zeroed = len(in_stock - present)
    baseline = len(in_stock)
    implausible = baseline > 0 and (
        len(parsed) < settings.ingest_min_row_fraction * baseline
        or zeroed > settings.ingest_max_zeroed_fraction * baseline
    )
    return IngestionReport(
        parsed=len(parsed),
        prior_in_stock=baseline,
        new=new,
        returned=returned,
        zeroed=zeroed,
        price_changed=price_changed,
        flagged=flagged,
        implausible=implausible,
    )


def ingest_pending(
    conn: sqlite3.Connection,
    incoming_dir: Path,
    processed_dir: Path,
    settings: Settings | None = None,
) -> list[Path]:
    """Reconcile every Stocklist PDF currently in ``incoming_dir`` into the catalog.

    Each eligible PDF is parsed and reconciled (the single upsert-by-SKU path), then moved to
    ``processed_dir`` so a later scan won't re-ingest it. Returns the source paths ingested,
    in processing order — each has already been moved, so it now lives under ``processed_dir``,
    not at the returned location. ``settings`` carries the sanity thresholds; it defaults to the
    process environment so existing callers and tests need not thread it through.

    Four kinds of drop are skipped and left in ``incoming_dir`` rather than reconciled, because
    each would otherwise corrupt the catalog through ``reconcile``'s run-date semantics:

    - **No valid date in the filename.** The Stocklist date drives ``last_seen`` and the absentee
      sweep, so a missing or out-of-range date can't be guessed — the file waits to be renamed.
    - **Older than the catalog.** Ingestion is monotonic: a Stocklist no newer than the latest
      already reconciled would regress ``last_seen`` and zero every SKU absent from it. This
      guards the cross-pass case the within-pass date sort cannot see.
    - **No parsed Items.** Treated as an incomplete copy, not an empty Stocklist; reconciling
      nothing would zero every SKU. It waits to settle and is retried.
    - **Structurally implausible.** A parse that keeps too few of the previous Stocklist's
      In-stock SKUs, or would zero too many of them, is the shape of a partial or column-shifted
      extraction; reconciling it would silently corrupt the catalog, so it too waits and alerts.
    """
    if settings is None:
        settings = load_settings(os.environ)
    with observability.span("ingest_pending"):
        return _ingest_pending(conn, incoming_dir, processed_dir, settings)


def _ingest_pending(
    conn: sqlite3.Connection, incoming_dir: Path, processed_dir: Path, settings: Settings
) -> list[Path]:
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
            observability.record_monotonicity_skip()
            continue
        items = parse_stocklist(pdf)
        if not items:
            _log.warning(
                "Parsed no Items from %s; leaving it for retry (incomplete copy?).", pdf.name
            )
            continue
        report = build_ingestion_report(all_items(conn), items, settings)
        observability.record_ingestion_report(
            new=report.new,
            returned=report.returned,
            zeroed=report.zeroed,
            price_changed=report.price_changed,
            flagged=report.flagged,
        )
        if report.implausible:
            _log.warning(
                "Holding %s: parsed %d Items vs %d previously In stock (would zero %d); "
                "implausible parse, leaving it for retry.",
                pdf.name,
                report.parsed,
                report.prior_in_stock,
                report.zeroed,
            )
            observability.record_ingest_held()
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
    settings: Settings | None = None,
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
        _ingest_pass(conn, incoming_dir, processed_dir, settings)
        time.sleep(interval)


def _ingest_pass(
    conn: sqlite3.Connection,
    incoming_dir: Path,
    processed_dir: Path,
    settings: Settings | None = None,
) -> None:
    """One watcher iteration: ingest pending drops, surviving any failure to the next poll.

    A failed pass (e.g. a PDF still being copied in that cannot be opened yet) is logged and
    swallowed so the loop keeps polling and the file is retried once it has settled.
    """
    try:
        for pdf in ingest_pending(conn, incoming_dir, processed_dir, settings):
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
