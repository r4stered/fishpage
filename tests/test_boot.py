from datetime import date
from decimal import Decimal

import pytest

from fishpage.boot import init_observability, restore_database, seed_if_empty
from fishpage.config import DEFAULT_PDF, load_settings
from fishpage.models import Item
from fishpage.store import all_items, latest_stocklist_date, open_store, reconcile

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)


def test_seed_if_empty_loads_the_sample_stocklist_into_an_empty_catalog(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    loaded = seed_if_empty(conn, DEFAULT_PDF)

    assert loaded > 0
    assert len(all_items(conn)) == loaded


def test_seeding_from_an_undated_pdf_falls_back_to_todays_date(tmp_path):
    undated = tmp_path / "stocklist.pdf"
    undated.write_bytes(DEFAULT_PDF.read_bytes())
    conn = open_store(tmp_path / "fishpage.db")

    loaded = seed_if_empty(conn, undated)

    assert loaded > 0
    assert latest_stocklist_date(conn) == date.today()


def test_a_populated_catalog_survives_a_restart_and_is_not_reseeded(tmp_path):
    db_path = tmp_path / "fishpage.db"
    conn = open_store(db_path)
    reconcile(conn, [ORNATE_M], date(2026, 6, 19))
    conn.close()

    # Restart: reopen the same file and run the boot seed.
    conn = open_store(db_path)
    loaded = seed_if_empty(conn, DEFAULT_PDF)

    assert loaded == 0
    stored = all_items(conn)
    assert [item.sku for item in stored] == ["110042"]


def test_restore_is_a_noop_when_no_replica_is_configured(tmp_path):
    settings = load_settings({})

    assert restore_database(settings) is False


def test_restore_refuses_to_run_silently_when_a_replica_is_configured():
    settings = load_settings({"LITESTREAM_REPLICA_URL": "s3://fishpage/db"})

    with pytest.raises(NotImplementedError):
        restore_database(settings)


def test_observability_is_a_noop_when_no_exporter_endpoint_is_configured():
    settings = load_settings({})

    assert init_observability(settings) is False


def test_observability_refuses_to_run_silently_when_an_endpoint_is_configured():
    settings = load_settings({"OTEL_EXPORTER_OTLP_ENDPOINT": "https://otel.example:4317"})

    with pytest.raises(NotImplementedError):
        init_observability(settings)
