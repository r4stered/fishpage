from dataclasses import replace
from datetime import date
from decimal import Decimal

from fishpage.models import Item
from fishpage.store import all_items, latest_stocklist_date, open_store, reconcile

JUN19 = date(2026, 6, 19)
JUN26 = date(2026, 6, 26)

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish Leopard Ctenopoma", Decimal("5.99"), Decimal("4.99"), 30)
SOLD_OUT = Item("110200", "L", "Datnoid Indo", Decimal("89.99"), None, 0)


def test_the_store_opens_in_wal_mode_so_litestream_can_replicate(tmp_path):
    # Litestream streams the write-ahead log; a database in the default rollback-journal mode
    # produces nothing for it to replicate. WAL is set on open so every store is replicable.
    conn = open_store(tmp_path / "fishpage.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


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
