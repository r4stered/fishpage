from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from fishpage.enricher import Difficulty, EnrichmentResult, PlantSafe, Temperament
from fishpage.migrations import MIGRATIONS, schema_version
from fishpage.models import ImageRecord, Item, Provenance
from fishpage.store import (
    add_to_pick_list,
    all_classifier_overrides,
    all_enrichments,
    all_images,
    all_items,
    attach_image,
    clear_enrichment,
    enrichment_for,
    image_for,
    latest_stocklist_date,
    open_store,
    persist_enrichment,
    pick_list_for,
    reconcile,
    remove_from_pick_list,
    set_classifier_override,
    set_pick_list_quantity,
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


def test_a_manual_classifier_override_round_trips(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    set_classifier_override(conn, "110042", "difficulty", "beginner")

    # The correction is read back keyed by Classifier; its presence in this table *is* manual
    # Provenance — the resolve-on-read layer needs nothing more to mark it a human fact.
    assert all_classifier_overrides(conn) == {"110042": {"difficulty": "beginner"}}


def test_overriding_the_same_classifier_again_replaces_the_value(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    set_classifier_override(conn, "110042", "difficulty", "beginner")
    set_classifier_override(conn, "110042", "difficulty", "advanced")

    # A second correction supersedes the first rather than accumulating rows — one human value per
    # Classifier per SKU, the table's (sku, key) primary key.
    assert all_classifier_overrides(conn) == {"110042": {"difficulty": "advanced"}}


def test_an_override_outside_the_vocabulary_is_rejected(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    # A value with no enum member, or an unknown Classifier key, can never reach the catalog — the
    # store refuses it, the same out-of-vocabulary-is-impossible guarantee the AI path has.
    with pytest.raises(ValueError):
        set_classifier_override(conn, "110042", "difficulty", "trivial")
    with pytest.raises(ValueError):
        set_classifier_override(conn, "110042", "color", "blue")
    assert all_classifier_overrides(conn) == {}


def test_all_enrichments_reads_every_persisted_ai_row_keyed_by_sku(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    persist_enrichment(conn, "110042", ORNATE_ENRICHMENT)

    # The grid resolves Classifiers for every visible card at once, so the store hands back all AI
    # rows in one read keyed by SKU — an un-enriched SKU is simply absent, not a None entry.
    enrichments = all_enrichments(conn)
    assert enrichments == {"110042": ORNATE_ENRICHMENT}


def test_all_images_reads_every_image_record_keyed_by_sku(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    attach_image(
        conn,
        "110042",
        object_key="img/110042.webp",
        attribution="A. Photographer",
        provenance=Provenance.WIKIMEDIA,
    )

    # One batch read gives the grid each card's image metadata — the key it proxies and the
    # attribution it must credit — without a per-card query.
    images = all_images(conn)
    assert set(images) == {"110042"}
    assert images["110042"].attribution == "A. Photographer"
    assert images["110042"].provenance is Provenance.WIKIMEDIA


def test_all_classifier_overrides_groups_corrections_by_sku(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    set_classifier_override(conn, "110042", "difficulty", "beginner")
    set_classifier_override(conn, "110042", "temperament", "peaceful")
    set_classifier_override(conn, "110092", "plant_safe", "unsafe")

    # The grid resolves manual Provenance for every card at once, so corrections come back grouped
    # by SKU — each SKU's inner dict is exactly what resolve_classifiers takes.
    overrides = all_classifier_overrides(conn)
    assert overrides == {
        "110042": {"difficulty": "beginner", "temperament": "peaceful"},
        "110092": {"plant_safe": "unsafe"},
    }


def test_add_to_pick_list_puts_a_line_on_the_actors_list_at_quantity_one(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    add_to_pick_list(conn, "buyer@sdc.test", "110042")

    lines = pick_list_for(conn, "buyer@sdc.test")
    assert [(line.item.sku, line.quantity) for line in lines] == [("110042", 1)]


def test_adding_an_item_already_on_the_list_is_idempotent(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    add_to_pick_list(conn, "buyer@sdc.test", "110042")
    set_pick_list_quantity(conn, "buyer@sdc.test", "110042", 5)

    add_to_pick_list(conn, "buyer@sdc.test", "110042")

    # A second add of the same SKU neither duplicates the line nor resets the quantity the buyer
    # already chose — the composite key collides and the existing line stands.
    lines = pick_list_for(conn, "buyer@sdc.test")
    assert [(line.item.sku, line.quantity) for line in lines] == [("110042", 5)]


def test_a_pick_line_carries_name_and_effective_price(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [LEAF], JUN19)  # LEAF has a Special price
    add_to_pick_list(conn, "buyer@sdc.test", "110092")
    set_pick_list_quantity(conn, "buyer@sdc.test", "110092", 3)

    [line] = pick_list_for(conn, "buyer@sdc.test")

    # The line shows the Item's name and the price that actually applies — the Special price wins —
    # and the line total is that effective price times the quantity.
    assert line.item.name == "Leaf Fish Leopard Ctenopoma"
    assert line.item.effective_price == Decimal("4.99")
    assert line.line_total == Decimal("14.97")


def test_setting_quantity_changes_the_line(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    add_to_pick_list(conn, "buyer@sdc.test", "110042")

    set_pick_list_quantity(conn, "buyer@sdc.test", "110042", 7)

    [line] = pick_list_for(conn, "buyer@sdc.test")
    assert line.quantity == 7


def test_removing_a_line_drops_it_from_the_list(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    add_to_pick_list(conn, "buyer@sdc.test", "110042")
    add_to_pick_list(conn, "buyer@sdc.test", "110092")

    remove_from_pick_list(conn, "buyer@sdc.test", "110042")

    lines = pick_list_for(conn, "buyer@sdc.test")
    assert [line.item.sku for line in lines] == ["110092"]


def test_one_actor_never_sees_anothers_pick_list(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    add_to_pick_list(conn, "alice@sdc.test", "110042")
    add_to_pick_list(conn, "bob@sdc.test", "110092")

    # The list is keyed by Actor, so each buyer's gathered Items are theirs alone — Bob's add never
    # bleeds into Alice's list and vice versa.
    assert [line.item.sku for line in pick_list_for(conn, "alice@sdc.test")] == ["110042"]
    assert [line.item.sku for line in pick_list_for(conn, "bob@sdc.test")] == ["110092"]
