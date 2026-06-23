from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from fishpage.app import create_app
from fishpage.models import Item
from fishpage.store import open_store, reconcile

JUN19 = date(2026, 6, 19)

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish Leopard Ctenopoma", Decimal("5.99"), Decimal("4.99"), 30)
SOLD_OUT = Item("110200", "L", "Datnoid Indo", Decimal("89.99"), None, 0)


def client(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF, SOLD_OUT], JUN19)
    return TestClient(create_app(conn))


def test_catalog_defaults_to_in_stock_with_shape(tmp_path):
    resp = client(tmp_path).get("/catalog")

    assert resp.status_code == 200
    items = {item["sku"]: item for item in resp.json()}
    # Default view is In stock only: the qty-0 Datnoid is absent.
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


def test_catalog_includes_out_of_stock_when_toggled(tmp_path):
    resp = client(tmp_path).get("/catalog", params={"include_out_of_stock": "true"})

    items = {item["sku"]: item for item in resp.json()}
    assert set(items) == {"110042", "110092", "110200"}
    assert items["110200"]["qty_avail"] == 0


def test_catalog_reflects_reconciled_state_after_reingest(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    # A second Stocklist: ORNATE_M is cheaper and scarcer, LEAF is gone.
    repriced = Item("110042", "M", "Bichir Ornate", Decimal("24.99"), None, 2)
    reconcile(conn, [repriced], date(2026, 6, 26))

    # The zeroed absentee only surfaces with the out-of-stock view turned on.
    resp = TestClient(create_app(conn)).get("/catalog", params={"include_out_of_stock": "true"})
    items = {item["sku"]: item for item in resp.json()}

    assert items["110042"]["retail_price"] == "24.99"
    assert items["110042"]["qty_avail"] == 2
    assert items["110092"]["qty_avail"] == 0  # absentee zeroed, still served


def test_index_defaults_to_in_stock_cards(tmp_path):
    html = client(tmp_path).get("/").text

    # The qty-0 Datnoid has no card by default; the in-stock Items do.
    assert 'data-sku="110200"' not in html
    assert html.count('data-sku="110042"') == 1
    assert html.count('data-sku="110092"') == 1


def test_index_shows_out_of_stock_cards_when_toggled(tmp_path):
    html = client(tmp_path).get("/", params={"include_out_of_stock": "true"}).text

    assert 'data-sku="110200"' in html


def test_index_has_auto_submitting_stock_toggle_unchecked_by_default(tmp_path):
    html = client(tmp_path).get("/").text

    # A checkbox bound to the query param, auto-submitting its form on change.
    assert 'name="include_out_of_stock"' in html
    assert "this.form.submit()" in html
    # Default view is In stock only, so the control is not checked.
    assert " checked" not in html


def test_index_toggle_is_checked_in_out_of_stock_view(tmp_path):
    html = client(tmp_path).get("/", params={"include_out_of_stock": "true"}).text

    assert " checked" in html


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
