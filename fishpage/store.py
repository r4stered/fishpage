"""Persist Item records in SQLite, keyed by SKU.

Ingestion is reconciliation, not mirroring: :func:`reconcile` upserts each
present SKU and advances its ``last_seen``, zeroes the quantity of any SKU absent from the
current Stocklist, and never deletes a row.
"""

import sqlite3
from datetime import date
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
    qty_avail     INTEGER NOT NULL,
    last_seen     TEXT
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


def reconcile(conn: sqlite3.Connection, items: list[Item], stocklist_date: date) -> None:
    """Reconcile the store against one Stocklist.

    Each ``item`` present in this Stocklist is upserted by SKU and stamped with
    ``stocklist_date`` as its ``last_seen``. No row is ever deleted.
    """
    conn.executemany(
        "INSERT INTO items (sku, size, name, retail_price, special_price, qty_avail, last_seen) "
        "VALUES (:sku, :size, :name, :retail, :special, :qty, :last_seen) "
        "ON CONFLICT(sku) DO UPDATE SET "
        "size = excluded.size, name = excluded.name, retail_price = excluded.retail_price, "
        "special_price = excluded.special_price, qty_avail = excluded.qty_avail, "
        "last_seen = excluded.last_seen",
        [
            {
                "sku": item.sku,
                "size": item.size,
                "name": item.name,
                "retail": str(item.retail_price),
                "special": None if item.special_price is None else str(item.special_price),
                "qty": item.qty_avail,
                "last_seen": stocklist_date.isoformat(),
            }
            for item in items
        ],
    )
    # An absentee is exactly a row the upsert above did NOT just stamp with this run's
    # date, so "absent" is "last_seen is not stocklist_date" — one bound parameter rather
    # than one per present SKU, which keeps us clear of SQLITE_MAX_VARIABLE_NUMBER (999 on
    # SQLite builds before 3.32) no matter how large the Stocklist grows. Absent SKUs are
    # zeroed, never deleted, and keep their last_seen.
    #
    # Tradeoff: this defines "absent" by date, not set membership, so re-running reconcile
    # twice with the *same* stocklist_date will not re-zero the first run's absentees (they
    # already carry that date). A degenerate case — real runs use a distinct date each night.
    conn.execute(
        "UPDATE items SET qty_avail = 0 WHERE last_seen IS NOT ?",
        (stocklist_date.isoformat(),),
    )
    conn.commit()


def all_items(conn: sqlite3.Connection) -> list[Item]:
    rows = conn.execute(
        "SELECT sku, size, name, retail_price, special_price, qty_avail, last_seen FROM items"
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def _row_to_item(row: sqlite3.Row) -> Item:
    special: Decimal | None = (
        None if row["special_price"] is None else Decimal(row["special_price"])
    )
    last_seen: date | None = (
        None if row["last_seen"] is None else date.fromisoformat(row["last_seen"])
    )
    return Item(
        sku=row["sku"],
        size=row["size"],
        name=row["name"],
        retail_price=Decimal(row["retail_price"]),
        special_price=special,
        qty_avail=row["qty_avail"],
        last_seen=last_seen,
    )
