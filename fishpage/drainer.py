"""The decoupled enrichment drainer: a background pass that drains the un-enriched queue.

Mirrors ingestion's trigger/work split. :func:`drain_pending` is one synchronous, paced pass over
the un-enriched queue and is trigger-agnostic; :func:`run_drainer` is the thin polling loop that
drives it on the always-on Machine. Enrichment is kept out of the upload request on purpose: a
fresh Stocklist can introduce hundreds of new SKUs, and each enrichment is a network round-trip, so
running them inline would hang the upload past the edge's request ceiling. Ingestion only marks new
SKUs un-enriched (it writes no enrichment row); this drainer fills them behind the live catalog.
"""

import logging
import sqlite3
import time
from collections.abc import Callable

from fishpage import observability
from fishpage.enricher import Enricher
from fishpage.store import persist_enrichment, unenriched_items

_log = logging.getLogger(__name__)


def drain_pending(
    conn: sqlite3.Connection,
    enricher: Enricher,
    *,
    rate: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Enrich and persist every Item in the un-enriched queue in one paced pass; return the SKUs
    drained.

    Each SKU is enriched and persisted on its own. A failure on one is logged and skipped — never
    aborting the pass — so the rest still land and the failed SKU stays queued for a later pass.
    Because persistence is per-SKU, a hard crash mid-pass leaves the finished SKUs enriched and the
    remainder still queued, so a restart resumes from exactly the survivors. ``rate`` paces the
    calls: a positive value sleeps that many seconds after each enrichment to spare the API.
    """
    with observability.span("drain_pending"):
        drained: list[str] = []
        for item in unenriched_items(conn):
            try:
                result = enricher.enrich(item.name, category=item.category, size=item.size)
            except Exception:
                _log.exception(
                    "Enrichment failed for SKU %s; leaving it queued for retry", item.sku
                )
                continue
            persist_enrichment(conn, item.sku, result)
            drained.append(item.sku)
            if rate:
                sleep(rate)
        return drained


def run_drainer(
    conn: sqlite3.Connection,
    enricher: Enricher,
    *,
    interval: float = 30.0,
    rate: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Poll the un-enriched queue forever, draining each pass — the drainer's thin trigger.

    Polling rather than an event is deliberate, the same call ingestion makes: there is no latency
    requirement behind filling Classifiers, and a poll loop survives a crash by simply re-reading
    the surviving queue on the next tick.
    """
    while True:
        _drain_pass(conn, enricher, rate=rate, sleep=sleep)
        sleep(interval)


def _drain_pass(
    conn: sqlite3.Connection,
    enricher: Enricher,
    *,
    rate: float,
    sleep: Callable[[float], None],
) -> None:
    """One drainer iteration, surviving any failure to the next poll so the loop never dies."""
    try:
        drained = drain_pending(conn, enricher, rate=rate, sleep=sleep)
        if drained:
            _log.info("Enriched %d Item(s): %s", len(drained), ", ".join(drained))
    except Exception:
        _log.exception("Drain pass failed; retrying on next poll")
