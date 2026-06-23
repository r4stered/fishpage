"""Reuse guard: flag a SKU reappearing under a materially different name."""

import logging
import sqlite3
from datetime import date
from decimal import Decimal

from fishpage.models import Item
from fishpage.store import all_items, open_store, reconcile

JUN19 = date(2026, 6, 19)
JUN26 = date(2026, 6, 26)
JUL03 = date(2026, 7, 3)

ORNATE_M = Item("110042", "M", "Bichir Ornate", Decimal("28.99"), None, 15)


def test_material_name_change_flags_the_item(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    # Same SKU reappears bound to a different animal entirely.
    reused = Item("110042", "L", "Discus Blue Diamond", Decimal("44.99"), None, 6)
    reconcile(conn, [reused], JUN26)

    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110042"].reuse_flagged is True


def test_normalizable_name_difference_does_not_flag(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    # Same animal, only case / whitespace / punctuation differ.
    restyled = Item("110042", "M", "  bichir,  ORNATE.  ", Decimal("28.99"), None, 15)
    reconcile(conn, [restyled], JUN26)

    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110042"].reuse_flagged is False


def test_material_name_change_is_logged(tmp_path, caplog):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)

    reused = Item("110042", "L", "Discus Blue Diamond", Decimal("44.99"), None, 6)
    with caplog.at_level(logging.WARNING, logger="fishpage.store"):
        reconcile(conn, [reused], JUN26)

    # The SKU and both names appear in the log so a reviewer can act on it.
    assert any(
        "110042" in r.message
        and "Bichir Ornate" in r.message
        and "Discus Blue Diamond" in r.message
        for r in caplog.records
    )


def test_flag_persists_across_reopen(tmp_path):
    db = tmp_path / "fishpage.db"
    conn = open_store(db)
    reconcile(conn, [ORNATE_M], JUN19)
    reused = Item("110042", "L", "Discus Blue Diamond", Decimal("44.99"), None, 6)
    reconcile(conn, [reused], JUN26)
    conn.close()

    # A fresh process reopening the same file still sees the flag.
    reopened = open_store(db)
    stored = {item.sku: item for item in all_items(reopened)}
    assert stored["110042"].reuse_flagged is True


def test_flag_is_sticky_once_raised(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")
    reconcile(conn, [ORNATE_M], JUN19)
    reused = Item("110042", "L", "Discus Blue Diamond", Decimal("44.99"), None, 6)
    reconcile(conn, [reused], JUN26)  # flags the Item; stored name is now Discus

    # A later run carries the settled name — no fresh material change — yet the flag holds.
    reconcile(conn, [reused], JUL03)

    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110042"].reuse_flagged is True


def test_first_sight_of_a_sku_is_never_flagged(tmp_path):
    conn = open_store(tmp_path / "fishpage.db")

    reconcile(conn, [ORNATE_M], JUN19)

    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110042"].reuse_flagged is False


def test_open_store_backfills_the_flag_onto_a_pre_existing_schema(tmp_path):
    db = tmp_path / "fishpage.db"
    # A store created before the reuse guard existed: items table without reuse_flagged.
    legacy = sqlite3.connect(db)
    legacy.execute(
        "CREATE TABLE items (sku TEXT PRIMARY KEY, size TEXT NOT NULL, name TEXT NOT NULL, "
        "retail_price TEXT NOT NULL, special_price TEXT, qty_avail INTEGER NOT NULL, "
        "last_seen TEXT)"
    )
    legacy.execute(
        "INSERT INTO items VALUES ('110042', 'M', 'Bichir Ornate', '28.99', NULL, 15, '2026-06-19')"
    )
    legacy.commit()
    legacy.close()

    # Reopening must add the column (not raise) and existing rows default to not-flagged.
    conn = open_store(db)
    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110042"].reuse_flagged is False

    # And the column is usable for the guard going forward.
    reused = Item("110042", "L", "Discus Blue Diamond", Decimal("44.99"), None, 6)
    reconcile(conn, [reused], JUN26)
    stored = {item.sku: item for item in all_items(conn)}
    assert stored["110042"].reuse_flagged is True
