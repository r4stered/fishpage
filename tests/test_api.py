from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from fishpage.app import create_app
from fishpage.models import Item
from fishpage.store import open_store, reconcile

JUN19 = date(2026, 6, 19)

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish Leopard Ctenopoma", Decimal("5.99"), Decimal("4.99"), 30)


def client(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    return TestClient(create_app(conn))


def test_catalog_endpoint_returns_all_items_with_shape(tmp_path):
    resp = client(tmp_path).get("/catalog")

    assert resp.status_code == 200
    items = {item["sku"]: item for item in resp.json()}
    assert set(items) == {"110042", "110092"}

    ornate = items["110042"]
    assert ornate == {
        "sku": "110042",
        "size": "M",
        "name": "Bichir Ornate",
        "retail_price": "28.99",
        "special_price": None,
        "qty_avail": 15,
    }
    assert items["110092"]["special_price"] == "4.99"


def test_catalog_reflects_reconciled_state_after_reingest(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    # A second Stocklist: ORNATE_M is cheaper and scarcer, LEAF is gone.
    repriced = Item("110042", "M", "Bichir Ornate", Decimal("24.99"), None, 2)
    reconcile(conn, [repriced], date(2026, 6, 26))

    resp = TestClient(create_app(conn)).get("/catalog")
    items = {item["sku"]: item for item in resp.json()}

    assert items["110042"]["retail_price"] == "24.99"
    assert items["110042"]["qty_avail"] == 2
    assert items["110092"]["qty_avail"] == 0  # absentee zeroed, still served


def test_index_renders_one_card_per_item(tmp_path):
    resp = client(tmp_path).get("/")
    html = resp.text

    assert resp.status_code == 200
    # One card per Item, tagged by SKU.
    assert html.count('data-sku="110042"') == 1
    assert html.count('data-sku="110092"') == 1

    # Each card shows name, size, retail price, quantity, and a placeholder image.
    # Assert against the tagged spans so a value can't be satisfied by an unrelated
    # number elsewhere in the page.
    assert "Bichir Ornate" in html
    assert "Leaf Fish Leopard Ctenopoma" in html
    assert '<span class="retail-price">$28.99</span>' in html
    assert '<span class="size">M</span>' in html
    assert '<span class="qty">15 available</span>' in html
    assert "placeholder" in html and "<img" in html

    # The special price appears only on the row that has one.
    assert '<span class="special-price">special $4.99</span>' in html
