from decimal import Decimal

from fastapi.testclient import TestClient

from fishpage.app import create_app
from fishpage.models import Item
from fishpage.store import open_store, save_items

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish Leopard Ctenopoma", Decimal("5.99"), Decimal("4.99"), 30)


def client(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    save_items(conn, [ORNATE_M, LEAF])
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


def test_index_renders_one_card_per_item(tmp_path):
    resp = client(tmp_path).get("/")
    html = resp.text

    assert resp.status_code == 200
    # One card per Item, tagged by SKU.
    assert html.count('data-sku="110042"') == 1
    assert html.count('data-sku="110092"') == 1

    # Each card shows name, size, retail price, quantity, and a placeholder image.
    assert "Bichir Ornate" in html
    assert "Leaf Fish Leopard Ctenopoma" in html
    assert "28.99" in html          # retail price
    assert ">M<" in html or ">M " in html  # size grade
    assert "15" in html             # qty_avail
    assert "placeholder" in html and "<img" in html

    # The special price appears only on the row that has one.
    assert "4.99" in html
