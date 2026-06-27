"""Watched-folder ingestion: a Stocklist PDF dropped into the incoming directory is
parsed and reconciled into the catalog, then moved aside so a re-scan won't re-ingest it."""

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import fishpage.ingest as ingest_mod
from fishpage.config import DEFAULT_PDF, load_settings
from fishpage.ingest import _ingest_pass, build_ingestion_report, ingest_pending, stocklist_date
from fishpage.models import Item
from fishpage.store import all_items, open_store

FIXTURE = DEFAULT_PDF

SETTINGS = load_settings({})


def _item(sku: str, *, retail: str = "9.99", qty: int = 5) -> Item:
    return Item(sku, "M", f"Fish {sku}", Decimal(retail), None, qty)


def _drop(incoming: Path, name: str) -> Path:
    incoming.mkdir(parents=True, exist_ok=True)
    target = incoming / name
    shutil.copy(FIXTURE, target)
    return target


def test_dropped_pdf_is_ingested_into_catalog(tmp_path):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")

    conn = open_store(tmp_path / "fishpage.db")
    ingest_pending(conn, incoming, processed)

    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110012"].name == "African Butterflyfish"
    assert len(stored) == 969


def test_ingested_pdf_is_moved_to_processed(tmp_path):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")

    conn = open_store(tmp_path / "fishpage.db")
    ingest_pending(conn, incoming, processed)

    assert list(incoming.glob("*.pdf")) == []  # the drop is cleared from incoming
    assert (processed / "Freshwater_Stocklist_6-19-26.pdf").is_file()


def test_rescan_with_nothing_pending_is_a_noop(tmp_path):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    drop = _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")

    conn = open_store(tmp_path / "fishpage.db")
    first = ingest_pending(conn, incoming, processed)
    assert first == [drop]  # the one drop is reported as ingested

    again = ingest_pending(conn, incoming, processed)
    assert again == []  # the already-processed drop is not re-ingested


def test_later_drop_reconciles_against_the_catalog(tmp_path):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    conn = open_store(tmp_path / "fishpage.db")

    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    ingest_pending(conn, incoming, processed)

    # A newer Stocklist lands; the date comes from its filename.
    _drop(incoming, "Freshwater_Stocklist_6-26-26.pdf")
    ingest_pending(conn, incoming, processed)

    stored = {item.sku: item for item in all_items(conn)}
    assert len(stored) == 969  # upsert-by-SKU, not a second rebuilt set of rows
    assert stored["110012"].last_seen == date(2026, 6, 26)  # advanced to the newer drop


def test_non_pdf_files_are_left_untouched(tmp_path):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    incoming.joinpath("notes.txt").write_text("not a stocklist")

    conn = open_store(tmp_path / "fishpage.db")
    ingested = ingest_pending(conn, incoming, processed)

    assert [p.name for p in ingested] == ["Freshwater_Stocklist_6-19-26.pdf"]
    assert incoming.joinpath("notes.txt").is_file()  # the stray file is not moved
    assert not processed.joinpath("notes.txt").exists()


def test_multiple_pending_drops_apply_in_stocklist_date_order(tmp_path):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    conn = open_store(tmp_path / "fishpage.db")

    # Both land before the next scan. Filename lexical order ("6-19-26" < "6-9-26")
    # disagrees with calendar order, so name-sorting would reconcile June 19 last by mistake.
    _drop(incoming, "Freshwater_Stocklist_6-9-26.pdf")
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    ingest_pending(conn, incoming, processed)

    stored = {item.sku: item for item in all_items(conn)}
    # The chronologically newest Stocklist (June 19) must win last_seen.
    assert stored["110012"].last_seen == date(2026, 6, 19)


def test_empty_parse_does_not_zero_the_catalog(tmp_path, monkeypatch):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    conn = open_store(tmp_path / "fishpage.db")

    # Seed a full catalog from a real Stocklist (real parse, before patching).
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    ingest_pending(conn, incoming, processed)
    assert len(all_items(conn)) == 969

    # A later drop is still being copied in, so parsing yields no rows.
    monkeypatch.setattr(ingest_mod, "parse_stocklist", lambda _path: [])
    truncated = _drop(incoming, "Freshwater_Stocklist_6-26-26.pdf")
    ingested = ingest_pending(conn, incoming, processed)

    assert ingested == []  # a no-row parse is not reconciled
    stored = {item.sku: item for item in all_items(conn)}
    assert len(stored) == 969
    assert stored["110012"].qty_avail == 10  # not zeroed by a phantom absentee sweep
    assert stored["110012"].last_seen == date(2026, 6, 19)  # untouched
    assert truncated.is_file()  # left in incoming for the next tick


def test_ingest_pass_survives_a_failed_parse(tmp_path, monkeypatch):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    conn = open_store(tmp_path / "fishpage.db")
    drop = _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")

    def boom(_path):
        raise ValueError("truncated xref")

    monkeypatch.setattr(ingest_mod, "parse_stocklist", boom)

    _ingest_pass(conn, incoming, processed)  # one watcher iteration; must not raise

    assert all_items(conn) == []  # nothing ingested
    assert drop.is_file()  # left in incoming for the next tick


def test_ingest_pass_ingests_a_real_drop(tmp_path):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")

    conn = open_store(tmp_path / "fishpage.db")
    _ingest_pass(conn, incoming, processed)

    assert len(all_items(conn)) == 969  # the pass reconciled the drop
    assert (processed / "Freshwater_Stocklist_6-19-26.pdf").is_file()


def test_stocklist_date_reads_the_filename():
    assert stocklist_date(Path("Freshwater_Stocklist_6-19-26.pdf")) == date(2026, 6, 19)


def test_stocklist_date_raises_when_filename_has_no_date():
    with pytest.raises(ValueError, match="no valid Stocklist date in filename"):
        stocklist_date(Path("no_date_here.pdf"))


def test_stocklist_date_raises_on_an_out_of_range_date():
    # The regex matches 13-40-26, but month 13 / day 40 is not a real date.
    with pytest.raises(ValueError, match="no valid Stocklist date in filename"):
        stocklist_date(Path("Freshwater_Stocklist_13-40-26.pdf"))


def test_invalid_date_drop_does_not_wedge_a_valid_one(tmp_path):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    conn = open_store(tmp_path / "fishpage.db")

    # A garbage-dated file sits beside a valid drop in the same pass.
    invalid = _drop(incoming, "Freshwater_Stocklist_13-40-26.pdf")
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    ingested = ingest_pending(conn, incoming, processed)

    assert [p.name for p in ingested] == ["Freshwater_Stocklist_6-19-26.pdf"]  # valid drop applied
    assert len(all_items(conn)) == 969
    assert invalid.is_file()  # the bad file is skipped, not wedging the pass


def test_undated_drop_is_skipped_not_stamped_with_today(tmp_path):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    conn = open_store(tmp_path / "fishpage.db")

    # Seed a full catalog from a properly dated Stocklist.
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    ingest_pending(conn, incoming, processed)

    # A valid Stocklist whose name carries no M-D-YY date must not be reconciled with today().
    undated = _drop(incoming, "stocklist.pdf")
    ingested = ingest_pending(conn, incoming, processed)

    assert ingested == []
    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110012"].last_seen == date(2026, 6, 19)  # not restamped with today's date
    assert undated.is_file()  # left in incoming for the user to rename


def test_older_drop_in_a_later_pass_is_skipped(tmp_path):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    conn = open_store(tmp_path / "fishpage.db")

    # Pass 1: the newer Stocklist lands and is reconciled.
    _drop(incoming, "Freshwater_Stocklist_6-26-26.pdf")
    ingest_pending(conn, incoming, processed)

    # Pass 2: an older Stocklist arrives after the newer one was already applied.
    stale = _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    ingested = ingest_pending(conn, incoming, processed)

    assert ingested == []  # not applied out of order
    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110012"].last_seen == date(2026, 6, 26)  # not regressed
    assert stored["110012"].qty_avail == 10  # not zeroed by a stale absentee sweep
    assert stale.is_file()  # left in incoming, not silently consumed


def test_ingest_emits_a_span(tmp_path, telemetry):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    conn = open_store(tmp_path / "fishpage.db")

    ingest_pending(conn, incoming, processed)

    # A reconcile pass carries its own span, with the nested parse span beneath it.
    names = telemetry.span_names()
    assert "ingest_pending" in names
    assert "parse_stocklist" in names


def test_skipping_an_older_drop_is_counted_as_a_metric(tmp_path, telemetry):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    conn = open_store(tmp_path / "fishpage.db")

    _drop(incoming, "Freshwater_Stocklist_6-26-26.pdf")
    ingest_pending(conn, incoming, processed)  # newer Stocklist applied

    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    ingest_pending(conn, incoming, processed)  # older Stocklist held back

    # The held-back drop is counted, so a misdated supplier export that keeps bouncing off the
    # monotonicity guard is visible as a metric rather than only a log line.
    assert telemetry.counter("fishpage.ingest.monotonicity_skips") == 1


def _seed(conn, items, when):
    """Reconcile a synthetic Stocklist straight into the store, bypassing the parser."""
    from fishpage.store import reconcile

    reconcile(conn, items, when)


def test_report_counts_describe_the_change_against_the_catalog():
    # Prior catalog: A In stock, B out of stock, C In stock.
    stored = [_item("A", retail="9.99", qty=5), _item("B", qty=0), _item("C", qty=5)]
    # New parse: A repriced, B returns In stock, D is brand new, C is absent (would be zeroed).
    parsed = [_item("A", retail="12.99", qty=5), _item("B", qty=3), _item("D", qty=2)]

    report = build_ingestion_report(stored, parsed, SETTINGS)

    assert report.new == 1  # D
    assert report.returned == 1  # B was qty 0, now In stock
    assert report.zeroed == 1  # C was In stock and is absent from the parse
    assert report.price_changed == 1  # A's retail moved
    assert report.flagged == 0
    assert not report.implausible  # one-of-two zeroed is under the 50% default


def test_zero_negative_and_absurd_prices_are_flagged():
    parsed = [
        _item("A", retail="9.99"),
        _item("B", retail="0"),
        _item("C", retail="-1"),
        _item("D", retail="999999"),  # past the 100000 default ceiling
    ]

    report = build_ingestion_report([], parsed, SETTINGS)

    assert report.flagged == 3  # zero, negative, absurd — the well-formed price is not flagged


def test_first_ever_ingest_into_an_empty_catalog_is_never_implausible():
    # No In-stock baseline to swing against, so even a tiny first parse is allowed through.
    report = build_ingestion_report([], [_item("A")], SETTINGS)

    assert report.prior_in_stock == 0
    assert not report.implausible


def test_implausible_parse_is_held_in_incoming_and_not_reconciled(tmp_path, monkeypatch, telemetry):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    conn = open_store(tmp_path / "fishpage.db")

    # Seed a full catalog from a real Stocklist, then a column-shifted parse drops most of the rows.
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    ingest_pending(conn, incoming, processed)
    assert len(all_items(conn)) == 969
    in_stock_before = sum(1 for i in all_items(conn) if i.qty_avail > 0)

    monkeypatch.setattr(ingest_mod, "parse_stocklist", lambda _path: [_item("110012", qty=5)])
    held = _drop(incoming, "Freshwater_Stocklist_6-26-26.pdf")
    ingested = ingest_pending(conn, incoming, processed)

    assert ingested == []  # not reconciled
    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110012"].last_seen == date(2026, 6, 19)  # catalog untouched
    # The absentee sweep never ran, so no live SKU was zeroed.
    assert sum(1 for i in stored.values() if i.qty_avail > 0) == in_stock_before
    assert held.is_file()  # left in incoming for retry, like the empty-parse guard
    assert telemetry.counter("fishpage.ingest.held") == 1  # one alert signal


def test_normal_parse_passes_through_and_reports_its_counts(tmp_path, monkeypatch, telemetry):
    incoming = tmp_path / "incoming"
    processed = tmp_path / "processed"
    conn = open_store(tmp_path / "fishpage.db")
    _seed(conn, [_item("110012", retail="9.99", qty=5)], date(2026, 6, 12))

    # A plausible later parse: keeps the one In-stock SKU (repriced) and adds a new one.
    parsed = [_item("110012", retail="11.99", qty=5), _item("999999", qty=3)]
    monkeypatch.setattr(ingest_mod, "parse_stocklist", lambda _path: parsed)
    _drop(incoming, "Freshwater_Stocklist_6-19-26.pdf")
    ingested = ingest_pending(conn, incoming, processed)

    assert len(ingested) == 1  # reconciled and moved aside
    assert telemetry.counter("fishpage.ingest.held") == 0
    kinds = {attrs["kind"]: value for attrs, value in telemetry.points("fishpage.ingest.report")}
    assert kinds == {"new": 1, "returned": 0, "zeroed": 0, "price_changed": 1, "flagged": 0}
