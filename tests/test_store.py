from decimal import Decimal

from fishpage.models import Item
from fishpage.store import all_items, open_store, save_items

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish Leopard Ctenopoma", Decimal("5.99"), Decimal("4.99"), 30)


def test_items_round_trip_through_sqlite(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    save_items(conn, [ORNATE_M, LEAF])
    stored = {item.sku: item for item in all_items(conn)}

    assert stored["110042"] == ORNATE_M
    assert stored["110092"] == LEAF


def test_store_is_keyed_by_sku(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    save_items(conn, [ORNATE_M, LEAF])

    stored = all_items(conn)
    assert {item.sku for item in stored} == {"110042", "110092"}
    assert len(stored) == 2
