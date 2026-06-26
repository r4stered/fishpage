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
from dataclasses import dataclass

from fishpage import observability
from fishpage.enricher import Enricher, EnrichmentResult
from fishpage.store import persist_enrichment, unenriched_items

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DrainResult:
    """The roll-up of one drain pass: the SKUs that landed and the count that failed."""

    drained: list[str]
    failed: int


def drain_pending(
    conn: sqlite3.Connection,
    enricher: Enricher,
    *,
    rate: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> DrainResult:
    """Enrich and persist every Item in the un-enriched queue in one paced pass; return the roll-up.

    Each SKU is enriched and persisted on its own. A failure on one is logged with its traceback and
    skipped — never aborting the pass — so the rest still land and the failed SKU stays queued for a
    later pass. Because persistence is per-SKU, a hard crash mid-pass leaves the finished SKUs
    enriched and the remainder still queued, so a restart resumes from exactly the survivors.
    ``rate`` paces the calls: a positive value sleeps that many seconds after each enrichment to
    spare the API.

    A pass-start line is emitted only when the queue is non-empty, so the polling loop's empty ticks
    stay silent. Each landed SKU emits its own INFO line carrying the resolved species and
    Classifiers, and the quality counters fire so a silently-degrading enrichment is observable.
    """
    with observability.span("drain_pending"):
        queued = unenriched_items(conn)
        if not queued:
            return DrainResult(drained=[], failed=0)
        _log.info("Draining %d un-enriched Item(s)", len(queued), extra={"queued": len(queued)})

        drained: list[str] = []
        failed = 0
        for item in queued:
            try:
                result = enricher.enrich(item.name, category=item.category, size=item.size)
            except Exception:
                observability.record_enrichment_call(outcome="failed", model=enricher.model)
                failed += 1
                _log.exception(
                    "Enrichment failed for SKU %s; leaving it queued for retry", item.sku
                )
                continue
            observability.record_enrichment_call(outcome="ok", model=enricher.model)
            _record_quality(result)
            persist_enrichment(conn, item.sku, result)
            drained.append(item.sku)
            _log.info(
                "Enriched SKU %s",
                item.sku,
                extra={
                    "sku": item.sku,
                    "scientific_name": result.scientific_name,
                    "common_name": result.common_name,
                    "difficulty": result.difficulty.value,
                    "temperament": result.temperament.value,
                    "plant_safe": result.plant_safe.value,
                },
            )
            if rate:
                sleep(rate)
        return DrainResult(drained=drained, failed=failed)


def _record_quality(result: EnrichmentResult) -> None:
    """Fire the honesty-guardrail counters for one result: an unresolved species and any
    ``unknown`` Classifier."""
    if result.scientific_name is None and result.common_name is None:
        observability.record_enrichment_species_unresolved()
    for classifier, value in (
        ("difficulty", result.difficulty),
        ("temperament", result.temperament),
        ("plant_safe", result.plant_safe),
    ):
        if value.value == "unknown":
            observability.record_enrichment_classifier_unknown(classifier=classifier)


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
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """One drainer iteration, surviving any failure to the next poll so the loop never dies."""
    try:
        started = monotonic()
        result = drain_pending(conn, enricher, rate=rate, sleep=sleep)
        duration = monotonic() - started
        if result.drained or result.failed:
            _log.info(
                "Drain pass complete: %d drained, %d failed in %.2fs",
                len(result.drained),
                result.failed,
                duration,
                extra={
                    "drained": len(result.drained),
                    "failed": result.failed,
                    "duration_s": round(duration, 3),
                },
            )
    except Exception:
        _log.exception("Drain pass failed; retrying on next poll")
