from dataclasses import replace
from datetime import date
from decimal import Decimal

from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament
from fishpage.migrations import MIGRATIONS, schema_version
from fishpage.models import ImageRecord, Item, Provenance
from fishpage.store import (
    all_items,
    attach_image,
    clear_enrichment,
    enrichment_for,
    image_for,
    latest_stocklist_date,
    open_store,
    persist_enrichment,
    reconcile,
    unenriched_items,
)

JUN19 = date(2026, 6, 19)
JUN26 = date(2026, 6, 26)

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish Leopard Ctenopoma", Decimal("5.99"), Decimal("4.99"), 30)
SOLD_OUT = Item("110200", "L", "Datnoid Indo", Decimal("89.99"), None, 0)

ORNATE_ENRICHMENT = EnrichmentResult(
    scientific_name="Polypterus ornatipinnis",
    common_name="Ornate Bichir",
    difficulty=Difficulty.INTERMEDIATE,
    temperament=Temperament.SEMI_AGGRESSIVE,
    plant_safe=PlantSafe.SAFE,
)


def test_the_store_opens_in_wal_mode_so_litestream_can_replicate(tmp_path):
    # Litestream streams the write-ahead log; a database in the default rollback-journal mode
    # produces nothing for it to replicate. WAL is set on open so every store is replicable.
    conn = open_store(tmp_path / "fishpage.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_open_store_runs_migrations_and_stamps_the_schema_version(tmp_path):
    # Opening the store applies the migration runner, so a fresh database lands at the latest
    # version rather than at the un-stamped 0 an ad-hoc CREATE TABLE would leave.
    conn = open_store(tmp_path / "fishpage.db")
    assert schema_version(conn) == MIGRATIONS[-1][0]


def test_latest_stocklist_date_is_none_when_empty(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    assert latest_stocklist_date(conn) is None


def test_latest_stocklist_date_is_the_newest_reconciled_date(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    reconcile(conn, [ORNATE_M], JUN26)
    assert latest_stocklist_date(conn) == JUN26


def test_items_round_trip_through_sqlite(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    stored = {item.sku: item for item in all_items(conn)}

    assert stored["110042"] == replace(ORNATE_M, last_seen=JUN19)
    assert stored["110092"] == replace(LEAF, last_seen=JUN19)


def test_all_items_excludes_out_of_stock_when_asked(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF, SOLD_OUT], JUN19)

    in_stock = all_items(conn, include_out_of_stock=False)

    assert {item.sku for item in in_stock} == {"110042", "110092"}


def test_store_is_keyed_by_sku(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    stored = all_items(conn)
    assert {item.sku for item in stored} == {"110042", "110092"}
    assert len(stored) == 2


def test_freshly_reconciled_skus_are_unenriched(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    # Ingestion upserts items but writes no enrichment row, so every new SKU is un-enriched —
    # that set is the drainer's work queue. The queue yields whole Items so the drainer has the
    # trade name, Derived Category, and Size it feeds the enricher.
    queued = unenriched_items(conn)
    assert {item.sku for item in queued} == {"110042", "110092"}
    assert any(item.name == "Bichir Ornate" for item in queued)


def test_persisting_enrichment_removes_a_sku_from_the_queue(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    persist_enrichment(conn, "110042", ORNATE_ENRICHMENT)

    # Filling one SKU shrinks the queue to exactly the rest, and the persisted result reads back
    # intact (species + Classifiers), while an unfilled SKU has no enrichment to read.
    assert {item.sku for item in unenriched_items(conn)} == {"110092"}
    assert enrichment_for(conn, "110042") == ORNATE_ENRICHMENT
    assert enrichment_for(conn, "110092") is None


def test_clearing_enrichment_requeues_the_sku(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    persist_enrichment(conn, "110042", ORNATE_ENRICHMENT)
    assert "110042" not in {item.sku for item in unenriched_items(conn)}

    clear_enrichment(conn, "110042")

    # An on-demand re-enrich clears the AI row, dropping the SKU back into the queue for a fresh
    # pass; its enrichment reads back as gone.
    assert "110042" in {item.sku for item in unenriched_items(conn)}
    assert enrichment_for(conn, "110042") is None


def test_attaching_a_manual_image_records_manual_provenance(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    attach_image(
        conn,
        "110042",
        object_key="img/110042.jpg",
        license="CC-BY-4.0",
        attribution="A. Photographer",
        source_url="https://example.org/ornate",
    )

    # The object key plus license/attribution/source read back intact, and the Provenance defaults
    # to manual — a human attached it. An Item with no image reads back as None.
    assert image_for(conn, "110042") == ImageRecord(
        object_key="img/110042.jpg",
        license="CC-BY-4.0",
        attribution="A. Photographer",
        source_url="https://example.org/ornate",
        provenance=Provenance.MANUAL,
    )
    assert image_for(conn, "110092") is None


def test_re_attaching_an_image_replaces_the_prior_one_for_that_sku(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    attach_image(conn, "110042", object_key="img/old.jpg")

    attach_image(conn, "110042", object_key="img/new.jpg")

    # One image per Item: a fresh upload supersedes the prior key rather than accumulating rows.
    record = image_for(conn, "110042")
    assert record is not None and record.object_key == "img/new.jpg"


def test_re_enrich_leaves_a_manual_image_untouched(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    persist_enrichment(conn, "110042", ORNATE_ENRICHMENT)
    attach_image(conn, "110042", object_key="img/110042.jpg")  # manual by default

    clear_enrichment(conn, "110042")

    # An on-demand re-enrich clears the AI row but a human's manual image survives — it lives in a
    # separate table the re-enrich never touches, the same un-clobberable guarantee as an override.
    assert enrichment_for(conn, "110042") is None
    record = image_for(conn, "110042")
    assert record is not None and record.object_key == "img/110042.jpg"


def test_re_enrich_clears_a_sourced_image_so_it_is_re_fetched(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    attach_image(conn, "110042", object_key="img/sourced.jpg", provenance=Provenance.WIKIMEDIA)

    clear_enrichment(conn, "110042")

    # A best-effort sourced image is re-enrichable, so re-enrich drops it; only manual is sticky.
    assert image_for(conn, "110042") is None


def test_persisting_enrichment_leaves_a_manual_override_intact(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    # A human corrected one Classifier: that ``manual`` value lives in classifier_override, the
    # authoritative layer the drainer must never clobber.
    conn.execute(
        "INSERT INTO classifier_override (sku, key, value) VALUES (?, ?, ?)",
        ("110042", "temperament", "peaceful"),
    )
    conn.commit()

    # Re-enrich with a *different* AI temperament for the same SKU.
    persist_enrichment(
        conn, "110042", replace(ORNATE_ENRICHMENT, temperament=Temperament.AGGRESSIVE)
    )

    # The AI row updates, but the manual override survives untouched — it wins because it lives in
    # a separate table the persist path never writes.
    override = conn.execute(
        "SELECT value FROM classifier_override WHERE sku = ? AND key = ?",
        ("110042", "temperament"),
    ).fetchone()
    assert override["value"] == "peaceful"
    reenriched = enrichment_for(conn, "110042")
    assert reenriched is not None and reenriched.temperament is Temperament.AGGRESSIVE
