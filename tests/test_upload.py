"""The authenticated upload page: the cloud trigger over the same ``ingest_pending`` core.

These tests drive the HTTP routes the way the deployment does — post a Stocklist PDF and read
the catalog back — rather than reaching into the trigger internals. The core's date,
monotonicity, and no-row guards are exercised through ``ingest_pending`` here, not re-tested.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from fishpage.app import create_app
from fishpage.config import DEFAULT_PDF
from fishpage.models import Item
from fishpage.store import all_items, open_store, reconcile

JUN19 = date(2026, 6, 19)
ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
SAMPLE_PDF_BYTES = DEFAULT_PDF.read_bytes()


def _client(tmp_path, *, seed=None, seed_date=JUN19):
    """A TestClient whose upload route writes into tmp incoming/processed dirs.

    When ``seed`` is given the catalog starts reconciled to ``seed_date``; otherwise it is empty,
    so any validly-dated upload is newer than the (absent) latest date.
    """
    conn = open_store(tmp_path / "fishpage.db")
    if seed is not None:
        reconcile(conn, seed, seed_date)
    app = create_app(
        conn,
        incoming_dir=tmp_path / "incoming",
        processed_dir=tmp_path / "processed",
    )
    return conn, TestClient(app)


def test_upload_page_renders_a_file_input(tmp_path):
    _, client = _client(tmp_path)
    html = client.get("/upload").text

    # A multipart form posting a single PDF file field back to the same route.
    assert "<form" in html
    assert 'method="post"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'type="file"' in html
    assert 'name="file"' in html


def _post_pdf(client, name, data=SAMPLE_PDF_BYTES):
    return client.post(
        "/upload",
        files={"file": (name, data, "application/pdf")},
    )


def test_upload_runs_pdf_through_ingest_and_catalog_reconciles(tmp_path):
    conn, client = _client(tmp_path)  # empty catalog

    resp = _post_pdf(client, "Freshwater_Stocklist_6-26-26.pdf")

    assert resp.status_code == 200
    # The uploaded Stocklist was reconciled into the catalog through the shared core.
    skus = {item.sku for item in all_items(conn, include_out_of_stock=True)}
    assert "110042" in skus
    # The drop is audited under processed/, not left in incoming/.
    assert (tmp_path / "processed" / "Freshwater_Stocklist_6-26-26.pdf").is_file()
    assert list((tmp_path / "incoming").glob("*.pdf")) == []
    # The result page confirms what landed.
    assert "Freshwater_Stocklist_6-26-26.pdf" in resp.text


def test_upload_rejects_undated_file_without_touching_catalog(tmp_path):
    conn, client = _client(tmp_path, seed=[ORNATE_M])  # catalog at JUN19

    resp = _post_pdf(client, "stocklist.pdf")  # no M-D-YY token

    # Rejected with a clear, date-focused message — not a 5xx, not a silent success.
    assert resp.status_code == 400
    assert "M-D-YY" in resp.text
    # The catalog is untouched and nothing was written through.
    assert {i.sku for i in all_items(conn, include_out_of_stock=True)} == {"110042"}
    assert list((tmp_path / "incoming").glob("*.pdf")) == []
    assert (
        not (tmp_path / "processed").exists() or list((tmp_path / "processed").glob("*.pdf")) == []
    )


def test_upload_rejects_out_of_range_date(tmp_path):
    _, client = _client(tmp_path)

    resp = _post_pdf(client, "Freshwater_Stocklist_13-40-26.pdf")  # date-shaped but unreal

    assert resp.status_code == 400
    assert "M-D-YY" in resp.text


def test_upload_rejects_stocklist_not_newer_than_catalog(tmp_path):
    conn, client = _client(tmp_path, seed=[ORNATE_M], seed_date=JUN19)

    # Same date as the catalog: the core's monotonicity guard holds it back.
    resp = _post_pdf(client, "Freshwater_Stocklist_6-19-26.pdf")

    assert resp.status_code == 400
    assert "not newer" in resp.text
    # Catalog unchanged, and the held-back drop is not left littering incoming/.
    assert {i.sku for i in all_items(conn, include_out_of_stock=True)} == {"110042"}
    assert list((tmp_path / "incoming").glob("*.pdf")) == []
