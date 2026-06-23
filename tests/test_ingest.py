"""Watched-folder ingestion: a Stocklist PDF dropped into the incoming directory is
parsed and reconciled into the catalog, then moved aside so a re-scan won't re-ingest it."""

import shutil
from datetime import date
from pathlib import Path

from fishpage.ingest import ingest_pending
from fishpage.store import all_items, open_store

FIXTURE = Path(__file__).parent / "fixtures" / "Freshwater_Stocklist_6-19-26.pdf"


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
