"""Ingestion reconciliation: upsert-by-SKU, advance last_seen, zero-out absentees."""

from datetime import date
from decimal import Decimal

from fishpage.models import Item, PriorSnapshot
from fishpage.store import all_items, open_store, prior_snapshots, reconcile

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


def test_new_sku_records_first_seen(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    reconcile(conn, [ORNATE_M], JUN19)

    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110042"].first_seen == JUN19


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
    # first_seen stays pinned to the first appearance (JUN19) even though the SKU returned — that
    # gap from last_seen is what keeps a returning Item from masquerading as new this week.
    assert stored["110042"].first_seen == JUN19


def _history(conn):
    """Every history row as (sku, stocklist_date, retail, special, qty) tuples, ordered."""
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT sku, stocklist_date, retail_price, special_price, qty "
            "FROM stocklist_history ORDER BY sku, stocklist_date"
        )
    ]


def test_reconcile_appends_a_snapshot_per_sku_per_stocklist(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    reconcile(conn, [ORNATE_M, LEAF], JUN19)

    # One immutable snapshot per SKU carrying the price and quantity that Stocklist printed —
    # str(Decimal) prices, the same form the items row stores.
    assert _history(conn) == [
        ("110042", "2026-06-19", "28.99", None, 15),
        ("110092", "2026-06-19", "5.99", "4.99", 30),
    ]


def test_two_ingests_keep_both_snapshots_immutably(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    # The next Stocklist reprices and reduces the same SKU; the upsert overwrites the items row.
    repriced = Item("110042", "M", "Bichir Ornate", Decimal("31.99"), None, 4)
    reconcile(conn, [repriced], JUN26)

    # Both snapshots stand — the JUN19 row is untouched, not overwritten by the JUN26 upsert, so the
    # week-over-week change the live row destroyed is still on the ledger.
    assert _history(conn) == [
        ("110042", "2026-06-19", "28.99", None, 15),
        ("110042", "2026-06-26", "31.99", None, 4),
    ]


def test_re_running_the_same_stocklist_date_does_not_duplicate_a_snapshot(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    # A second reconcile at the same date (a degenerate re-run) appends nothing new: the
    # (sku, stocklist_date) row already written stands, never updated.
    reconcile(conn, [Item("110042", "M", "Bichir Ornate", Decimal("99.99"), None, 1)], JUN19)

    assert _history(conn) == [("110042", "2026-06-19", "28.99", None, 15)]


def test_prior_snapshots_reads_the_row_before_the_current_stocklist(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)  # qty 15, retail 28.99
    out_of_stock = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 0)
    reconcile(conn, [out_of_stock], JUN26)  # zeroed
    back = Item("110042", "M", "Bichir Ornate", Decimal("33.99"), None, 8)
    reconcile(conn, [back], JUL03)  # back in stock, repriced

    # The "previous Stocklist" for JUL03 is the JUN26 snapshot (qty 0), not the older JUN19 one —
    # the greatest stocklist_date strictly before the current date.
    priors = prior_snapshots(conn, JUL03)
    assert priors == {"110042": PriorSnapshot(Decimal("28.99"), None, 0)}

    # A SKU first seen in the earliest Stocklist has no earlier row and is simply absent.
    assert prior_snapshots(conn, JUN19) == {}


def test_a_sku_dropped_from_the_stocklist_records_a_zero_snapshot(tmp_path):
    # The production out-of-stock path: a SKU vanishes from the Stocklist entirely — it is not
    # listed at qty 0, it is simply gone. The absentee sweep zeroes its items row; reconcile must
    # also write a qty-0 history row, or a later return reads its last in-stock row as the prior
    # snapshot and back-in-stock never fires.
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)  # both present
    reconcile(conn, [LEAF], JUN26)  # ORNATE dropped from the list, not listed at 0

    assert ("110042", "2026-06-26", "28.99", None, 0) in _history(conn)

    reconcile(conn, [ORNATE_M], JUL03)  # back in stock
    assert prior_snapshots(conn, JUL03)["110042"] == PriorSnapshot(Decimal("28.99"), None, 0)


def test_a_sku_absent_for_several_weeks_records_one_zero_snapshot(tmp_path):
    # Only the absence transition (qty_avail > 0 → 0) is snapshotted, so a long-absent SKU does
    # not accrue a fresh zero row every Stocklist.
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M, LEAF], JUN19)
    reconcile(conn, [LEAF], JUN26)  # ORNATE goes absent
    reconcile(conn, [LEAF], JUL03)  # ORNATE still absent

    assert [row for row in _history(conn) if row[0] == "110042"] == [
        ("110042", "2026-06-19", "28.99", None, 15),
        ("110042", "2026-06-26", "28.99", None, 0),
    ]
