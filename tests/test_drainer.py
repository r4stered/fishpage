"""The decoupled enrichment drainer: a paced background pass that fills the un-enriched queue.

These tests drive the drainer through an injected fake :class:`~fishpage.enricher.Enricher`, so the
queue-and-persist orchestration is exercised with no key and no network — the drainer is opt-in and
default-off, and the suite never reaches for a credential.
"""

import io
import json
import logging
from datetime import date
from decimal import Decimal

import pytest

import fishpage.drainer as drainer
from fishpage.config import load_settings
from fishpage.drainer import drain_pending, run_drainer
from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament
from fishpage.models import Item
from fishpage.observability import configure_logging
from fishpage.store import enrichment_for, open_store, reconcile, unenriched_items


class _Stop(Exception):
    """Raised from the injected sleeper to break the otherwise-infinite drain loop in a test."""


JUN19 = date(2026, 6, 19)
ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish", Decimal("5.99"), Decimal("4.99"), 30)


def _result(common_name: str) -> EnrichmentResult:
    return EnrichmentResult(
        scientific_name=None,
        common_name=common_name,
        difficulty=Difficulty.UNKNOWN,
        temperament=Temperament.UNKNOWN,
        plant_safe=PlantSafe.UNKNOWN,
        strain_specific=False,
    )


class RecordingEnricher:
    """An injectable Enricher that echoes the trade name back and records each call."""

    model = "fake-model"

    def __init__(self):
        self.calls: list[dict] = []

    def enrich(self, trade_name: str, *, category: str, size: str) -> EnrichmentResult:
        self.calls.append({"trade_name": trade_name, "category": category, "size": size})
        return _result(common_name=trade_name)


class FlakyEnricher:
    """Raises for one SKU's trade name, enriches the rest — a mid-batch failure that can heal."""

    model = "fake-model"

    def __init__(self, fail_name: str):
        self._fail_name = fail_name
        self.healed = False

    def enrich(self, trade_name: str, *, category: str, size: str) -> EnrichmentResult:
        if trade_name == self._fail_name and not self.healed:
            raise RuntimeError("enrichment API blew up")
        return _result(common_name=trade_name)


def test_drain_pending_fills_the_whole_queue(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    result = drain_pending(conn, RecordingEnricher())

    # Every queued SKU is enriched and persisted in one pass; the queue empties and the returned
    # SKUs are exactly the ones drained.
    assert set(result.drained) == {"110042", "110092"}
    assert result.failed == 0
    assert unenriched_items(conn) == []
    enriched = enrichment_for(conn, "110042")
    assert enriched is not None and enriched.common_name == "Bichir Ornate"


def test_drain_pending_feeds_the_trade_name_category_and_size(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    enricher = RecordingEnricher()

    drain_pending(conn, enricher)

    # The drainer feeds the enricher the Item's trade name plus its Derived Category and Size —
    # the same triple the model is prompted on.
    (call,) = enricher.calls
    assert call == {"trade_name": "Bichir Ornate", "category": ORNATE_M.category, "size": "M"}


def test_drain_pending_survives_a_mid_batch_failure_and_a_restart_resumes(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    enricher = FlakyEnricher(fail_name="Leaf Fish")

    result = drain_pending(conn, enricher)

    # One SKU's enrichment blew up; the other still persisted, and the failed SKU stays queued
    # rather than aborting the whole pass.
    assert set(result.drained) == {"110042"}
    assert result.failed == 1
    assert {item.sku for item in unenriched_items(conn)} == {"110092"}

    # A later pass — the restart — picks up exactly the survivor and finishes the batch.
    enricher.healed = True
    drain_pending(conn, enricher)
    assert unenriched_items(conn) == []


def test_drain_pending_paces_calls_with_the_injected_sleeper(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    pauses: list[float] = []

    drain_pending(conn, RecordingEnricher(), rate=0.5, sleep=pauses.append)

    # Each enrichment is a network round-trip, so the pass rate-limits itself between SKUs rather
    # than firing the whole queue at the API at once.
    assert pauses == [0.5, 0.5]


def test_run_drainer_drains_a_pass_then_sleeps_the_interval(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    pauses: list[float] = []

    def stop_after_first(seconds: float) -> None:
        pauses.append(seconds)
        raise _Stop

    # The poll loop runs one drain pass, emptying the queue, then sleeps the inter-pass interval —
    # which the injected sleeper turns into a clean break instead of looping forever.
    with pytest.raises(_Stop):
        run_drainer(conn, RecordingEnricher(), interval=30.0, rate=0.0, sleep=stop_after_first)

    assert unenriched_items(conn) == []
    assert pauses == [30.0]


def test_run_drainer_survives_a_failed_pass_and_keeps_polling(tmp_path, monkeypatch):
    conn = open_store(tmp_path / "fishpage.db")

    def boom(*args, **kwargs):
        raise RuntimeError("queue read blew up")

    monkeypatch.setattr(drainer, "drain_pending", boom)

    def stop_after_first(seconds: float) -> None:
        raise _Stop

    # A pass that throws is swallowed, so the loop reaches its sleep and would poll again rather
    # than dying — one bad pass never takes the drainer down.
    with pytest.raises(_Stop):
        run_drainer(conn, RecordingEnricher(), interval=30.0, sleep=stop_after_first)


class GappyEnricher:
    """Returns a result with no species and every Classifier unknown — the honesty-guardrail gap."""

    model = "fake-model"

    def enrich(self, trade_name: str, *, category: str, size: str) -> EnrichmentResult:
        return EnrichmentResult(
            scientific_name=None,
            common_name=None,
            difficulty=Difficulty.UNKNOWN,
            temperament=Temperament.UNKNOWN,
            plant_safe=PlantSafe.UNKNOWN,
            strain_specific=False,
        )


class ResolvedEnricher:
    """Returns a fully resolved result — a species and confident Classifiers, no gaps."""

    model = "fake-model"

    def enrich(self, trade_name: str, *, category: str, size: str) -> EnrichmentResult:
        return EnrichmentResult(
            scientific_name="Polypterus ornatipinnis",
            common_name="Ornate Bichir",
            difficulty=Difficulty.INTERMEDIATE,
            temperament=Temperament.SEMI_AGGRESSIVE,
            plant_safe=PlantSafe.SAFE,
            strain_specific=False,
        )


@pytest.fixture
def log_stream():
    """Render the fishpage logger to an in-memory JSON stream, restoring it afterward.

    Configures the same JSON handler the app installs, so a test reads the structured fields the
    drainer actually emits rather than asserting against a mock.
    """
    logger = logging.getLogger("fishpage")
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    stream = io.StringIO()
    configure_logging(load_settings({}), stream=stream)
    yield stream
    logger.handlers[:] = saved_handlers
    logger.setLevel(saved_level)


def _lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_each_enriched_sku_logs_its_species_and_classifiers(tmp_path, log_stream):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    drain_pending(conn, ResolvedEnricher())

    # The per-SKU success line carries the resolved species and the three Classifiers as
    # structured fields, so a fresh batch enriches visibly rather than silently.
    (line,) = [li for li in _lines(log_stream) if li.get("sku") == "110042"]
    assert line["level"] == "INFO"
    assert line["scientific_name"] == "Polypterus ornatipinnis"
    assert line["common_name"] == "Ornate Bichir"
    assert line["difficulty"] == "intermediate"
    assert line["temperament"] == "semi_aggressive"
    assert line["plant_safe"] == "safe"


def test_a_pass_with_work_logs_a_start_line(tmp_path, log_stream):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    drain_pending(conn, RecordingEnricher())

    # A non-empty queue announces itself once at the top of the pass with the queue depth.
    (start,) = [li for li in _lines(log_stream) if "Draining" in li["message"]]
    assert start["queued"] == 2


def test_an_empty_pass_logs_nothing(tmp_path, log_stream):
    conn = open_store(tmp_path / "fishpage.db")  # nothing queued

    drain_pending(conn, RecordingEnricher())

    # An empty queue stays silent so the 30s poll does not flood the logs with empty passes.
    assert _lines(log_stream) == []


def test_end_of_pass_summary_reports_counts_and_duration_not_a_sku_list(tmp_path, log_stream):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    def stop_after_first(seconds: float) -> None:
        raise _Stop

    enricher = FlakyEnricher(fail_name="Leaf Fish")
    with pytest.raises(_Stop):
        run_drainer(conn, enricher, interval=30.0, rate=0.0, sleep=stop_after_first)

    # The end-of-pass summary is counts plus duration, no longer the now-redundant SKU list.
    (summary,) = [li for li in _lines(log_stream) if "Drain pass complete" in li["message"]]
    assert summary["drained"] == 1
    assert summary["failed"] == 1
    assert "duration_s" in summary
    assert "110042" not in summary["message"]


def test_calls_counter_is_tagged_by_outcome_and_model(tmp_path, telemetry):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    drain_pending(conn, FlakyEnricher(fail_name="Leaf Fish"))

    # One SKU landed and one blew up, each tagged by outcome and the enricher's model — the
    # failure rate reads off this one counter.
    by_attrs = {
        frozenset(attrs.items()): value
        for attrs, value in telemetry.points("fishpage.enrichment.calls")
    }
    assert by_attrs[frozenset({"outcome": "ok", "model": "fake-model"}.items())] == 1
    assert by_attrs[frozenset({"outcome": "failed", "model": "fake-model"}.items())] == 1


def test_species_unresolved_counter_fires_on_an_honest_gap(tmp_path, telemetry):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    drain_pending(conn, GappyEnricher())

    # A call that resolves no species increments the early-warning counter for degrading
    # name-resolution.
    assert telemetry.counter("fishpage.enrichment.species_unresolved") == 1


def test_a_resolved_species_does_not_touch_the_unresolved_counter(tmp_path, telemetry):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    drain_pending(conn, ResolvedEnricher())

    assert "fishpage.enrichment.species_unresolved" not in telemetry.metric_names()


def test_classifier_unknown_counter_fires_per_classifier(tmp_path, telemetry):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    drain_pending(conn, GappyEnricher())

    # Every Classifier came back unknown, each tagged by which Classifier it was, so a single
    # attribute degrading shows up rather than hiding in an aggregate.
    by_attrs = {
        frozenset(attrs.items()): value
        for attrs, value in telemetry.points("fishpage.enrichment.classifier_unknown")
    }
    assert by_attrs[frozenset({"classifier": "difficulty"}.items())] == 1
    assert by_attrs[frozenset({"classifier": "temperament"}.items())] == 1
    assert by_attrs[frozenset({"classifier": "plant_safe"}.items())] == 1


def test_confident_classifiers_do_not_touch_the_unknown_counter(tmp_path, telemetry):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    drain_pending(conn, ResolvedEnricher())

    assert "fishpage.enrichment.classifier_unknown" not in telemetry.metric_names()
