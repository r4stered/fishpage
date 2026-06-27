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
from fishpage.images import ImageStore, store_image
from fishpage.imagesource import ImageSource
from fishpage.models import Provenance
from fishpage.store import image_for, persist_enrichment, unenriched_items

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
    image_store: ImageStore | None = None,
    image_source: ImageSource | None = None,
    max_dimension: int = 1024,
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
            if image_store is not None and image_source is not None:
                _acquire_auto_image(
                    conn, image_store, image_source, item.sku, result, max_dimension=max_dimension
                )
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


def _acquire_auto_image(
    conn: sqlite3.Connection,
    image_store: ImageStore,
    image_source: ImageSource,
    sku: str,
    result: EnrichmentResult,
    *,
    max_dimension: int,
) -> None:
    """Fetch and store a sourced lead image for one just-enriched Item, store-confident-only.

    The gate is the spike's: an image is acquired only when the species resolved (non-``None``)
    and the Item is not a strain — a strain's wild-type photo would be the wrong fish — and never
    over an existing ``manual`` image, which is authoritative and un-clobberable. The whole step is
    best-effort: a source that finds nothing storable or one that raises leaves the Item imageless
    on the manual path, and never fails the enrichment that already persisted.
    """
    if result.scientific_name is None or result.strain_specific:
        return
    existing = image_for(conn, sku)
    if existing is not None and existing.provenance is Provenance.MANUAL:
        return
    try:
        sourced = image_source.fetch(result.scientific_name)
        if sourced is None:
            return
        store_image(
            image_store,
            conn,
            sku,
            sourced.data,
            provenance=Provenance.WIKIMEDIA,
            license=sourced.license,
            attribution=sourced.attribution,
            source_url=sourced.source_url,
            max_dimension=max_dimension,
        )
    except Exception:
        _log.exception("Auto-image failed for SKU %s; leaving it on the manual path", sku)


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
    image_store: ImageStore | None = None,
    image_source: ImageSource | None = None,
    max_dimension: int = 1024,
) -> None:
    """Poll the un-enriched queue forever, draining each pass — the drainer's thin trigger.

    Polling rather than an event is deliberate, the same call ingestion makes: there is no latency
    requirement behind filling Classifiers, and a poll loop survives a crash by simply re-reading
    the surviving queue on the next tick. When an image store and source are wired, each pass also
    fills a resolved, non-strain Item's lead image; without them it fills Classifiers only.
    """
    while True:
        _drain_pass(
            conn,
            enricher,
            rate=rate,
            sleep=sleep,
            image_store=image_store,
            image_source=image_source,
            max_dimension=max_dimension,
        )
        sleep(interval)


def _drain_pass(
    conn: sqlite3.Connection,
    enricher: Enricher,
    *,
    rate: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float] = time.monotonic,
    image_store: ImageStore | None = None,
    image_source: ImageSource | None = None,
    max_dimension: int = 1024,
) -> None:
    """One drainer iteration, surviving any failure to the next poll so the loop never dies."""
    try:
        started = monotonic()
        result = drain_pending(
            conn,
            enricher,
            rate=rate,
            sleep=sleep,
            image_store=image_store,
            image_source=image_source,
            max_dimension=max_dimension,
        )
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
