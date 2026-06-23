"""Persist Item records in SQLite, keyed by SKU.

This is the walking-skeleton store: a plain insert into a fresh database. Upsert-by-SKU
reconciliation (advance ``last_seen``, zero out absent SKUs, reuse guard — ADR-0001) is a
later slice.
"""

import sqlite3
from decimal import Decimal
from pathlib import Path

from fishpage.models import Item

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    sku           TEXT PRIMARY KEY,
    size          TEXT NOT NULL,
    name          TEXT NOT NULL,
    retail_price  TEXT NOT NULL,
    special_price TEXT,
    qty_avail     INTEGER NOT NULL
)
"""


def open_store(path: str | Path) -> sqlite3.Connection:
    # check_same_thread=False: the FastAPI handler thread differs from the thread that
    # opened the connection. Our access is serialized (read-mostly), so this is safe.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def save_items(conn: sqlite3.Connection, items: list[Item]) -> None:
    conn.executemany(
        "INSERT INTO items (sku, size, name, retail_price, special_price, qty_avail) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                item.sku,
                item.size,
                item.name,
                str(item.retail_price),
                None if item.special_price is None else str(item.special_price),
                item.qty_avail,
            )
            for item in items
        ],
    )
    conn.commit()


def all_items(conn: sqlite3.Connection) -> list[Item]:
    rows = conn.execute(
        "SELECT sku, size, name, retail_price, special_price, qty_avail FROM items"
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def _row_to_item(row: sqlite3.Row) -> Item:
    special: Decimal | None = (
        None if row["special_price"] is None else Decimal(row["special_price"])
    )
    return Item(
        sku=row["sku"],
        size=row["size"],
        name=row["name"],
        retail_price=Decimal(row["retail_price"]),
        special_price=special,
        qty_avail=row["qty_avail"],
    )
