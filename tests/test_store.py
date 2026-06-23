from dataclasses import replace
from datetime import date
from decimal import Decimal

from fishpage.models import Item
from fishpage.store import all_items, open_store, reconcile

JUN19 = date(2026, 6, 19)

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish Leopard Ctenopoma", Decimal("5.99"), Decimal("4.99"), 30)


def test_items_round_trip_through_sqlite(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    stored = {item.sku: item for item in all_items(conn)}

    assert stored["110042"] == replace(ORNATE_M, last_seen=JUN19)
    assert stored["110092"] == replace(LEAF, last_seen=JUN19)


def test_store_is_keyed_by_sku(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    stored = all_items(conn)
    assert {item.sku for item in stored} == {"110042", "110092"}
    assert len(stored) == 2
