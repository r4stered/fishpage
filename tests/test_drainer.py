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
from PIL import Image

import fishpage.drainer as drainer
from fishpage.config import load_settings
from fishpage.drainer import backfill_images, drain_pending, run_drainer
from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament
from fishpage.images import StoredImage
from fishpage.imagesource import SourcedImage
from fishpage.models import Item, Provenance
from fishpage.observability import configure_logging
from fishpage.store import (
    attach_image,
    enrichment_for,
    image_for,
    open_store,
    persist_enrichment,
    reconcile,
    unenriched_items,
)


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


def test_run_drainer_backfills_existing_images_once_before_polling(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    # Already enriched but never imaged — the prod state where the queue is otherwise empty.
    persist_enrichment(conn, "110042", RESOLVED)
    store = FakeImageStore()
    source = FakeImageSource(_sourced())

    def stop_after_first(seconds: float) -> None:
        raise _Stop

    with pytest.raises(_Stop):
        run_drainer(
            conn,
            RecordingEnricher(),
            interval=30.0,
            rate=0.0,
            sleep=stop_after_first,
            image_store=store,
            image_source=source,
        )

    # The drainer backfills the already-enriched catalog's images at startup — before the first poll
    # sleep breaks the loop — so a queue that is empty of *un-enriched* Items still collects images.
    record = image_for(conn, "110042")
    assert record is not None and record.provenance is Provenance.WIKIMEDIA


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


def _real_jpeg() -> bytes:
    """A genuine JPEG the store_image optimization seam can decode — a sourced image is real."""
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (40, 160, 90)).save(buf, format="JPEG")
    return buf.getvalue()


SOURCED_JPEG = _real_jpeg()


class StrainEnricher:
    """Resolves a confident species but flags it strain-specific — the wild-type photo is wrong."""

    model = "fake-model"

    def enrich(self, trade_name: str, *, category: str, size: str) -> EnrichmentResult:
        return EnrichmentResult(
            scientific_name="Pterophyllum scalare",
            common_name="Gold Marble Angel",
            difficulty=Difficulty.INTERMEDIATE,
            temperament=Temperament.SEMI_AGGRESSIVE,
            plant_safe=PlantSafe.SAFE,
            strain_specific=True,
        )


class FakeImageStore:
    """An in-memory ImageStore — the bucket the drainer puts a sourced image into, no network."""

    def __init__(self):
        self.objects: dict[str, StoredImage] = {}

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self.objects[key] = StoredImage(data=data, content_type=content_type)

    def get(self, key: str) -> StoredImage | None:
        return self.objects.get(key)


class FakeImageSource:
    """An injectable ImageSource that records the species it was asked for and returns a canned
    result — a sourced image, or ``None`` for the honest gap. The suite never hits Wikimedia."""

    def __init__(self, result: SourcedImage | None):
        self._result = result
        self.species: list[str] = []

    def fetch(self, species: str) -> SourcedImage | None:
        self.species.append(species)
        return self._result


def _sourced() -> SourcedImage:
    return SourcedImage(
        data=SOURCED_JPEG,
        license="CC BY-SA 4.0",
        attribution="A. Photographer",
        source_url="https://commons.wikimedia.org/wiki/File:Fish.jpg",
    )


def test_drain_stores_a_wikimedia_image_for_a_resolved_wild_type(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    store = FakeImageStore()
    source = FakeImageSource(_sourced())

    drain_pending(conn, ResolvedEnricher(), image_store=store, image_source=source)

    # A resolved, non-strain species is the store-confident case: the drainer keys the source off
    # the resolved species and stores the result with wikimedia Provenance plus its licence,
    # attribution, and source URL — the bytes in the bucket, only the metadata in the DB.
    assert source.species == ["Polypterus ornatipinnis"]
    record = image_for(conn, "110042")
    assert record is not None
    assert record.provenance is Provenance.WIKIMEDIA
    assert record.license == "CC BY-SA 4.0"
    assert record.attribution == "A. Photographer"
    assert record.source_url == "https://commons.wikimedia.org/wiki/File:Fish.jpg"
    assert Image.open(io.BytesIO(store.objects["110042"].data)).format == "WEBP"


def test_drain_skips_the_auto_image_for_a_strain(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    store = FakeImageStore()
    source = FakeImageSource(_sourced())

    drain_pending(conn, StrainEnricher(), image_store=store, image_source=source)

    # A strain resolves to a real species with confidence, but its wild-type photo is the wrong
    # fish — so the gate never even queries the source, and no image is stored.
    assert source.species == []
    assert image_for(conn, "110042") is None
    assert store.objects == {}


def test_drain_skips_the_auto_image_for_an_unresolved_species(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    store = FakeImageStore()
    source = FakeImageSource(_sourced())

    drain_pending(conn, GappyEnricher(), image_store=store, image_source=source)

    # No species resolved — the honest gap. No query, no image; the Item stays on the manual path.
    assert source.species == []
    assert image_for(conn, "110042") is None


def test_drain_never_clobbers_a_manual_image(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    # A human already uploaded an image for this Item before it was enriched.
    attach_image(conn, "110042", object_key="110042", provenance=Provenance.MANUAL)
    store = FakeImageStore()
    source = FakeImageSource(_sourced())

    drain_pending(conn, ResolvedEnricher(), image_store=store, image_source=source)

    # A manual image is authoritative and un-clobberable: even a resolved wild-type does not
    # overwrite it, and the source is never queried for a SKU that already has the human's image.
    assert source.species == []
    record = image_for(conn, "110042")
    assert record is not None and record.provenance is Provenance.MANUAL
    assert record.object_key == "110042"


def test_drain_leaves_no_image_when_the_source_finds_none(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    store = FakeImageStore()
    source = FakeImageSource(None)  # no usable, commercial-free image for this species

    result = drain_pending(conn, ResolvedEnricher(), image_store=store, image_source=source)

    # The source found nothing storable; the Item is still enriched and counted as drained — the
    # missing image is an accepted outcome, not a failure.
    assert source.species == ["Polypterus ornatipinnis"]
    assert image_for(conn, "110042") is None
    assert result.drained == ["110042"]


def test_drain_without_an_image_source_only_fills_classifiers(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    # The auto-image path is opt-in: with no store/source wired (the default), the drainer fills
    # Classifiers exactly as before and attaches no image.
    drain_pending(conn, ResolvedEnricher())

    assert enrichment_for(conn, "110042") is not None
    assert image_for(conn, "110042") is None


RESOLVED = EnrichmentResult(
    scientific_name="Polypterus ornatipinnis",
    common_name="Ornate Bichir",
    difficulty=Difficulty.INTERMEDIATE,
    temperament=Temperament.SEMI_AGGRESSIVE,
    plant_safe=PlantSafe.SAFE,
    strain_specific=False,
)


def test_backfill_images_stores_an_image_for_each_pending_enriched_item(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    # Two Items already enriched to a storable species but never imaged — the prod backfill case
    # after enrichment ran before the image source existed.
    persist_enrichment(conn, "110042", RESOLVED)
    persist_enrichment(conn, "110092", RESOLVED)
    store = FakeImageStore()
    source = FakeImageSource(_sourced())

    landed = backfill_images(conn, store, source)

    # The backfill keys the source off each Item's stored species and stores a wikimedia image — no
    # enricher and no LLM call in sight, so an already-enriched catalog gets images for free.
    assert source.species == ["Polypterus ornatipinnis", "Polypterus ornatipinnis"]
    first = image_for(conn, "110042")
    second = image_for(conn, "110092")
    assert first is not None and first.provenance is Provenance.WIKIMEDIA
    assert second is not None and second.provenance is Provenance.WIKIMEDIA
    assert landed == 2


def test_backfill_images_is_a_noop_on_an_empty_queue(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)  # un-enriched: not in the image backfill queue
    source = FakeImageSource(_sourced())

    landed = backfill_images(conn, FakeImageStore(), source)

    # Nothing enriched-but-imageless means nothing to fetch — the source is never queried.
    assert source.species == []
    assert landed == 0


def test_backfill_images_paces_calls_with_the_injected_sleeper(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    persist_enrichment(conn, "110042", RESOLVED)
    persist_enrichment(conn, "110092", RESOLVED)
    pauses: list[float] = []

    backfill_images(
        conn, FakeImageStore(), FakeImageSource(_sourced()), rate=0.5, sleep=pauses.append
    )

    # Each fetch is several Wikimedia round-trips, so the backfill rate-limits itself between Items
    # rather than firing ~900 fetches at the API at once.
    assert pauses == [0.5, 0.5]


def test_backfill_images_skips_an_item_that_already_has_an_image(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    persist_enrichment(conn, "110042", RESOLVED)
    attach_image(conn, "110042", object_key="110042", provenance=Provenance.MANUAL)
    source = FakeImageSource(_sourced())

    backfill_images(conn, FakeImageStore(), source)

    # An Item with an image — here a manual upload — is not in the queue, so the backfill never
    # touches it and the human's image stands.
    assert source.species == []
    record = image_for(conn, "110042")
    assert record is not None and record.provenance is Provenance.MANUAL


def test_an_auto_image_failure_does_not_fail_the_enrichment(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    store = FakeImageStore()

    class BoomSource:
        def fetch(self, species: str) -> SourcedImage | None:
            raise RuntimeError("wikimedia blew up")

    result = drain_pending(conn, ResolvedEnricher(), image_store=store, image_source=BoomSource())

    # A source that raises is best-effort failure: the Classifiers already persisted, the SKU still
    # counts as drained, and no image lands — the image step never takes the enrichment down.
    assert result.drained == ["110042"]
    assert result.failed == 0
    assert enrichment_for(conn, "110042") is not None
    assert image_for(conn, "110042") is None
