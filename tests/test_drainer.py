"""The decoupled enrichment drainer: a paced background pass that fills the un-enriched queue.

These tests drive the drainer through an injected fake :class:`~fishpage.enricher.Enricher`, so the
queue-and-persist orchestration is exercised with no key and no network — the drainer is opt-in and
default-off, and the suite never reaches for a credential.
"""

from datetime import date
from decimal import Decimal

import pytest

import fishpage.drainer as drainer
from fishpage.drainer import drain_pending, run_drainer
from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament
from fishpage.models import Item
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
    )


class RecordingEnricher:
    """An injectable Enricher that echoes the trade name back and records each call."""

    def __init__(self):
        self.calls: list[dict] = []

    def enrich(self, trade_name: str, *, category: str, size: str) -> EnrichmentResult:
        self.calls.append({"trade_name": trade_name, "category": category, "size": size})
        return _result(common_name=trade_name)


class FlakyEnricher:
    """Raises for one SKU's trade name, enriches the rest — a mid-batch failure that can heal."""

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

    drained = drain_pending(conn, RecordingEnricher())

    # Every queued SKU is enriched and persisted in one pass; the queue empties and the returned
    # SKUs are exactly the ones drained.
    assert set(drained) == {"110042", "110092"}
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

    drained = drain_pending(conn, enricher)

    # One SKU's enrichment blew up; the other still persisted, and the failed SKU stays queued
    # rather than aborting the whole pass.
    assert set(drained) == {"110042"}
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
