from datetime import date
from decimal import Decimal

from fishpage import observability
from fishpage.config import load_settings
from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament
from fishpage.models import Item
from fishpage.store import open_store, persist_enrichment, reconcile

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
TIGER_M = Item("110043", "M", "Bichir Tiger", Decimal("31.99"), None, 8)

AN_ENRICHMENT = EnrichmentResult(
    scientific_name="Polypterus ornatipinnis",
    common_name="Ornate Bichir",
    difficulty=Difficulty.INTERMEDIATE,
    temperament=Temperament.SEMI_AGGRESSIVE,
    plant_safe=PlantSafe.SAFE,
    strain_specific=False,
)


def test_configure_is_a_noop_when_no_exporter_endpoint_is_configured():
    # Bare `just run` and CI: no OTLP endpoint, so nothing is exported and the call reports it
    # did not start an exporter. Instruments still record into in-process providers, so the
    # domain code can call them unconditionally.
    assert observability.configure(load_settings({})) is False


def test_configure_starts_the_exporter_when_an_endpoint_is_configured():
    settings = load_settings({"OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example:4318"})

    # An endpoint is set, so export is wired up. The OTLP exporter connects lazily on first
    # flush, so configuring against an unreachable host neither blocks nor raises here.
    assert observability.configure(settings) is True


def test_catalog_freshness_gauge_reports_days_since_last_ingest(tmp_path, telemetry):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], date(2026, 6, 19))

    # The gauge the staleness alert keys on: how many days since the newest Stocklist applied.
    # The clock is injected so the elapsed days are pinned, not relative to the wall clock.
    observability.track_catalog_freshness(conn, today=lambda: date(2026, 6, 24))

    assert telemetry.counter("fishpage.catalog.days_since_last_ingest") == 5


def test_catalog_freshness_gauge_reports_nothing_for_an_empty_catalog(tmp_path, telemetry):
    conn = open_store(tmp_path / "fishpage.db")  # never ingested

    observability.track_catalog_freshness(conn, today=lambda: date(2026, 6, 24))

    # No "days since" exists when nothing has ever been ingested; the gauge stays silent and the
    # alert treats the absence as the stale signal rather than reporting a bogus 0 days.
    assert telemetry.counter("fishpage.catalog.days_since_last_ingest") == 0
    assert "fishpage.catalog.days_since_last_ingest" not in telemetry.metric_names()


def test_queue_depth_gauge_reports_the_count_of_unenriched_items(tmp_path, telemetry):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, TIGER_M], date(2026, 6, 19))
    # One of the two Items has been enriched, so the queue is one Item deep.
    persist_enrichment(conn, ORNATE_M.sku, AN_ENRICHMENT)

    observability.track_enrichment_queue_depth(conn)

    assert telemetry.counter("fishpage.enrichment.queue_depth") == 1


def test_queue_depth_gauge_reports_zero_for_a_fully_drained_catalog(tmp_path, telemetry):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], date(2026, 6, 19))
    persist_enrichment(conn, ORNATE_M.sku, AN_ENRICHMENT)

    observability.track_enrichment_queue_depth(conn)

    # A populated catalog with nothing left to enrich is a true zero — the drainer caught up — not
    # the missing-data signal an empty catalog gives.
    assert telemetry.counter("fishpage.enrichment.queue_depth") == 0
    assert "fishpage.enrichment.queue_depth" in telemetry.metric_names()


def test_queue_depth_gauge_reports_nothing_for_an_empty_catalog(tmp_path, telemetry):
    conn = open_store(tmp_path / "fishpage.db")  # never ingested

    observability.track_enrichment_queue_depth(conn)

    # No queue exists when nothing has ever been ingested; the gauge stays silent so Grafana reads
    # the absence as missing data rather than a bogus empty queue.
    assert telemetry.counter("fishpage.enrichment.queue_depth") == 0
    assert "fishpage.enrichment.queue_depth" not in telemetry.metric_names()
