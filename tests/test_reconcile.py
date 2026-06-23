"""Ingestion reconciliation: upsert-by-SKU, advance last_seen, zero-out absentees."""

from datetime import date
from decimal import Decimal

from fishpage.models import Item
from fishpage.store import all_items, open_store, reconcile

JUN19 = date(2026, 6, 19)
JUN26 = date(2026, 6, 26)
JUL03 = date(2026, 7, 3)

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)
LEAF = Item("110092", "-", "Leaf Fish Leopard Ctenopoma", Decimal("5.99"), Decimal("4.99"), 30)


def test_new_sku_inserts_and_records_last_seen(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    reconcile(conn, [ORNATE_M], JUN19)

    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110042"].qty_avail == 15
    assert stored["110042"].last_seen == JUN19


def test_existing_sku_updates_price_and_qty_and_advances_last_seen(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    repriced = Item("110042", "M", "Bichir Ornate", Decimal("31.99"), None, 4)
    reconcile(conn, [repriced], JUN26)

    stored = {item.sku: item for item in all_items(conn)}
    assert len(stored) == 1  # upsert, not a second row
    assert stored["110042"].retail_price == Decimal("31.99")
    assert stored["110042"].qty_avail == 4
    assert stored["110042"].last_seen == JUN26


def test_absent_sku_is_zeroed_but_retained(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    # Second Stocklist omits LEAF entirely.
    reconcile(conn, [ORNATE_M], JUN26)

    stored = {item.sku: item for item in all_items(conn)}
    leaf = stored["110092"]
    assert leaf.qty_avail == 0  # zeroed out, not deleted
    assert leaf.last_seen == JUN19  # not seen on JUN26, so last_seen is unchanged
    assert leaf.name == "Leaf Fish Leopard Ctenopoma"  # the rest of the row is retained


def test_reconcile_never_deletes_a_row(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    # A second Stocklist with a completely disjoint SKU set.
    newcomer = Item("110200", "L", "Discus Blue Diamond", Decimal("44.99"), None, 6)
    reconcile(conn, [newcomer], JUN26)

    stored = {item.sku: item for item in all_items(conn)}
    # Every prior SKU survives alongside the newcomer — three rows, none deleted.
    assert set(stored) == {"110042", "110092", "110200"}


def test_last_seen_reflects_most_recent_appearance(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    reconcile(conn, [ORNATE_M], JUN19)  # present
    reconcile(conn, [], JUN26)  # absent — out of stock this week
    reconcile(conn, [ORNATE_M], JUL03)  # back in stock

    stored = {item.sku: item for item in all_items(conn)}
    # last_seen is the latest Stocklist the SKU appeared in (JUL03), not the run it was absent.
    assert stored["110042"].last_seen == JUL03
    assert stored["110042"].qty_avail == 15
